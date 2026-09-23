#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_projects.py — 批量扫描真实开源项目的依赖许可证，产出评测数据

用 GitHub 连接器拉取各项目的依赖清单 → 本地跑 license_audit → 汇总统计。
产出 scan_summary.json / scan_summary.md，作为技术报告"效果验证"一节的数据来源。

踩过的坑：多数项目的顶层 requirements.txt 只是个指针文件（内容为
"-r requirements/runtime.txt"），直接解析会得到 0 个依赖。因此需要先
解析 -r / --requirement 引用链，并横向比较各候选清单，取依赖数最多的那个。
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))      # ghmcp.py 在工作区根目录

import ghmcp
from license_audit import (parse_requirements, parse_pyproject,
                           parse_package_json)

SCAN = HERE / "scan"
SCAN.mkdir(exist_ok=True)

# (owner, repo, 项目自身许可证)
REPOS = [
    ("open-compass", "opencompass", "Apache-2.0"),
    ("EleutherAI", "lm-evaluation-harness", "MIT"),
    ("vllm-project", "vllm", "Apache-2.0"),
    ("run-llama", "llama_index", "MIT"),
    ("chatchat-space", "Langchain-Chatchat", "Apache-2.0"),
    ("modelscope", "modelscope", "Apache-2.0"),
    ("QwenLM", "Qwen-Agent", "Apache-2.0"),
    ("deepset-ai", "haystack", "Apache-2.0"),
    ("infiniflow", "ragflow", "Apache-2.0"),
    ("FlowiseAI", "Flowise", "Apache-2.0"),
]

CANDIDATES = [
    "requirements.txt",
    "requirements/runtime.txt",
    "requirements/common.txt",
    "requirements/base.txt",
    "requirements/requirements.txt",
    "pyproject.toml",
    "package.json",
]

MAX_PKGS = 70
INCLUDE_RE = re.compile(r"^(?:-r|--requirement)\s+(\S+)", re.I)


def grab(owner, repo, path):
    """取文件内容，失败返回 None。"""
    try:
        r = ghmcp.call_tool("get_file_contents",
                            {"owner": owner, "repo": repo, "path": path})
    except Exception:
        return None
    for c in r.get("result", {}).get("content", []):
        if c.get("type") == "resource":
            return c["resource"].get("text")
    return None


def _join(base, rel):
    rel = rel.strip().lstrip("./")
    if not base or base == ".":
        return rel
    return f"{base.rstrip('/')}/{rel}"


def load_manifest(owner, repo, path, depth=0):
    """拉取并解析一份清单，自动跟随 -r / --requirement 引用。"""
    txt = grab(owner, repo, path)
    if not txt or len(txt.strip()) < 3:
        return []

    # 先看是否是指针文件：内容里出现 -r 引用
    if depth < 3:
        base = str(Path(path).parent).replace("\\", "/")
        merged = []
        for line in txt.splitlines():
            m = INCLUDE_RE.match(line.strip())
            if m:
                merged.extend(load_manifest(owner, repo,
                                            _join(base, m.group(1)), depth + 1))
        if merged:
            return merged

    tmp = SCAN / ("_probe" + Path(path).suffix)
    tmp.write_text(txt, encoding="utf-8")
    try:
        if path.endswith(".toml"):
            return parse_pyproject(tmp)
        if path.endswith(".json"):
            return parse_package_json(tmp)
        return parse_requirements(tmp)
    except Exception:
        return []


