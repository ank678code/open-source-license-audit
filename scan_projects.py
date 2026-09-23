#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_projects.py — 批量扫描真实开源项目的依赖许可证，产出评测数据

流程：用 GitHub REST API 拉取各项目的依赖清单 → 本地跑 license_audit
     → 汇总统计 → 产出 scan_summary.json / scan_summary.md

v0.3 修正（可复现性）：
  · 此前依赖工作区根目录的 ghmcp.py（自研 GitHub 连接器封装）。该文件既不在
    仓库里也不在提交包里，导致"任何人拿到源码都能复现评测数据"这一承诺无法兑现
    —— 执行本脚本会直接 ModuleNotFoundError。
    → 改为仅用标准库 urllib 直连 GitHub REST API，零第三方依赖，
      与作品"运行时零安装"的定位保持一致。
  · 可选凭据：设置环境变量 GITHUB_TOKEN（或 GH_TOKEN）可提升配额上限；
    未设置时走匿名接口（60 次/小时，够一次完整扫描用）。

踩过的坑：多数项目的顶层 requirements.txt 只是个指针文件（内容为
"-r requirements/runtime.txt"），直接解析会得到 0 个依赖。因此需要先
解析 -r / --requirement 引用链，并横向比较各候选清单，取依赖数最多的那个。

用法：
  python scan_projects.py                       # 扫描全部 10 个候选项目
  python scan_projects.py --jobs 12             # 元数据抓取并发（提速）
  python scan_projects.py --only vllm ragflow   # 只扫指定项目（名字子串匹配）
  python scan_projects.py --out-dir ./scan      # 指定中间产物目录
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from license_audit import (parse_requirements, parse_pyproject,
                           parse_package_json, category_of, VERSION)

SCAN = HERE / "scan"

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

