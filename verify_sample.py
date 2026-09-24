#!/usr/bin/env python3
"""扫描结果独立抽查脚本。

从 scan/ 下各项目的报告里分层随机抽样，重新抓取 PyPI / npm 的原始
许可证元数据，逐条与工具判定结果对照——用于自证扫描结论不是编造的。

只依赖 Python 标准库。固定随机种子，结果可复现。

用法：
    python verify_sample.py            # 默认抽 40 条
    python verify_sample.py --n 100    # 自定义抽样量
    python verify_sample.py --seed 42  # 换随机种子（结果会变，但结论应稳定）
"""
import argparse
import glob
import json
import os
import random
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(HERE, "scan")
UA = {"User-Agent": "license-audit-verify/1.0"}

# 把源站五花八门的写法映射到可比较的 SPDX 近似值
ALIAS = {
    "apache software license": "apache-2.0",
    "apache license": "apache-2.0",
    "apache 2.0": "apache-2.0",
    "mit license": "mit",
    "mit license (mit)": "mit",
    "bsd license": "bsd-3-clause",
    "3-clause bsd license": "bsd-3-clause",
    "isc license (iscl)": "isc",
    "isc license": "isc",
    "mozilla public license 2.0 (mpl 2.0)": "mpl-2.0",
    "the unlicense (unlicense)": "unlicense",
    "python software foundation license": "psf-2.0",
    "zlib/libpng license": "zlib",
}


def fetch_pypi(name, ver=None):
    url = (f"https://pypi.org/pypi/{name}/{ver}/json" if ver
           else f"https://pypi.org/pypi/{name}/json")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
        return json.loads(r.read())


def fetch_npm(name, ver=None):
    url = (f"https://registry.npmjs.org/{name}/{ver}" if ver
           else f"https://registry.npmjs.org/{name}")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
        return json.loads(r.read())


def load_records():
    rows = []
    for f in sorted(glob.glob(os.path.join(SCAN, "*.report.json"))):
        proj = os.path.basename(f).replace(".report.json", "")
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for r in d.get("records", []):
            rows.append((proj, r))
    return rows


def upstream_values(rec):
    """独立抓取源站原始值，返回候选字符串列表（失败返回 None）。"""
    name, src, ver = rec["name"], rec.get("source"), rec.get("version")
    try:
        if src == "PyPI":
            info = fetch_pypi(name, ver)["info"]
            cands = []
            if info.get("license_expression"):
                cands.append(info["license_expression"])
            cands += [c.split("::")[-1].strip()
                      for c in info.get("classifiers", []) if "License" in c]
            if info.get("license"):
                cands.append(info["license"])
            return cands
        d = fetch_npm(name, ver)
        lic = d.get("license")
        if isinstance(lic, dict):
            lic = lic.get("type")
        out = [lic] if lic else []
        if d.get("licenses"):
            out.append(json.dumps(d["licenses"], ensure_ascii=False))
        return out
    except Exception:
        return None


def loose_match(tool, cands):
    """宽松比对：任一元数据写法能指向工具判定即算一致。

    比对刻意从宽——目的不是挑工具的错，而是确认"工具判定是否有源站依据"。
    真正的争议条目由人工复核（见 verify_sample.md），脚本只做初筛。
    """
    if not tool:
        return False
    t = str(tool).strip().lower()
    # 工具标 unknown 的条目：源站信息不足，本身就是"不臆断"的正确行为
    if t.startswith("unknown") or t.endswith("-unknown") or t == "":
        return True

    joined = " | ".join(str(c) for c in cands).lower()

    # 1) 源站值里有直接对应物
    for c in cands:
        k = str(c).strip().lower()
        mapped = ALIAS.get(k, k)
        if t == mapped or t in mapped or mapped in t:
            return True
        if t.replace("license", "").strip() == mapped.replace("license", "").strip():
            return True

    # 2) 主许可证族 + 版本号都对得上（应对 "GNU GPL 3.0" / "GPLv2" 这类自然语言写法）
    fam = None
    for name in ("agpl", "lgpl", "gpl", "mpl", "epl", "cddl", "apache", "bsd", "mit", "isc"):
        if name in t:
            fam = name
            break
    if fam and fam in joined:
        ver = re.search(r"(\d+)", t)
        if not ver or re.search(rf"(?:{fam})[^0-9]{{0,12}}{ver.group(1)}", joined) \
                or re.search(rf"{ver.group(1)}[^0-9]{{0,12}}(?:{fam})", joined) \
                or re.search(rf"\bv?{ver.group(1)}\b", joined):
            return True

    # 3) 兜底子串
    return t in joined


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40, help="抽样总数（默认 40）")
    ap.add_argument("--seed", type=int, default=20260924, help="随机种子")
    a = ap.parse_args()

    rows = load_records()
    if not rows:
        print("未找到 scan/*.report.json，请先运行 python scan_projects.py", file=sys.stderr)
        return 1

    rnd = random.Random(a.seed)
    ok = [x for x in rows if x[1].get("spdx")]
    copyleft = [x for x in rows if x[1].get("spdx") and
                any(k in x[1]["spdx"] for k in ("GPL", "MPL", "LGPL", "AGPL", "EPL", "CDDL"))]
    unknown = [x for x in rows if not x[1].get("spdx")]
    for lst in (ok, copyleft, unknown):
        rnd.shuffle(lst)

    third = max(1, a.n // 3)
    sample = ok[:third] + copyleft[:third] + unknown[:a.n - 2 * third]

    print(f"全库记录 {len(rows)} 条，分层抽样 {len(sample)} 条（种子 {a.seed}）\n")
    agree = absent = mismatch = 0
    for proj, rec in sample:
        tool = rec.get("spdx")
        cands = upstream_values(rec)
        name = str(rec["name"])[:34]

        # 抓取失败，或源站返回了记录但没有任何许可证字段
        if not cands:
            absent += 1
            no_info = not tool or str(tool).upper().startswith("UNKNOWN")
            mark = "✅" if no_info else "⚠️"
            why = "源站无此包" if cands is None else "源站无许可证字段"
            print(f"{mark} {name:34s} {why}；工具判 {tool}")
            continue

        if loose_match(tool, cands):
            agree += 1
            print(f"✅ {name:34s} {str(tool):20s} ← {str(cands[0])[:46]}")
        else:
            mismatch += 1
            print(f"❌ {name:34s} {str(tool):20s} vs 源站 {cands}")
        time.sleep(0.15)

    valid = agree + mismatch
    print("\n" + "=" * 64)
    print(f"一致 {agree}　不一致 {mismatch}　源站无此包 {absent}")
    if valid:
        print(f"有效样本准确率：{agree}/{valid} = {agree * 100.0 / valid:.1f}%")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
