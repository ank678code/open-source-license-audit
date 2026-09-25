#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_docs_consistency.py — 文档 / 配置里的声明必须与代码实际状态逐项相符

为什么需要它：「文档落后于代码」这一类问题已经连续两轮被第三方检查指出——
上一轮的 H1（交付数据滞后）、L1（版本号三处不一致）、L2（文档用例数过时），
以及本轮的 P2-1（README 仍写 196 / 283）。根因是**数字硬编码在文档里**，
每加一组用例、每发一次版本就会漂移；靠人工每轮核对必然漏。

因此把它做成闸门，由 CI 拦住。检查三类：

  1. 测试用例数 —— 实际跑一遍两个测试文件，文档里声明的数必须落在
     {test_cases 数, test_semantic 数, 两者之和} 里；
  2. 扫描数据   —— README 声明的项目数 / 依赖数 / 识别率 / 高可信 / 风险项
     / 传染性项目 / 工作区内部包数，必须与 scan_summary.json 一致；
  3. 版本号     —— VERSION == pyproject.toml == CHANGELOG 首个版本段
     == scan_summary.json 的 tool_version。

**宽松匹配**：只在文档里**找到**该声明时才比对数值；若文档被改写掉某条声明，
不报错（不强迫文档必须怎么写），只在该声明存在且数值不符时报错。

用法：
  python check_docs_consistency.py            # 检查，失败时退出码 1
  python check_docs_consistency.py --no-tests # 跳过实际跑测试（只查文档与数据）
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PY = sys.executable

ERRORS = []
NOTES = []


def err(msg):
    ERRORS.append(msg)


def run_tests(name):
    """跑一个测试文件，返回 (用例总数, 通过数)。"""
    r = subprocess.run([PY, str(HERE / name)], cwd=str(HERE),
                       capture_output=True, text=True, encoding="utf-8")
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"用例总数\s*(\d+)\s*　通过\s*(\d+)", out)
    if not m:
        err(f"{name} 未输出可解析的用例统计（退出码 {r.returncode}）")
        return None, None
    total, passed = int(m.group(1)), int(m.group(2))
    if total != passed:
        err(f"{name} 有失败用例：通过 {passed} / 共 {total}")
    return total, passed


def check_case_counts(docs, n_cases, n_sem):
    allowed = {n_cases, n_sem, n_cases + n_sem}
    for doc in docs:
        text = (HERE / doc).read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"(\d+)\s*个用例", text):
            got = int(m.group(1))
            if got not in allowed:
                line = text[:m.start()].count("\n") + 1
                err(f"{doc}:{line} 声明「{got} 个用例」，实际为 "
                    f"{n_cases} / {n_sem} / 合计 {n_cases + n_sem}")
            else:
                NOTES.append(f"{doc} 「{got} 个用例」✓")


# (正则, scan_summary.json 中的字段, 人类可读名)
SCAN_PATTERNS = [
    (r"整体识别率\s*\**\s*([\d.]+)\s*%", "overall_resolve_rate", "整体识别率"),
    (r"高可信度判定占比\s*\**\s*([\d.]+)\s*%", "overall_high_conf_rate", "高可信占比"),
    (r"检出风险项\s*\**\s*(\d+)\s*\**\s*条", "findings", "检出风险项"),
    (r"检出风险项\s*\**\s*\d+\s*\**\s*条（高危\s*(\d+)\s*条）", "findings_high", "高危条数"),
    (r"\*\*\s*(\d+)\s*个真实开源项目\s*\*\*", "projects_scanned", "扫描项目数"),
    (r"累计\s*\**\s*(\d+)\s*个依赖", "total_dependencies", "累计依赖数"),
    (r"(\d+)\s*个项目里\s*(\d+)\s*个含传染性依赖", None, "含传染性项目"),
    (r"累计排除\s*\**\s*(\d+)\s*个\s*\**\s*工作区内部包", "workspace_excluded", "工作区内部包"),
    (r"剔除「源站客观无数据」后为\s*\**\s*([\d.]+)\s*%", "effective_resolve_rate", "有效识别率"),
]


def check_scan_summary(doc="README.md"):
    sp = HERE / "scan_summary.json"
    if not sp.exists():
        err("scan_summary.json 不存在，无法核对扫描数据声明")
        return
    s = json.loads(sp.read_text(encoding="utf-8"))
    text = (HERE / doc).read_text(encoding="utf-8", errors="replace")

    for pat, field, label in SCAN_PATTERNS:
        for m in re.finditer(pat, text):
            line = text[:m.start()].count("\n") + 1
            if field is None and label == "含传染性项目":
                want_proj, want_cl = s["projects_scanned"], s["projects_with_copyleft"]
                got = (int(m.group(1)), int(m.group(2)))
                if got != (want_proj, want_cl):
                    err(f"{doc}:{line} 声明「{got[0]} 个项目里 {got[1]} 个含传染性依赖」，"
                        f"实际为 {want_proj} / {want_cl}")
                else:
                    NOTES.append(f"{doc} 含传染性项目 ✓")
                continue
            want = s[field]
            got_raw = m.group(1)
            got = float(got_raw) if "." in got_raw else int(got_raw)
            if abs(float(got) - float(want)) > 1e-9:
                err(f"{doc}:{line} 声明「{label} = {got}」，scan_summary.json 为 {want}")
            else:
                NOTES.append(f"{doc} {label} ✓")


def check_versions():
    ver_file = re.search(r'^VERSION\s*=\s*"([^"]+)"',
                         (HERE / "license_audit.py").read_text(encoding="utf-8"), re.M)
    ver_code = ver_file.group(1) if ver_file else None
    pp = (HERE / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pp, re.M)
    ver_pp = m.group(1) if m else None
    ch = (HERE / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^##\s*\[([\d.]+)\]", ch, re.M)
    ver_ch = m.group(1) if m else None
    sp = HERE / "scan_summary.json"
    ver_scan = json.loads(sp.read_text(encoding="utf-8"))["tool_version"] if sp.exists() else None

    seen = {"license_audit.py VERSION": ver_code, "pyproject.toml": ver_pp,
            "CHANGELOG 首段": ver_ch, "scan_summary.json": ver_scan}
    uniq = set(seen.values())
    if len(uniq) != 1 or None in uniq:
        err("版本号不一致：" + "，".join(f"{k}={v}" for k, v in seen.items()))
    else:
        NOTES.append(f"版本号一致：{ver_code} ✓")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tests", action="store_true", help="跳过实际跑测试")
    a = ap.parse_args()

    docs = ["README.md", "CONTRIBUTING.md"]
    if a.no_tests:
        NOTES.append("已跳过测试运行（--no-tests）")
    else:
        n_cases, _ = run_tests("test_cases.py")
        n_sem, _ = run_tests("test_semantic.py")
        if n_cases and n_sem:
            check_case_counts(docs, n_cases, n_sem)

    check_scan_summary()
    check_versions()

    print(f"一致性闸门：核对 {len(NOTES)} 项")
    for n in NOTES:
        print(f"  ✓ {n}")
    if ERRORS:
        print(f"\n发现 {len(ERRORS)} 处文档与代码不一致：")
        for e in ERRORS:
            print(f"  ✗ {e}")
        print("\n提示：文档里的数字应与实际状态保持一致；"
              "改完代码/加完用例后同步更新文档，或删掉该处的数字声明。")
        return 1
    print("\n全部一致 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