GH_API = "https://api.github.com/repos/{owner}/{repo}/contents/{path}"
GH_UA = {"User-Agent": f"aic-license-audit/{VERSION}",
         "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}

# 传染性许可的类别（用于统计"传染性依赖"）
COPYLEFT_CATS = ("weak-copyleft", "strong-copyleft", "network-copyleft")


class RateLimited(Exception):
    """GitHub API 配额用尽。

    v0.3 修正：以前 403/429 会被 `except Exception: return None` 吞掉，
    最终以"未找到可解析的依赖清单（no_manifest）"的形式进入汇总——
    把「接口限流」误报成「这个项目没有依赖清单」，结论完全错。
    实测匿名接口 60 次/小时，连跑两次完整扫描就会触发。
    """


def _token():
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def gh_get_file(owner, repo, path):
    """用标准库取 GitHub 仓库里某个文件的文本内容，失败返回 None。

    只依赖 urllib，不需要任何第三方库或本地连接器脚本。
    目录、二进制文件、404 一律返回 None；配额用尽抛 RateLimited。
    """
    url = GH_API.format(owner=owner, repo=repo, path=path)
    headers = dict(GH_UA)
    tok = _token()
    if tok:
        headers["Authorization"] = "Bearer " + tok
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            remaining = (e.headers.get("X-RateLimit-Remaining") or "").strip()
            if e.code == 429 or remaining == "0":
                raise RateLimited(
                    f"GitHub API 配额已用尽（HTTP {e.code}，"
                    f"X-RateLimit-Remaining={remaining or '?'}）")
        return None
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("type") != "file":
        return None
    enc = d.get("encoding")
    content = d.get("content")
    if enc == "base64" and content:
        try:
            return base64.b64decode(content).decode("utf-8", "replace")
        except Exception:
            return None
    if d.get("download_url"):
        try:
            req = urllib.request.Request(d["download_url"], headers=GH_UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            return None
    return None


def _join(base, rel):
    rel = rel.strip().lstrip("./")
    if not base or base == ".":
        return rel
    return f"{base.rstrip('/')}/{rel}"


def load_manifest(owner, repo, path, depth=0):
    """拉取并解析一份清单，自动跟随 -r / --requirement 引用。"""
    txt = gh_get_file(owner, repo, path)
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

    SCAN.mkdir(parents=True, exist_ok=True)
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=12,
                    help="license_audit 的元数据抓取并发数（默认 12）")
    ap.add_argument("--only", nargs="*", default=None,
                    help="只扫描名字包含任一关键字的项目")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--offline", action="store_true",
                    help="离线模式：复用 scan/ 下已有的依赖清单快照，完全不访问 GitHub。"
                         "用于接口限流时重算，或在不联网的环境里复现评测数据")
    a = ap.parse_args()

    global SCAN
    if a.out_dir:
        SCAN = Path(a.out_dir)
    SCAN.mkdir(parents=True, exist_ok=True)

    repos = REPOS
    if a.only:
        repos = [r for r in REPOS
                 if any(k.lower() in f"{r[0]}/{r[1]}".lower() for k in a.only)]

    if a.offline:
        print("离线模式：复用 scan/ 下已有的依赖清单快照，不访问 GitHub。")
        print("（首次使用前需先联网跑一次，或用 --manifests 提供快照目录）\n")
    elif not _token():
        print("提示：未检测到 GITHUB_TOKEN，走匿名接口（60 次/小时）。")
        print("      匿名配额只够约一次完整扫描；被限流时可加 --offline 复用快照重算。\n")

    results = []
    rate_limited = 0
    for owner, repo, lic in repos:
        full = f"{owner}/{repo}"
        print(f"\n=== {full} ===")

        best_path, best_pkgs, is_npm = None, [], False
        if a.offline:
            py_snap = SCAN / f"{owner}__{repo}.requirements.txt"
            js_snap = SCAN / f"{owner}__{repo}.package.json"
            if py_snap.exists():
                best_pkgs = parse_requirements(py_snap)
                best_path, is_npm = "snapshot:requirements.txt", False
            elif js_snap.exists():
                best_pkgs = parse_package_json(js_snap)
                best_path, is_npm = "snapshot:package.json", True
            if not best_pkgs:
                print("  离线模式下未找到该项目的依赖清单快照，跳过")
                results.append({"project": full, "license": lic,
                                "status": "no_snapshot"})
                continue
        else:
            try:
                for cand in CANDIDATES:
                    pkgs = load_manifest(owner, repo, cand)
                    if len(pkgs) > len(best_pkgs):
                        best_path, best_pkgs = cand, pkgs
                    if len(best_pkgs) >= 15:   # 已经拿到足够多的依赖，不必再试
                        break
            except RateLimited as e:
                print(f"  ⚠ {e}")
                print("    → 该项目未扫描。这不是「项目没有依赖清单」，而是接口配额问题。")
                results.append({"project": full, "license": lic,
                                "status": "rate_limited"})
                rate_limited += 1
                continue

        if not best_pkgs:
            print("  未找到可解析的依赖清单，跳过")
            results.append({"project": full, "license": lic, "status": "no_manifest"})
            continue

        pkgs = sorted(set(best_pkgs))[:MAX_PKGS]
        # 关键：npm 依赖必须按 npm 生态去查，否则会拿 npm 包名去 PyPI 查，
        # 命中同名的无关包（实测 Flowise 的 husky 被查成 PyPI 上的另一个 "Husky"）。
        if not is_npm:
            is_npm = best_path.endswith(".json")
        print(f"  依赖清单：{best_path} → {len(pkgs)} 个依赖"
              f"（{'npm' if is_npm else 'PyPI'} 生态）")

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
               "--project-license", lic, "--project-name", full,
               "--jobs", str(a.jobs), "--out", str(out)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        jf = Path(str(out).replace(".md", ".json"))
        if p.returncode != 0 or not jf.exists():
            print("  审计失败：", (p.stderr or p.stdout)[:180])
            results.append({"project": full, "license": lic, "status": "audit_failed"})
            continue

        d = json.loads(jf.read_text(encoding="utf-8"))
        recs = d["records"]
        resolved = [r for r in recs if r["spdx"] != "UNKNOWN"]
        high_conf = [r for r in recs if r.get("confidence") == "高"]
        cats = Counter(category_of(r["spdx"]) for r in recs)
        hi = [f for f in d["findings"] if f["level"] == "高"]
        # 传染性依赖按"类别"判定，而不是靠字符串里有没有 GPL——
        # 后者会漏掉 EPL 之类的弱传染许可
        cl = [{"name": r["name"], "spdx": r["spdx"]} for r in recs
              if category_of(r["spdx"]) in COPYLEFT_CATS]

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
        "tool_version": VERSION,
        "projects_scanned": len(ok), "projects_failed": len(results) - len(ok),
        "projects_rate_limited": rate_limited,
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
          f"- 工具版本：**v{VERSION}**",
          f"- 扫描项目数：**{len(ok)}** 个",
          f"- 累计依赖数：**{td}**",
          f"- 许可证识别率：**{summary['overall_resolve_rate']}%**（{tr}/{td}）",
          f"- 高可信度判定占比：**{summary['overall_high_conf_rate']}%**（{thc}/{td}）",
          f"- 检出风险项：**{tf}** 条（其中高危 {tfh} 条）",
          f"- 含传染性依赖的项目：**{len(withcl)}/{len(ok)}**", ""]
    if rate_limited:
        md += [f"> ⚠ 有 {rate_limited} 个项目因 GitHub API 配额用尽未扫描"
               "（不是「项目没有依赖清单」）。设置 `GITHUB_TOKEN` 后重跑，"
               "或加 `--offline` 复用已有依赖清单快照。", ""]
    md += ["| 项目 | 自身许可 | 依赖数 | 识别率 | 高可信 | 风险(高) | 传染性依赖 |",
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
    if rate_limited:
        print(f"⚠ {rate_limited} 个项目因 GitHub 配额用尽未扫描——"
              "设置 GITHUB_TOKEN 后重跑，或加 --offline 复用快照")
    print("=" * 64)


if __name__ == "__main__":
    main()
