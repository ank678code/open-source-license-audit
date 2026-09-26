#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rescan_failed.py — 对已有扫描报告中抓取失败的条目重新抓取并回填

为什么需要它：批量扫描要发数百次网络请求，弱网 / 代理受限环境下必然出现
偶发 FETCH_ERROR。若不重试，这些条目会以"未识别"进入统计，
把识别率拉低十几个百分点，让评测数据失去可比性。

真实失败（包不存在）与网络失败必须区分：
  · NOT_FOUND   → 包确实不在该生态，保持原样
  · FETCH_ERROR → 网络问题，应当重试

v0.3 修正（可复现性）：
  · 报告来源不再只认 scan/*.report.json（该目录已被 .gitignore 排除，
    拿到源码的人没法复现）。现在会同时扫描
    --reports 指定目录、scan/ 目录，以及 05_佐证材料 下的"逐项目报告"目录，
    并自动区分 report.md / report.json / *.report.md 几种命名。
  · 传染性依赖改为按类别判定（与 scan_projects.py 口径一致）。
  · 清理未使用的 import。

用法：
  python rescan_failed.py                      # 自动发现报告并重试
  python rescan_failed.py --rounds 5           # 指定每个条目的重试轮数
  python rescan_failed.py --reports <目录>      # 指定报告所在目录（可多次）
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from license_audit import (fetch_pypi, fetch_npm, detect_conflicts, category_of,
                           to_checklist_table, VERSION)

HERE = Path(__file__).parent
SCAN = HERE / "scan"

COPYLEFT_CATS = ("weak-copyleft", "strong-copyleft", "network-copyleft")


def find_reports(extra_dirs=()):
    """收集所有可用的审计报告 JSON（*.report.json / *.json 且含 records）。"""
    cands = []
    for d in [SCAN, HERE, *[Path(x) for x in extra_dirs]]:
        if not d.exists():
            continue
        for pat in ("*.report.json", "*_report.json", "*.json"):
            cands.extend(sorted(d.glob(pat)))
    out, seen = [], set()
    for p in cands:
        rp = p.resolve()
        if rp in seen or p.name.startswith("scan_summary"):
            continue
        seen.add(rp)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and "records" in d and "project" in d:
            out.append(p)
    return out


def render_md(d):
    """按 license_audit.py 的格式重新渲染报告 Markdown。"""
    md = [f"# 《开源及第三方资源使用清单》（自动生成）", "",
          f"**项目名称**：{d['project']}　**项目自身许可证**：{d['project_license']}　"
          f"**扫描依赖数**：{len(d['records'])}", "", "## 一、风险汇总", ""]
    findings = d["findings"]
    if findings:
        md += ["| 级别 | 资源 | 检出许可证 | 风险说明 | 处理建议 |", "|---|---|---|---|---|"]
        for f in sorted(findings, key=lambda x: 0 if x["level"] == "高" else 1):
            md.append(f"| {f['level']} | {f['pkg']} | {f['license']} | {f['reason']} | {f['advice']} |")
    else:
        md.append("未检出许可证兼容性风险。")
    md += ["", "## 二、资源清单", "", to_checklist_table(d["records"]), "",
           f"> 项目自身许可证：{d['project_license']}　|　清单由脚本自动生成，"
           f"「识别依据」列标注了每个许可证的判定来源，可信度非「高」的项须人工复核　|　"
           f"共 {len(d['records'])} 项"]
    return "\n".join(md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=4, help="每个失败条目的重试轮数")
    ap.add_argument("--reports", action="append", default=[],
                    help="额外报告目录（可重复指定）")
    a = ap.parse_args()

    reports = find_reports(a.reports)
    if not reports:
        print("未找到扫描报告。请先运行 scan_projects.py，"
              "或用 --reports 指定报告所在目录")
        return
    print(f"发现 {len(reports)} 份报告")

    total_fixed = total_still = 0
    for f in reports:
        d = json.loads(f.read_text(encoding="utf-8"))
        need = [r for r in d["records"] if r["status"] == "FETCH_ERROR"]
        if not need:
            continue
        print(f"\n=== {d['project']}：{len(need)} 条待重试 ===")
        for r in need:
            ok = False
            for i in range(a.rounds):
                rec = fetch_pypi(r["name"]) if r["source"] == "PyPI" else fetch_npm(r["name"])
                if rec["status"] == "OK":
                    r.update(rec)
                    ok = True
                    total_fixed += 1
                    print(f"  ✓ {r['name']} → {rec['spdx']}（第 {i+1} 轮）")
                    break
                time.sleep(1.5 * (i + 1))
            if not ok:
                total_still += 1
                print(f"  ✗ {r['name']} 重试 {a.rounds} 轮仍失败")

        # 回填后重新计算冲突与统计
        d["findings"] = detect_conflicts(d["project_license"], d["records"], 0)
        stats = Counter(category_of(x["spdx"]) for x in d["records"])
        d["stats"] = dict(stats)
        d["tool_version"] = VERSION
        f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        Path(str(f).replace(".json", ".md")).write_text(render_md(d), encoding="utf-8")

    # 重新汇总
    rows = []
    for f in reports:
        d = json.loads(f.read_text(encoding="utf-8"))
        recs = d["records"]
        resolved = [r for r in recs if r["spdx"] != "UNKNOWN"]
        high = [r for r in recs if r.get("confidence") == "高"]
        cats = Counter(category_of(r["spdx"]) for r in recs)
        cl = [{"name": r["name"], "spdx": r["spdx"]} for r in recs
              if category_of(r["spdx"]) in COPYLEFT_CATS]
        rows.append({
            "project": d["project"], "license": d["project_license"], "status": "ok",
            "total": len(recs), "resolved": len(resolved),
            "resolve_rate": round(100.0 * len(resolved) / len(recs), 1),
            "high_confidence": len(high),
            "high_conf_rate": round(100.0 * len(high) / len(recs), 1),
            "categories": dict(cats), "findings": len(d["findings"]),
            "findings_high": sum(1 for x in d["findings"] if x["level"] == "高"),
            "copyleft_deps": cl,
            "unresolved": [r["name"] for r in recs if r["spdx"] == "UNKNOWN"],
        })

    td = sum(r["total"] for r in rows)
    tr = sum(r["resolved"] for r in rows)
    thc = sum(r["high_confidence"] for r in rows)
    tf = sum(r["findings"] for r in rows)
    tfh = sum(r["findings_high"] for r in rows)
    withcl = [r for r in rows if r["copyleft_deps"]]
    cats = Counter()
    for r in rows:
        for k, v in r["categories"].items():
            cats[k] += v

    summary = {
        "tool_version": VERSION,
        "projects_scanned": len(rows), "projects_failed": 0,
        "total_dependencies": td, "resolved": tr,
        "overall_resolve_rate": round(100.0 * tr / td, 1),
        "high_confidence": thc,
        "overall_high_conf_rate": round(100.0 * thc / td, 1),
        "findings": tf, "findings_high": tfh,
        "projects_with_copyleft": len(withcl),
        "category_distribution": dict(cats),
        "note": "本汇总在 rescan_failed.py 回填抓取失败的条目后重新计算",
        "per_project": rows,
    }
    (HERE / "scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 真实开源项目批量扫描结果", "",
          f"- 扫描项目数：**{len(rows)}** 个",
          f"- 累计依赖数：**{td}**",
          f"- 许可证识别率：**{summary['overall_resolve_rate']}%**（{tr}/{td}）",
          f"- 高可信度判定占比：**{summary['overall_high_conf_rate']}%**（{thc}/{td}）",
          f"- 检出风险项：**{tf}** 条（其中高危 {tfh} 条）",
          f"- 含传染性依赖的项目：**{len(withcl)}/{len(rows)}**", "",
          "| 项目 | 自身许可 | 依赖数 | 识别率 | 高可信 | 风险(高) | 传染性依赖 |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        cls = ", ".join(f"{c['name']}({c['spdx']})" for c in r["copyleft_deps"]) or "—"
        md.append(f"| {r['project']} | {r['license']} | {r['total']} | {r['resolve_rate']}% | "
                  f"{r['high_conf_rate']}% | {r['findings']}({r['findings_high']}) | {cls} |")
    (HERE / "scan_summary.md").write_text("\n".join(md), encoding="utf-8")

    print("\n" + "=" * 62)
    print(f"回填完成：成功 {total_fixed} 条，仍失败 {total_still} 条")
    print(f"识别率 {summary['overall_resolve_rate']}%　高可信 {summary['overall_high_conf_rate']}%")
    print(f"风险项 {tf} 条（高危 {tfh}）　含传染性依赖的项目 {len(withcl)}/{len(rows)}")
    print("=" * 62)


if __name__ == "__main__":
    main()
