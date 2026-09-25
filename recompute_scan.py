#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recompute_scan.py — 用当前判定逻辑离线重算历史扫描数据（不需要网络）

为什么需要它：扫描结果（scan/*.report.json）里存了每条依赖**当时**的判定，
但判定逻辑会演进——知识库扩容、类别体系调整、冲突判定修正。想让旧数据与
新逻辑对齐，要么重跑一遍扫描（要联网、要几十分钟、源站元数据还可能已经变了），
要么**复用已落盘的上游原始值离线重算**。后者是本脚本的做法，也是
FIXES.md 第十二节所说的"重算方式"：

  · 每条记录里存了上游返回的原始许可证值（`license_raw`），
    用当前的 normalize_license 重新归一化，即可得到新的 spdx 与类别；
  · 再用当前的 detect_conflicts 重新判定风险项；
  · 报告渲染直接调用 render_report()，产出与"重跑一遍工具"完全一致的报告；
  · 汇总按 scan_projects.py 的同口径重算（含未识别项归因与双口径识别率）。

已知局限：`license_field` 的"四级可信度链"不会重跑——链的选择本身依赖
normalize_license 的结果，而当时被跳过的上游字段没有落盘。因此本重算对
"源站给了明确许可证、但旧知识库不认"这一类情形可能**低估**改善幅度，
得到的是一组保守数字。`license_raw` 里若仍包含可识别片段（例如完整许可证
正文里的 "Zope Public License"），则能被重新识别。

用法：
  python recompute_scan.py                       # 数据目录 = 本脚本所在目录
  python recompute_scan.py --data <目录>          # 指定含 scan/ 的数据目录
  python recompute_scan.py --data <目录> --code <目录>   # 代码与数据分开放时
  python recompute_scan.py --dry-run             # 只打印差异，不写文件
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_ap = argparse.ArgumentParser(description="离线重算历史扫描数据")
_ap.add_argument("--data", default=str(Path(__file__).parent),
                 help="存放 scan/ 与 scan_summary.* 的数据目录（默认本脚本所在目录）")
_ap.add_argument("--code", default=None,
                 help="从哪个目录 import license_audit（默认与 --data 相同）")
_ap.add_argument("--dry-run", action="store_true",
                 help="只打印重算差异，不写回任何文件")
_A = _ap.parse_args()

SRC = Path(_A.data)
CODE = Path(_A.code) if _A.code else SRC

sys.path.insert(0, str(CODE))
import license_audit as la  # noqa: E402

TOOL_VERSION = la.VERSION
SUMMARY = SRC / "scan_summary.json"
SCAN = SRC / "scan"


def recompute_project(report_path):
    """重算单个项目，返回 (新报告字典, 汇总行, 判定变化列表)。"""
    old = json.loads(report_path.read_text(encoding="utf-8"))
    records, changed = [], []
    for r in old["records"]:
        r = dict(r)
        before = r.get("spdx")
        raw = r.get("license_raw") or ""
        r["spdx"] = la.normalize_license(raw)
        r["unknown_kind"] = la.classify_unknown(
            {"spdx": r["spdx"], "status": r.get("status"), "license_raw": raw})
        if before != r["spdx"]:
            changed.append((r["name"], before, r["spdx"], r.get("confidence")))
        records.append(r)

    findings = la.detect_conflicts(old["project_license"], records)
    locked = sum(1 for r in records if r.get("requested_version"))
    md_text, report = la.render_report(old["project"], old["project_license"],
                                       records, findings, locked)

    if not _A.dry_run:
        report_path.with_suffix(".md").write_text(md_text, encoding="utf-8")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                               encoding="utf-8")

    resolved = [r for r in records if r["spdx"] != "UNKNOWN"]
    high_conf = [r for r in records if r.get("confidence") == "高"]
    cats = Counter(la.category_of(r["spdx"]) for r in records)
    hi = [f for f in findings if f["level"] == "高"]
    cl = [{"name": r["name"], "spdx": r["spdx"]} for r in records
          if la.category_of(r["spdx"]) in ("weak-copyleft", "strong-copyleft",
                                           "network-copyleft")]
    total = len(records)
    row = {
        "total": total, "resolved": len(resolved),
        "resolve_rate": round(100.0 * len(resolved) / total, 1) if total else 0.0,
        "high_confidence": len(high_conf),
        "high_conf_rate": round(100.0 * len(high_conf) / total, 1) if total else 0.0,
        "categories": dict(cats), "findings": len(findings),
        "findings_high": len(hi), "copyleft_deps": cl,
        "unresolved": [r["name"] for r in records if r["spdx"] == "UNKNOWN"],
        "unknown_breakdown": la.unknown_breakdown(records),
        "effective_resolve_rate": la.effective_resolve_rate(records),
    }
    return report, row, changed


def main():
    if not SUMMARY.exists():
        print(f"找不到 {SUMMARY}：请用 --data 指定含 scan_summary.json 与 scan/ 的目录",
              file=sys.stderr)
        return 1
    reports = sorted(SCAN.glob("*.report.json"))
    if not reports:
        print(f"找不到项目报告（{SCAN}/*.report.json）：请先运行 python scan_projects.py",
              file=sys.stderr)
        return 1

    old_summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    # manifest / ecosystem / workspace_excluded 与判定逻辑无关，沿用历史值
    meta = {p["project"]: p for p in old_summary["per_project"]}
    print(f"发现 {len(reports)} 份项目报告，工具版本 v{TOOL_VERSION}")

    results, all_changed = [], []
    for rp in reports:
        report, row, changed = recompute_project(rp)
        m = meta.get(report["project"], {})
        row = {"project": report["project"], "license": report["project_license"],
               "status": "ok", "manifest": m.get("manifest", "?"),
               "ecosystem": m.get("ecosystem", "PyPI"), **row,
               "workspace_excluded": m.get("workspace_excluded", 0)}
        results.append(row)
        if changed:
            all_changed.append((report["project"], changed))
            print("  %-40s %d 项判定变化" % (report["project"], len(changed)))

    # 按历史汇总中的顺序排列，保证与既有材料表格可比
    order = [p["project"] for p in old_summary["per_project"]]
    results.sort(key=lambda r: order.index(r["project"]) if r["project"] in order else 999)

    ok = [r for r in results if r["status"] == "ok"]
    td = sum(r["total"] for r in ok)
    tr = sum(r["resolved"] for r in ok)
    thc = sum(r["high_confidence"] for r in ok)
    tf = sum(r["findings"] for r in ok)
    tfh = sum(r["findings_high"] for r in ok)
    withcl = [r for r in ok if r["copyleft_deps"]]
    cats = Counter()
    for r in ok:
        for k, v in r["categories"].items():
            cats[k] += v
    ukb = Counter()
    for r in ok:
        for k, v in r["unknown_breakdown"].items():
            ukb[k] += v
    ukb = {k: ukb.get(k, 0) for k in la.UNKNOWN_KINDS}
    tool_fault = ukb.get("UNSUPPORTED_LICENSE", 0)

    summary = {
        "tool_version": TOOL_VERSION,
        "projects_scanned": len(ok), "projects_failed": len(results) - len(ok),
        "projects_rate_limited": old_summary.get("projects_rate_limited", 0),
        "total_dependencies": td, "resolved": tr,
        "overall_resolve_rate": round(100.0 * tr / td, 1) if td else 0.0,
        "high_confidence": thc,
        "overall_high_conf_rate": round(100.0 * thc / td, 1) if td else 0.0,
        "findings": tf, "findings_high": tfh,
        "projects_with_copyleft": len(withcl),
        "workspace_excluded": sum(r["workspace_excluded"] for r in ok),
        "category_distribution": dict(cats),
        "unknown_breakdown": ukb,
        "unknown_tool_fault": tool_fault,
        "effective_resolve_rate": round(
            100.0 * (td - tool_fault - ukb.get("FETCH_FAILED", 0)) / td, 1) if td else 0.0,
        "note": "本汇总由 recompute_scan.py 用当前判定逻辑离线重算历史数据得到",
        "per_project": results,
    }

    # ---- 与重算前对比 ----
    print("\n" + "=" * 62)
    print("指标                     重算前      重算后      变化")
    rows = [
        ("扫描项目数", old_summary["projects_scanned"], summary["projects_scanned"]),
        ("累计依赖数", old_summary["total_dependencies"], summary["total_dependencies"]),
        ("识别数", old_summary["resolved"], summary["resolved"]),
        ("识别率", old_summary["overall_resolve_rate"], summary["overall_resolve_rate"]),
        ("高可信数", old_summary["high_confidence"], summary["high_confidence"]),
        ("高可信率", old_summary["overall_high_conf_rate"], summary["overall_high_conf_rate"]),
        ("风险项", old_summary["findings"], summary["findings"]),
        ("高危", old_summary["findings_high"], summary["findings_high"]),
        ("含传染性依赖项目", old_summary["projects_with_copyleft"],
         summary["projects_with_copyleft"]),
    ]
    for name, a, b in rows:
        delta = round(b - a, 1) if isinstance(a, float) else b - a
        print("%-22s %-11s %-11s %s" % (name, a, b, delta))
    print("\n未识别归因:", {la.UNKNOWN_KIND_CN.get(k, k): v for k, v in ukb.items() if v})
    print("剔除源站无数据后识别率:", summary["effective_resolve_rate"], "%")

    if all_changed:
        print("\n判定发生变化的条目（共 %d 个项目）:" % len(all_changed))
        for proj, ch in all_changed:
            for name, before, after, conf in ch:
                print("  %-34s %-22s %-20s -> %-22s [%s]" %
                      (proj, name, before, after, conf))

    # 知识库未收录的条目——唯一该由工具改进的一类，单独列出便于逐条核对
    unsupported = []
    for rp in reports:
        d = json.loads(rp.read_text(encoding="utf-8"))
        for rec in d["records"]:
            if la.classify_unknown(rec) == "UNSUPPORTED_LICENSE":
                unsupported.append((d["project"], rec["name"], rec.get("license_raw")))
    if unsupported:
        print("\n知识库未收录的条目（应由工具改进）:")
        for proj, name, raw in unsupported:
            print("  %-30s %-22s 源站值: %s" % (proj, name, (raw or "")[:60]))

    if _A.dry_run:
        print("\n[dry-run] 未写回任何文件")
        return summary

    # ---- 汇总 Markdown（与 scan_projects.py 的输出格式一致）----
    md = ["# 真实开源项目批量扫描结果", "",
          f"- 工具版本：**v{TOOL_VERSION}**",
          f"- 扫描项目数：**{summary['projects_scanned']}** 个",
          f"- 累计依赖数：**{td}**",
          f"- 许可证识别率：**{summary['overall_resolve_rate']}%**（{tr}/{td}）",
          f"- 高可信度判定占比：**{summary['overall_high_conf_rate']}%**（{thc}/{td}）",
          f"- 检出风险项：**{tf}** 条（其中高危 {tfh} 条）",
          f"- 含传染性依赖的项目：**{len(withcl)}/{len(ok)}**"]
    if summary["workspace_excluded"]:
        md.append(f"- 排除的 monorepo 工作区内部包：**{summary['workspace_excluded']}** 个"
                  "（`workspace:` 标记，不发布到 registry，不参与审计）")
    if sum(ukb.values()):
        md += ["", "### 未识别项归因（v0.4）", "",
               f"- 未识别合计：**{sum(ukb.values())}** 条",
               f"- 源站无此包：**{ukb.get('NOT_IN_REGISTRY', 0)}** 条（工具无能为力）",
               f"- 源站未填许可证：**{ukb.get('NO_METADATA', 0)}** 条（须人工核对上游仓库）",
               f"- 知识库未收录该写法：**{tool_fault}** 条（**应由工具改进**，补进 LICENSE_DB 即可降低）",
               f"- 网络获取失败：**{ukb.get('FETCH_FAILED', 0)}** 条（重跑即可）",
               "",
               f"> 原始识别率 {summary['overall_resolve_rate']}% 把上述四种性质混在一起统计；"
               f"剔除「源站客观无数据」后为 **{summary['effective_resolve_rate']}%**。"
               "两者并列展示，才能让识别率这个数字站得住。"]
    md += ["", "| 项目 | 自身许可 | 依赖数 | 识别率 | 高可信 | 风险(高) | 传染性依赖 |",
           "|---|---|---|---|---|---|---|"]
    for r in results:
        cls = ", ".join(f"{c['name']}({c['spdx']})" for c in r["copyleft_deps"]) or "—"
        md.append(f"| {r['project']} | {r['license']} | {r['total']} | {r['resolve_rate']}% | "
                  f"{r['high_conf_rate']}% | {r['findings']}({r['findings_high']}) | {cls} |")
    (SRC / "scan_summary.md").write_text("\n".join(md), encoding="utf-8")
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    print(f"\n已写出：{SUMMARY} 与 scan_summary.md")
    return summary


if __name__ == "__main__":
    main()