def main():
    ghmcp.connect()
    results = []

    for owner, repo, lic in REPOS:
        full = f"{owner}/{repo}"
        print(f"\n=== {full} ===")

        best_path, best_pkgs = None, []
        for cand in CANDIDATES:
            pkgs = load_manifest(owner, repo, cand)
            if len(pkgs) > len(best_pkgs):
                best_path, best_pkgs = cand, pkgs
            if len(best_pkgs) >= 15:          # 已经拿到足够多的依赖，不必再试
                break

        if not best_pkgs:
            print("  未找到可解析的依赖清单，跳过")
            results.append({"project": full, "license": lic, "status": "no_manifest"})
            continue

        pkgs = sorted(set(best_pkgs))[:MAX_PKGS]
        # 关键：npm 依赖必须按 npm 生态去查，否则会拿 npm 包名去 PyPI 查，
        # 命中同名的无关包（实测 Flowise 的 husky 被查成 PyPI 上的另一个 "Husky"）。
        is_npm = best_path.endswith(".json")
        print(f"  依赖清单：{best_path} → {len(pkgs)} 个依赖（{'npm' if is_npm else 'PyPI'} 生态）")

        if is_npm:
            mf = SCAN / f"{owner}__{repo}.package.json"
            mf.write_text(json.dumps({"name": repo, "version": "0.0.0",
                                      "dependencies": {p: "*" for p in pkgs}},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
            flag = "--package-json"
        else:
            mf = SCAN / f"{owner}__{repo}.requirements.txt"
            mf.write_text("\n".join(pkgs), encoding="utf-8")
            flag = "--requirements"

        out = SCAN / f"{owner}__{repo}.report.md"
        cmd = [sys.executable, str(HERE / "license_audit.py"), flag, str(mf),
               "--project-license", lic, "--project-name", full, "--out", str(out)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        jf = Path(str(out).replace(".md", ".json"))
        if p.returncode != 0 or not jf.exists():
            print("  审计失败：", (p.stderr or p.stdout)[:180])
            results.append({"project": full, "license": lic, "status": "audit_failed"})
            continue

        d = json.loads(jf.read_text(encoding="utf-8"))
        recs = d["records"]
        from license_audit import category_of
        resolved = [r for r in recs if r["spdx"] != "UNKNOWN"]
        high_conf = [r for r in recs if r.get("confidence") == "高"]
        cats = Counter(category_of(r["spdx"]) for r in recs)
        hi = [f for f in d["findings"] if f["level"] == "高"]
        cl = [{"name": r["name"], "spdx": r["spdx"]} for r in recs
              if "GPL" in r["spdx"] or "MPL" in r["spdx"]]

        row = {
            "project": full, "license": lic, "status": "ok", "manifest": best_path,
            "ecosystem": "npm" if is_npm else "PyPI",
            "total": len(recs), "resolved": len(resolved),
            "resolve_rate": round(100.0 * len(resolved) / len(recs), 1) if recs else 0.0,
            "high_confidence": len(high_conf),
            "high_conf_rate": round(100.0 * len(high_conf) / len(recs), 1) if recs else 0.0,
            "categories": dict(cats), "findings": len(d["findings"]),
            "findings_high": len(hi), "copyleft_deps": cl,
            "unresolved": [r["name"] for r in recs if r["spdx"] == "UNKNOWN"],
        }
        results.append(row)
        print(f"  识别率 {row['resolve_rate']}%　高可信 {row['high_conf_rate']}%　"
              f"风险 {row['findings']}（高 {row['findings_high']}）")
        if cl:
            print("  传染性依赖：" + ", ".join(f"{c['name']}({c['spdx']})" for c in cl))

    ok = [r for r in results if r.get("status") == "ok"]
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

    summary = {
        "projects_scanned": len(ok), "projects_failed": len(results) - len(ok),
        "total_dependencies": td, "resolved": tr,
        "overall_resolve_rate": round(100.0 * tr / td, 1) if td else 0.0,
        "high_confidence": thc,
        "overall_high_conf_rate": round(100.0 * thc / td, 1) if td else 0.0,
        "findings": tf, "findings_high": tfh,
        "projects_with_copyleft": len(withcl),
        "category_distribution": dict(cats), "per_project": results,
    }
    (HERE / "scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 真实开源项目批量扫描结果", "",
          f"- 扫描项目数：**{len(ok)}** 个",
          f"- 累计依赖数：**{td}**",
          f"- 许可证识别率：**{summary['overall_resolve_rate']}%**（{tr}/{td}）",
          f"- 高可信度判定占比：**{summary['overall_high_conf_rate']}%**（{thc}/{td}）",
          f"- 检出风险项：**{tf}** 条（其中高危 {tfh} 条）",
          f"- 含传染性依赖的项目：**{len(withcl)}/{len(ok)}**", "",
          "| 项目 | 自身许可 | 依赖数 | 识别率 | 高可信 | 风险(高) | 传染性依赖 |",
          "|---|---|---|---|---|---|---|"]
    for r in results:
        if r.get("status") != "ok":
            md.append(f"| {r['project']} | {r['license']} | — | — | — | — | 跳过({r['status']}) |")
            continue
        cls = ", ".join(f"{c['name']}({c['spdx']})" for c in r["copyleft_deps"]) or "—"
        md.append(f"| {r['project']} | {r['license']} | {r['total']} | {r['resolve_rate']}% | "
                  f"{r['high_conf_rate']}% | {r['findings']}({r['findings_high']}) | {cls} |")
    (HERE / "scan_summary.md").write_text("\n".join(md), encoding="utf-8")

    print("\n" + "=" * 64)
    print(f"扫描完成：{len(ok)} 个项目，{td} 个依赖")
    print(f"识别率 {summary['overall_resolve_rate']}%　高可信 {summary['overall_high_conf_rate']}%")
    print(f"风险项 {tf} 条（高危 {tfh}）　含传染性依赖的项目 {len(withcl)}/{len(ok)}")
    print("=" * 64)


if __name__ == "__main__":
    main()
