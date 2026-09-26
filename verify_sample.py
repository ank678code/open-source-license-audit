#!/usr/bin/env python3
"""扫描结果独立抽查脚本。

从 scan/ 下各项目的报告里分层随机抽样，重新抓取 PyPI / npm 的原始
许可证元数据，逐条与工具判定结果对照——用于自证扫描结论不是编造的。

只依赖 Python 标准库（不 import license_audit，避免"用工具验证工具"）。
固定随机种子，结果可复现。

比对口径（v0.4.1 起同时输出两个，避免只报一个好看的数字）：

  严口径（strict）——只做机械规范化：大小写、标点归一、去掉 "license" 词、
  `v` 前缀、`+`/or-later 后缀、`2-0`→`2.0`。**不查任何映射表**。
  要求工具判定与源站写法在规范化后字面相同。源站写 "Apache Software License"
  这类无法机械对齐的写法，严口径一律不算一致。

  宽口径（loose）——允许"族 + 版本号"模糊匹配，并允许 ALIAS 表预映射。
  用于回答"工具判定是否有源站依据"，但**说服力弱于严口径**，因为
  ALIAS 表本身是工具的既有知识，用它来比对带有自证成分。

工具判 UNKNOWN 而源站有值的条目，两个口径都不计入一致（
此前这类被 loose_match 直接判为一致，等于把漏判算成正确）。

**抓取失败单列成桶（v0.4.2 补充）**：本机对源站是**间歇可达**的。此前
`upstream_values` 把所有异常一律返回 None，报告里统一写成"源站无此包"——
等于把网络抖动说成源站的问题，而且每次运行的数字都不一样。现在区分并
分开计数：源站无此包（HTTP 404）/ 源站无许可证字段 / 抓取失败（网络，应重跑），
与主程序 `license_audit.py` 的 `NOT_FOUND` / `NO_METADATA` / `FETCH_FAILED`
归因保持同一套语义。抓取失败占比偏高时，脚本会明确提示本次准确率不可用。

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
import urllib.error
import urllib.request

# 本脚本不依赖 license_audit，需自己把 stdout 强制成 UTF-8：
# Windows 控制台默认是 cp1252/gbk，打印中文会抛 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(HERE, "scan")
UA = {"User-Agent": "license-audit-verify/1.0"}

# 抓取重试：源站间歇不可达时，一次失败就下结论会污染抽查结果
FETCH_RETRIES = 3
FETCH_BACKOFF = 0.8

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


def _fetch_json(url):
    """带重试地抓取 JSON，并区分「源站 404」与「网络失败」。

    返回 (status, payload)，status 取值：

      · `"ok"`           —— 成功，payload 为解析后的响应体
      · `"not_found"`    —— HTTP 404，该包在本生态确实不存在
      · `"fetch_failed"` —— 超时 / 连接中断 / 限流 / 非 404 的 HTTP 错误

    为什么必须分开：本机对 pypi.org 是**间歇可达**的（时而通、时而连接中断）。
    若把一次连接失败当成"源站无此包"，抽查报告就会得出错误结论，而且每次
    运行结果都不一样——**不可复现的数字比没有数字更糟**。
    实测证据：同一批抽样里 `tqdm` 两次取到 `MPL-2.0 AND MIT`，
    另三次却报"源站无此包"；而 `Jinja2` / `MarkupSafe` 等 PyPI 上的常见包
    也被报成不存在（curl 单独请求返回的是连接失败 000，不是 404）。

    主程序 `license_audit.py` 早已用 `status` 字段区分 `NOT_FOUND` 与
    `FETCH_ERROR` 并给出不同建议，这里对齐同一套语义。
    """
    last = "未知错误"
    for attempt in range(FETCH_RETRIES):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return "ok", json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "not_found", None
            last = "HTTP %d" % e.code
        except Exception as e:                      # 超时、连接重置、JSON 损坏
            last = type(e).__name__
        if attempt < FETCH_RETRIES - 1:
            time.sleep(FETCH_BACKOFF * (attempt + 1))
    return "fetch_failed", last


def fetch_pypi(name, ver=None):
    """抓 PyPI；返回 (status, payload)，status 含义见 `_fetch_json`。"""
    url = (f"https://pypi.org/pypi/{name}/{ver}/json" if ver
           else f"https://pypi.org/pypi/{name}/json")
    return _fetch_json(url)


def fetch_npm(name, ver=None):
    """抓 npm；返回 (status, payload)，status 含义见 `_fetch_json`。"""
    url = (f"https://registry.npmjs.org/{name}/{ver}" if ver
           else f"https://registry.npmjs.org/{name}")
    return _fetch_json(url)


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


# 未识别在数据里是**字符串** "UNKNOWN"，不是空值。
# 只判 falsy 会漏掉全部未识别记录——实测 spdx 为 None 的有 0 条、
# spdx == "UNKNOWN" 的有 19 条，于是"未识别层"永远抽不到东西，
# 而这一层恰恰是抽查最该审视的：工具说"认不出来"，到底是不是真的认不出来。
def is_unknown(rec):
    s = rec.get("spdx")
    return (not s) or str(s).upper() == "UNKNOWN"


COPYLEFT_KEYS = ("GPL", "MPL", "LGPL", "AGPL", "EPL", "CDDL")


def stratify(rows, n, seed):
    """按「已识别 / 传染性 / 未识别」三层分层随机抽样。

    ok 与 unknown 互斥；copyleft 是 ok 的子集（传染性许可本身属于已识别，
    但独立成层才能保证样本里一定有它）。
    """
    rnd = random.Random(seed)
    unknown = [x for x in rows if is_unknown(x[1])]
    ok = [x for x in rows if not is_unknown(x[1])]
    copyleft = [x for x in ok
                if any(k in (x[1].get("spdx") or "") for k in COPYLEFT_KEYS)]
    for lst in (ok, copyleft, unknown):
        rnd.shuffle(lst)
    third = max(1, n // 3)
    return ok[:third] + copyleft[:third] + unknown[:n - 2 * third]


def upstream_values(rec):
    """独立抓取源站原始值。

    返回 (status, cands)：

      · `("ok", [...])`          —— 抓到了；cands 为空列表表示源站有包但
                                    没填任何许可证字段
      · `("not_found", None)`    —— HTTP 404，源站确实无此包
      · `("fetch_failed", None)` —— 网络失败。**绝不可当作"源站无此包"**：
                                    前者重跑即可，后者要核对包名是否拼错。

    这三个状态在报告里必须分开计数，否则网络抖动会被说成源站的问题。
    """
    name, src, ver = rec["name"], rec.get("source"), rec.get("version")

    if src == "PyPI":
        status, payload = fetch_pypi(name, ver)
        if status != "ok":
            return status, None
        info = (payload or {}).get("info") or {}
        cands = []
        if info.get("license_expression"):
            cands.append(info["license_expression"])
        cands += [c.split("::")[-1].strip()
                  for c in info.get("classifiers", []) if "License" in c]
        if info.get("license"):
            cands.append(info["license"])
        return "ok", cands

    status, d = fetch_npm(name, ver)
    if status != "ok":
        return status, None
    lic = (d or {}).get("license")
    if isinstance(lic, dict):
        lic = lic.get("type")
    out = [lic] if lic else []
    if (d or {}).get("licenses"):
        out.append(json.dumps(d["licenses"], ensure_ascii=False))
    return "ok", out


def canon_strict(v):
    """严口径的机械规范化：不查映射表，只做写法层面的对齐。

    保留版本号（GPL-2 与 GPL-3 不能混），保留 or-later 语义。
    """
    s = str(v or "").strip().lower()
    s = re.sub(r"\(.*?\)", " ", s)              # 去掉括号补充说明
    s = re.sub(r"licen[cs]e", " ", s)           # "license" 这个词不承载信息
    s = s.replace("+", " or-later ")            # GPLv2+ 这类旧式或-later 写法
    s = re.sub(r"\bv(?=\d)", "", s)             # v2 -> 2
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[^a-z0-9.]+", "-", s)          # 其余标点统一成连字符
    s = re.sub(r"-+", "-", s).strip("-.")
    s = re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", s)  # apache-2-0 -> apache-2.0
    return s


def strict_match(tool, cands):
    """严口径：规范化后字面相同才算一致。"""
    if not tool:
        return False
    t = canon_strict(tool)
    if not t:
        return False
    return any(canon_strict(c) == t for c in cands)


def loose_match(tool, cands):
    """宽松比对：任一元数据写法能指向工具判定即算一致。

    比对刻意从宽——目的不是挑工具的错，而是确认「工具判定是否有源站依据」。
    真正的争议条目由人工复核（见 verify_sample.md），脚本只做初筛。

    注意：这里**不含**任何"UNKNOWN 视为一致"的宽容分支。v0.4.1 起主流程
    已把"源站有值但工具未识别"前置分流为漏判桶，工具判 UNKNOWN 却在此处
    返回 True 会把真实漏判算成正确——v0.4.2 删掉该分支（检查清单 P3-3）。
    """
    if not tool:
        return False
    t = str(tool).strip().lower()
    if t.startswith("unknown") or t.endswith("-unknown") or t == "":
        return False

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

    sample = stratify(rows, a.n, a.seed)

    print(f"全库记录 {len(rows)} 条，分层抽样 {len(sample)} 条（种子 {a.seed}）\n")
    # 六个互斥的判定桶，避免把不同性质的结果混进一个"准确率"里
    n_strict = n_loose_only = n_family_only = n_unresolved = n_mismatch = 0
    n_absent = n_no_meta = n_fetch_failed = 0
    for proj, rec in sample:
        tool = rec.get("spdx")
        name = str(rec["name"])[:34]
        status, cands = upstream_values(rec)

        # 网络失败单列成桶：它不是"源站没有数据"，重跑就能拿到。
        # 混进 absent 会让抽查得出错误结论，且每次运行数字都不同。
        if status == "fetch_failed":
            n_fetch_failed += 1
            print(f"⏳ {name:34s} 抓取失败（网络问题，非判定问题）；重跑即可")
            continue

        if status == "not_found":
            n_absent += 1
            no_info = not tool or str(tool).upper().startswith("UNKNOWN")
            mark = "✅" if no_info else "⚠️"
            print(f"{mark} {name:34s} 源站无此包；工具判 {tool}")
            continue

        # 源站有包、但没填任何许可证字段——同属"源站没给数据"，
        # 但与"包不存在"是两回事，计数与建议都不同
        if not cands:
            n_no_meta += 1
            no_info = not tool or str(tool).upper().startswith("UNKNOWN")
            mark = "✅" if no_info else "⚠️"
            print(f"{mark} {name:34s} 源站无许可证字段；工具判 {tool}")
            continue

        t = str(tool or "").strip()
        # 工具判 UNKNOWN / 家族待确认：源站有值却没给出结论，不能算一致
        if (not t) or t.upper() == "UNKNOWN":
            n_unresolved += 1
            print(f"⚠️ {name:34s} 源站有值但工具未识别：{str(cands[0])[:44]}")
            time.sleep(0.15)
            continue
        if t.lower().endswith("-unknown"):
            n_family_only += 1
            print(f"◻ {name:34s} {t:20s} 家族已识别、版本待确认 ← {str(cands[0])[:40]}")
            time.sleep(0.15)
            continue

        if strict_match(t, cands):
            n_strict += 1
            print(f"✅ {name:34s} {t:20s} ← {str(cands[0])[:46]}")
        elif loose_match(t, cands):
            n_loose_only += 1
            print(f"◐ {name:34s} {t:20s} 仅宽口径一致 ← {str(cands[0])[:40]}")
        else:
            n_mismatch += 1
            print(f"❌ {name:34s} {t:20s} vs 源站 {cands}")
        time.sleep(0.15)

    valid = n_strict + n_loose_only + n_family_only + n_unresolved + n_mismatch
    print("\n" + "=" * 64)
    print(f"抽样总数 {len(sample)}　有效可比对 {valid}")
    print(f"未纳入比对：源站无此包 {n_absent}　源站无许可证字段 {n_no_meta}"
          f"　抓取失败 {n_fetch_failed}")
    print("-" * 64)
    print(f"  严口径一致（规范化后字面相同）      {n_strict}")
    print(f"  仅宽口径一致（族+版本/别名匹配）    {n_loose_only}")
    print(f"  家族已识别、版本待确认（*-unknown） {n_family_only}")
    print(f"  源站有值但工具未识别（漏判）        {n_unresolved}")
    print(f"  不一致                              {n_mismatch}")
    print("-" * 64)
    if valid:
        print(f"严口径准确率：{n_strict}/{valid} = {n_strict * 100.0 / valid:.1f}%")
        wide = n_strict + n_loose_only + n_family_only
        print(f"宽口径准确率：{wide}/{valid} = {wide * 100.0 / valid:.1f}%")
        print("（两个口径的定义见本文件头部注释；只报一个数字会掩盖口径差异）")

    # 抓取失败占比偏高时，这份报告的数字不可用——必须写明，否则读者会
    # 以为那些条目是"源站没有数据"，从而高估覆盖率。
    if n_fetch_failed:
        pct = n_fetch_failed * 100.0 / len(sample)
        print("-" * 64)
        print(f"⚠️ 有 {n_fetch_failed} 条（{pct:.0f}%）因网络失败未纳入比对。")
        print("   这些条目**不代表源站没有数据**，只是本次没抓到；")
        print(f"   脚本已自动重试 {FETCH_RETRIES} 次仍失败，说明源站间歇不可达。")
        print("   建议重跑后再引用准确率——含大量抓取失败的结果不可复现。")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
