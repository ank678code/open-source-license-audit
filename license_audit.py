#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
license_audit.py — 开源许可证合规检查 + 《开源及第三方资源使用清单》生成器
选题1 技术可行性验证原型 v0.2

功能链路：
  依赖清单解析 → PyPI/npm 元数据抓取 → 许可证归一化(SPDX) → 类别判定
  → 兼容性冲突检测(含传递依赖抽查) → 输出竞赛要求的清单表格

v0.2 修正（均为实测中发现的真实问题）：
  · pandas 的 license 字段是 61KB 全文，关键词全文匹配会误判为 GPL
    → 改为四级可信度解析链：license_expression > trove classifier > license 首行 > 截断兜底
  · numpy 的 license_expression 是复合表达式 "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0"
    → 增加表达式解析，按 AND/OR/WITH 拆分后逐个归一化，类别取最严格者
  · mysqlclient 是 "GPL-2.0-or-later"，此前被误归一为 GPL-2.0-only
    → 增加 -or-later / + 后缀处理，该区别直接影响兼容性判断
  · PyQt5 写 "GPL v3"、chardet 写 "0BSD" 此前均无法识别 → 补充正则

用法：
  python license_audit.py --requirements req.txt --project-license MIT
  python license_audit.py --packages requests pymupdf --project-license Apache-2.0 --transitive 40
"""

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

PYPI = "https://pypi.org/pypi/{name}/json"
NPM = "https://registry.npmjs.org/{name}/latest"
UA = {"User-Agent": "aic-license-audit/0.2 (feasibility prototype)"}

# ---------------------------------------------------------------- 许可证知识库

# 关键词 -> SPDX 标识。顺序敏感：具体在前，宽泛在后。
# 注意：这些模式只用于匹配「单个许可证标识」，不用于匹配整篇许可证正文。
SPDX_PATTERNS = [
    (r"^AGPL[- ]?v?[- ]?3", "AGPL-3.0-only"),
    (r"GNU Affero", "AGPL-3.0-only"),
    (r"^LGPL[- ]?v?[- ]?3", "LGPL-3.0-only"),
    (r"^LGPL[- ]?v?[- ]?2\.1", "LGPL-2.1-only"),
    (r"^LGPL[- ]?v?[- ]?2\b", "LGPL-2.0-only"),
    (r"^GPL[- ]?v?[- ]?3", "GPL-3.0-only"),
    (r"^GPL[- ]?v?[- ]?2", "GPL-2.0-only"),
    (r"GNU (Library or Lesser|Lesser) General Public", "LGPL-3.0-only"),
    (r"GNU Library General Public", "LGPL-2.0-only"),
    (r"GNU General Public", "GPL-3.0-only"),
    (r"^Apache[- ]?(Software )?License", "Apache-2.0"),
    (r"^Apache[- ]?2", "Apache-2.0"),
    (r"^MPL[- ]?v?[- ]?2", "MPL-2.0"),
    (r"Mozilla Public", "MPL-2.0"),
    (r"^EPL[- ]?v?[- ]?2", "EPL-2.0"),
    (r"Eclipse Public", "EPL-2.0"),
    (r"^0BSD$", "0BSD"),
    (r"^BSD[- ]?3", "BSD-3-Clause"),
    (r"^BSD[- ]?2", "BSD-2-Clause"),
    (r"^BSD[- ]?(License|$)", "BSD-3-Clause"),
    # 词序颠倒的写法：protobuf 的 license 字段写 "3-Clause BSD License"
    (r"^3[- ]?Clause BSD", "BSD-3-Clause"),
    (r"^2[- ]?Clause BSD", "BSD-2-Clause"),
    # MIT-CMU（Pillow 等使用）必须在 MIT 之前匹配，否则会被通用 MIT 模式吃掉
    (r"^MIT[- ]CMU", "MIT-CMU"),
    (r"^MIT$|^MIT ", "MIT"),
    (r"^ISC$|^ISC ", "ISC"),
    (r"Python Software Foundation|^PSF", "PSF-2.0"),
    (r"^Unlicense", "Unlicense"),
    (r"^CC0", "CC0-1.0"),
    (r"Public Domain|Historical Permission", "HPND"),
    (r"^BlueOak", "BlueOak-1.0.0"),          # npm 生态常见，rimraf 等包在用
    (r"^zlib", "Zlib"),
    # 兜底：只写了家族名、没写版本号。类别可判定，但版本须人工确认，
    # 单列 -unknown 标识以免给出错误版本的确定性结论。
    (r"\bLGPL\b|Lesser General Public", "LGPL-unknown"),
    (r"\bAGPL\b|Affero", "AGPL-unknown"),
    (r"\bGPL\b", "GPL-unknown"),
]

# SPDX 标识 -> (类别, 关键许可义务摘要)
LICENSE_DB = {
    "MIT":            ("permissive",        "保留版权与许可声明"),
    "BSD-2-Clause":   ("permissive",        "保留版权与许可声明"),
    "BSD-3-Clause":   ("permissive",        "保留版权与许可声明，不得用作者名义背书"),
    "Apache-2.0":     ("permissive",        "保留 NOTICE 与变更说明，含专利授权条款"),
    "ISC":            ("permissive",        "保留版权与许可声明"),
    "PSF-2.0":        ("permissive",        "保留版权与许可声明"),
    "Unlicense":      ("permissive",        "无附加义务（放弃著作权）"),
    "CC0-1.0":        ("permissive",        "无附加义务（放弃著作权）"),
    "Zlib":           ("permissive",        "保留版权与许可声明，不得用作者名义背书"),
    "0BSD":           ("permissive",        "无附加义务（放弃署名要求）"),
    "HPND":           ("permissive",        "保留版权与许可声明"),
    "MIT-CMU":        ("permissive",        "保留版权与许可声明（CMU 变体，与 HPND 同源）"),
    "BlueOak-1.0.0":  ("permissive",        "保留版权与许可声明，含明确的专利授权"),
    "MPL-2.0":        ("weak-copyleft",     "MPL 覆盖的文件需以 MPL 开放源码"),
    "EPL-2.0":        ("weak-copyleft",     "EPL 覆盖的模块需以 EPL 开放源码"),
    "LGPL-2.0-only":  ("weak-copyleft",     "动态链接可用；修改库本体需以 LGPL 开放"),
    "LGPL-2.1-only":  ("weak-copyleft",     "动态链接可用；修改库本体需以 LGPL 开放"),
    "LGPL-3.0-only":  ("weak-copyleft",     "动态链接可用；修改库本体需以 LGPL 开放"),
    "GPL-2.0-only":   ("strong-copyleft",   "衍生作品整体需以 GPL-2.0 开放源码"),
    "GPL-3.0-only":   ("strong-copyleft",   "衍生作品整体需以 GPL-3.0 开放源码，含专利与反 DRM 条款"),
    "AGPL-3.0-only":  ("network-copyleft",  "网络服务对外提供即触发源码开放义务"),
    "GPL-unknown":    ("strong-copyleft",   "衍生作品需以 GPL 开放源码（具体版本待人工确认）"),
    "LGPL-unknown":   ("weak-copyleft",     "动态链接可用；修改库本体需以 LGPL 开放（具体版本待人工确认）"),
    "AGPL-unknown":   ("network-copyleft",  "网络服务对外提供即触发源码开放义务（具体版本待人工确认）"),
    "UNKNOWN":        ("unknown",           "许可证未识别，须人工确认后方可分发"),
}

CATEGORY_CN = {
    "permissive": "宽松许可",
    "weak-copyleft": "弱传染",
    "strong-copyleft": "强传染",
    "network-copyleft": "网络传染",
    "unknown": "未识别",
}
# 传染强度排序，用于复合表达式取最严格者
CATEGORY_RANK = {"permissive": 0, "weak-copyleft": 1, "strong-copyleft": 2,
                 "network-copyleft": 3, "unknown": 4}

# 项目自身许可证 -> 允许的依赖类别（自主设计的兼容性矩阵）
COMPAT_MATRIX = {
    "MIT":           {"permissive", "weak-copyleft"},
    "BSD-2-Clause":  {"permissive", "weak-copyleft"},
    "BSD-3-Clause":  {"permissive", "weak-copyleft"},
    "Apache-2.0":    {"permissive", "weak-copyleft"},
    "ISC":           {"permissive", "weak-copyleft"},
    "PSF-2.0":       {"permissive", "weak-copyleft"},
    "MPL-2.0":       {"permissive", "weak-copyleft"},
    "LGPL-3.0-only": {"permissive", "weak-copyleft"},
    "GPL-2.0-only":  {"permissive", "weak-copyleft", "strong-copyleft"},
    "GPL-3.0-only":  {"permissive", "weak-copyleft", "strong-copyleft"},
    "AGPL-3.0-only": {"permissive", "weak-copyleft", "strong-copyleft", "network-copyleft"},
}


def normalize_single(tok):
    """归一化单个许可证标识（不含 AND/OR 组合），保留 -or-later 语义。"""
    s = (tok or "").strip().strip("()").rstrip(".").strip()
    if not s or s.lower() in ("unknown", "none", "null", "unlicensed", "see license"):
        return "UNKNOWN"
    later = bool(re.search(r"(-or-later|\+)$", s, re.IGNORECASE))
    base = re.sub(r"(-or-later|\+)$", "", s, flags=re.IGNORECASE).strip()
    for pat, spdx in SPDX_PATTERNS:
        if re.search(pat, base, re.IGNORECASE):
            if later and spdx.endswith("-only"):
                return spdx.replace("-only", "-or-later")
            return spdx
    return "UNKNOWN"


def _looks_like_spdx_expression(s):
    """判断字符串是否是 SPDX 表达式（AND/OR/WITH 分隔的裸标识符）。

    关键：不能无条件按 AND/OR 拆分。Trove classifier 里的
    "GNU Library or Lesser General Public License (LGPL)" 是自然语言描述，
    其中的 " or " 不是 SPDX 的 OR 运算符；误拆会把它切成
    "GNU Library" + "Lesser General Public License (LGPL)" 两个无法识别的碎片。
    """
    toks = re.split(r"\s+(?:AND|OR|WITH)\s+", s.strip(), flags=re.IGNORECASE)
    if len(toks) == 1:
        return False
    for i, t in enumerate(toks):
        if i % 2 == 1:                      # 分隔符位置
            continue
        if not re.fullmatch(r"[A-Za-z0-9.+\-]+", t.strip()):
            return False                    # 片段含空格等，说明是自然语言
    return True


def normalize_license(raw):
    """归一化完整许可证字符串，支持 SPDX 复合表达式（AND / OR / WITH）。"""
    if not raw:
        return "UNKNOWN"
    s = raw.strip()
    if not _looks_like_spdx_expression(s):
        return normalize_single(s)
    ids = []
    for p in re.split(r"\s+(?:AND|OR|WITH)\s+", s, flags=re.IGNORECASE):
        spdx = normalize_single(p)
        if spdx != "UNKNOWN" and spdx not in ids:
            ids.append(spdx)
    if not ids:
        return "UNKNOWN"
    return " AND ".join(ids)


def category_of(spdx):
    """取许可证（含复合表达式）的最严格类别。"""
    if not spdx or spdx == "UNKNOWN":
        return "unknown"
    cats = []
    for part in spdx.split(" AND "):
        base = re.sub(r"-or-later$", "-only", part)
        cat = LICENSE_DB.get(part, LICENSE_DB.get(base, ("unknown", "")))[0]
        cats.append(cat)
    if not cats:
        return "unknown"
    return max(cats, key=lambda c: CATEGORY_RANK.get(c, 4))


def obligations_of(spdx):
    """汇总复合表达式各项的许可义务。"""
    obs = []
    for part in (spdx or "UNKNOWN").split(" AND "):
        base = re.sub(r"-or-later$", "-only", part)
        ob = LICENSE_DB.get(part, LICENSE_DB.get(base, ("unknown", "须人工确认")))[1]
        if ob not in obs:
            obs.append(ob)
    return "；".join(obs)


# ---------------------------------------------------------------- 元数据抓取

_cache = {}


def _get(url, tries=3):
    if url in _cache:
        return _cache[url]
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            _cache[url] = data
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                _cache[url] = None
                return None
            last = e
        except Exception as e:
            last = e
        time.sleep(1.2 * (i + 1))
    _cache[url] = {"__error__": str(last)}
    return _cache[url]


def _blank(name, source, version, status):
    return {"name": name, "source": source, "version": version,
            "license_raw": "", "spdx": "UNKNOWN", "license_field": "无",
            "confidence": "无", "deps": [], "status": status}


def resolve_license_pypi(info):
    """按可信度从高到低解析许可证，返回 (原始值, 字段来源, 可信度)。

    优先级设计（踩坑后修正的关键逻辑）：
      1. license_expression  —— PEP 639 结构化字段，最可信
      2. Trove classifiers   —— 官方分类器，可信
      3. license 字段首行     —— 通常是许可证名称
      4. license 字段截断     —— 兜底，降级为低可信度

    第 4 档必须截断：部分包的 license 字段是数万字的完整许可证正文
    （实测 pandas 达 61,643 字符），正文里会顺带提到 "GNU General Public
    License"，若做全文关键词匹配必然误判为 GPL。
    """
    expr = (info.get("license_expression") or "").strip()
    if expr and normalize_license(expr) != "UNKNOWN":
        return expr, "license_expression", "高"

    for c in info.get("classifiers", []) or []:
        if c.startswith("License ::"):
            cand = c.split("::")[-1].strip()
            if normalize_license(cand) != "UNKNOWN":
                return cand, "trove classifier", "高"

    lic = (info.get("license") or "").strip()
    if lic:
        head = lic.splitlines()[0].strip()[:120]
        if head and normalize_license(head) != "UNKNOWN":
            return head, "license 首行", "中"
        return lic[:200], "license 截断(低可信)", "低"
    return "", "无", "无"


def fetch_pypi(name, version=None):
    url = PYPI.format(name=name) if not version else f"https://pypi.org/pypi/{name}/{version}/json"
    d = _get(url)
    if d is None:
        return _blank(name, "PyPI", version or "?", "NOT_FOUND")
    if "__error__" in d:
        return _blank(name, "PyPI", version or "?", "FETCH_ERROR")
    info = d.get("info", {})
    raw, field, conf = resolve_license_pypi(info)
    deps = []
    for req in info.get("requires_dist", []) or []:
        if "extra ==" in req:          # 跳过可选依赖组
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)", req.strip())
        if m:
            deps.append(m.group(1))
    return {"name": info.get("name") or name, "source": "PyPI",
            "version": info.get("version") or version or "?",
            "license_raw": raw[:160], "spdx": normalize_license(raw),
            "license_field": field, "confidence": conf,
            "deps": deps, "status": "OK"}


def fetch_npm(name):
    d = _get(NPM.format(name=name))
    if d is None:
        return _blank(name, "npm", "?", "NOT_FOUND")
    if "__error__" in d:
        return _blank(name, "npm", "?", "FETCH_ERROR")
    raw = d.get("license") or ""
    if isinstance(raw, dict):
        raw = raw.get("type", "")
    if not raw and d.get("licenses"):
        lic = d["licenses"]
        raw = lic[0].get("type", "") if isinstance(lic, list) and lic else ""
    return {"name": d.get("name") or name, "source": "npm",
            "version": d.get("version") or "?",
            "license_raw": str(raw)[:160], "spdx": normalize_license(str(raw)),
            "license_field": "license",
            "confidence": "高" if str(raw).strip() else "无",
            "deps": list((d.get("dependencies") or {}).keys()), "status": "OK"}


# ---------------------------------------------------------------- 清单解析

def parse_requirements(path: Path):
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-") or "://" in line:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(?:[<>=!~\[;].*)?$", line)
        if m:
            out.append(m.group(1))
    return out


def parse_package_json(path: Path):
    d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    return sorted(set(list((d.get("dependencies") or {}).keys()) +
                      list((d.get("devDependencies") or {}).keys())))


_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)")


def _name_of(spec):
    m = _REQ_NAME.match(str(spec))
    return m.group(1) if m else None


def parse_pyproject(path: Path):
    """解析 pyproject.toml 的依赖。

    支持三种主流写法：
      · PEP 621  [project] dependencies = [...] / optional-dependencies
      · PEP 735  [dependency-groups]
      · Poetry   [tool.poetry.dependencies] / [tool.poetry.group.*.dependencies]
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    out = set()
    try:
        import tomllib
        d = tomllib.loads(raw)
    except ImportError:                      # Python < 3.11 无 tomllib
        return _parse_pyproject_regex(raw)
    except Exception as e:
        raise SystemExit(f"pyproject.toml 解析失败：{e}")

    proj = d.get("project") or {}
    for spec in proj.get("dependencies") or []:
        n = _name_of(spec)
        if n:
            out.add(n)
    for group in (proj.get("optional-dependencies") or {}).values():
        for spec in group or []:
            n = _name_of(spec)
            if n:
                out.add(n)

    for group in (d.get("dependency-groups") or {}).values():
        for spec in group or []:
            if isinstance(spec, str):
                n = _name_of(spec)
                if n:
                    out.add(n)

    poetry = ((d.get("tool") or {}).get("poetry") or {})
    for name in (poetry.get("dependencies") or {}):
        if name.lower() != "python":
            out.add(name)
    for grp in (poetry.get("group") or {}).values():
        for name in ((grp or {}).get("dependencies") or {}):
            if name.lower() != "python":
                out.add(name)
    return sorted(out)


def _parse_pyproject_regex(raw):
    """无 tomllib 时的降级解析：够用即可，覆盖 requirements 数组的常见写法。"""
    out = set()
    in_deps = False
    for line in raw.splitlines():
        s = line.strip()
        if re.match(r"^dependencies\s*=\s*\[", s):
            in_deps = True
            s = s.split("[", 1)[1]
        if in_deps:
            m = re.search(r"[\"']([^\"']+)[\"']", s)
            if m:
                n = _name_of(m.group(1))
                if n:
                    out.add(n)
            if "]" in s:
                in_deps = False
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*=\s*[\"{]", s)
        if m and m.group(1).lower() != "python":
            out.add(m.group(1))
    return sorted(out)


# ---------------------------------------------------------------- 冲突检测

def detect_conflicts(project_license, records, transitive_limit=0):
    """返回冲突列表。每条含：级别 / 依赖 / 许可证 / 原因 / 建议。"""
    findings = []
    allowed = COMPAT_MATRIX.get(project_license, set())

    for r in records:
        if r["status"] != "OK":
            findings.append({
                "level": "中", "pkg": r["name"], "license": r["spdx"],
                "reason": f"无法从 {r['source']} 获取元数据（{r['status']}），许可证状态未知",
                "advice": "人工核对仓库 LICENSE 文件后补填清单",
            })
            continue
        cat = category_of(r["spdx"])
        note = "（复合许可证，已按最严格项判定）" if " AND " in r["spdx"] else ""

        if cat == "unknown":
            low = r.get("confidence") in ("低", "无")
            findings.append({
                "level": "中" if low else "高", "pkg": r["name"], "license": r["spdx"],
                "reason": f"许可证无法识别{note}（字段来源：{r.get('license_field','?')}，"
                          f"可信度 {r.get('confidence','?')}，原始值：{r['license_raw'] or '空'}）",
                "advice": "人工核对仓库 LICENSE 文件后补填清单，不得直接纳入分发范围",
            })
        elif cat == "network-copyleft":
            findings.append({
                "level": "高", "pkg": r["name"], "license": r["spdx"],
                "reason": f"AGPL 依赖{note}：只要项目以网络服务形式对外提供，即触发整体源码开放义务",
                "advice": "确认是否接受开源全部服务端代码；否则替换该依赖或隔离为独立进程",
            })
        elif cat == "strong-copyleft" and cat not in allowed:
            findings.append({
                "level": "高", "pkg": r["name"], "license": r["spdx"],
                "reason": f"{project_license} 项目引入强传染依赖{note}，衍生作品可能需整体以 GPL 开放",
                "advice": "改用宽松许可替代品，或将该依赖隔离为独立可执行程序并通过进程边界调用",
            })
        elif cat == "weak-copyleft" and cat not in allowed:
            findings.append({
                "level": "中", "pkg": r["name"], "license": r["spdx"],
                "reason": f"弱传染依赖{note}：修改库本体需按原许可开放",
                "advice": "保持动态链接、不改动库本体，并在清单中注明",
            })

    # 传递依赖抽查：只看直接依赖时最容易漏掉传染性许可
    if transitive_limit > 0:
        direct = {r["name"].lower() for r in records}
        checked = 0
        for r in records:
            for sub in r.get("deps", []):
                if checked >= transitive_limit:
                    break
                if sub.lower() in direct:
                    continue
                checked += 1
                info = fetch_pypi(sub) if r["source"] == "PyPI" else fetch_npm(sub)
                cat = category_of(info["spdx"])
                if cat in ("strong-copyleft", "network-copyleft"):
                    findings.append({
                        "level": "高", "pkg": f"{r['name']} → {info['name']}",
                        "license": info["spdx"],
                        "reason": "传递依赖中检出"
                                  f"{'网络' if cat == 'network-copyleft' else '强'}传染许可，"
                                  "直接依赖清单里看不到",
                        "advice": "纳入清单并评估；这是仅检查直接依赖时最常见的漏检点",
                    })
            if checked >= transitive_limit:
                break
    return findings


# ---------------------------------------------------------------- 主流程

def audit(packages, project_license="MIT", source="PyPI", depth=0, transitive_limit=0):
    records, seen, frontier = [], set(), list(packages)
    level = 0
    while frontier and level <= depth:
        nxt = []
        for name in frontier:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            rec = fetch_pypi(name) if source == "PyPI" else fetch_npm(name)
            records.append(rec)
            if level < depth:
                nxt.extend(rec.get("deps", []))
        frontier = nxt
        level += 1
    return records, detect_conflicts(project_license, records, transitive_limit)


def to_checklist_table(records):
    """生成符合竞赛要求的《开源及第三方资源使用清单》。"""
    lines = ["| 资源名称 | 类型 | 版本 | 来源 | 许可证/授权类型 | 识别依据 | 使用方式 | "
             "关键许可义务或限制 | 自主开发边界 | 合规状态 | 开放方式 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        cat = category_of(r["spdx"])
        status = {"OK": "已核验" if cat != "unknown" else "待确认",
                  "NOT_FOUND": "未找到",
                  "FETCH_ERROR": "获取失败"}[r["status"]]
        lines.append("| {} | {} 依赖包 | {} | {} | {} | {}（可信度{}） | "
                     "作为库调用（未修改源码） | {} | 未修改，仅调用公开 API | {} | "
                     "随项目一并声明许可 |".format(
                         r["name"], r["source"], r["version"], r["source"],
                         r["spdx"] if r["spdx"] != "UNKNOWN" else "未识别",
                         r.get("license_field", "?"), r.get("confidence", "?"),
                         obligations_of(r["spdx"]), status))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requirements")
    ap.add_argument("--package-json")
    ap.add_argument("--pyproject")
    ap.add_argument("--packages", nargs="*")
    ap.add_argument("--project-license", default="MIT")
    ap.add_argument("--project-name", default="待填")
    ap.add_argument("--depth", type=int, default=0, help="0=仅直接依赖")
    ap.add_argument("--transitive", type=int, default=0, help="抽查传递依赖条数上限，0=关闭")
    ap.add_argument("--out", default="license_audit_report.md")
    a = ap.parse_args()

    if a.requirements:
        pkgs, source = parse_requirements(Path(a.requirements)), "PyPI"
    elif a.pyproject:
        pkgs, source = parse_pyproject(Path(a.pyproject)), "PyPI"
    elif a.package_json:
        pkgs, source = parse_package_json(Path(a.package_json)), "npm"
    elif a.packages:
        pkgs, source = a.packages, "PyPI"
    else:
        ap.error("需要 --requirements / --pyproject / --package-json / --packages 之一")

    print(f"[1/4] 解析依赖清单：{len(pkgs)} 个直接依赖（来源 {source}）")
    records, findings = audit(pkgs, a.project_license, source, a.depth, a.transitive)
    ok = sum(1 for r in records if r["status"] == "OK")
    print(f"[2/4] 获取许可证元数据：成功 {ok} / 共 {len(records)}")

    stats = {}
    for r in records:
        c = category_of(r["spdx"])
        stats[c] = stats.get(c, 0) + 1
    print("[3/4] 许可证类别分布：" + "，".join(
        f"{CATEGORY_CN.get(k,k)} {v}" for k, v in sorted(stats.items(), key=lambda x: -x[1])))
    hi = sum(1 for f in findings if f["level"] == "高")
    print(f"[4/4] 检出风险项：{len(findings)} 条（高 {hi} / 中 {len(findings)-hi}）")

    md = ["# 《开源及第三方资源使用清单》（自动生成）", "",
          f"**项目名称**：{a.project_name}　**项目自身许可证**：{a.project_license}　"
          f"**扫描依赖数**：{len(records)}", "", "## 一、风险汇总", ""]
    if findings:
        md += ["| 级别 | 资源 | 检出许可证 | 风险说明 | 处理建议 |", "|---|---|---|---|---|"]
        for f in sorted(findings, key=lambda x: 0 if x["level"] == "高" else 1):
            md.append(f"| {f['level']} | {f['pkg']} | {f['license']} | {f['reason']} | {f['advice']} |")
    else:
        md.append("未检出许可证兼容性风险。")
    md += ["", "## 二、资源清单", "", to_checklist_table(records), "",
           f"> 项目自身许可证：{a.project_license}　|　清单由脚本自动生成，"
           f"「识别依据」列标注了每个许可证的判定来源，可信度非「高」的项须人工复核　|　共 {len(records)} 项"]
    Path(a.out).write_text("\n".join(md), encoding="utf-8")
    Path(a.out.replace(".md", ".json")).write_text(
        json.dumps({"project": a.project_name, "project_license": a.project_license,
                    "records": records, "findings": findings, "stats": stats},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"报告已写出：{a.out} / {a.out.replace('.md', '.json')}")


if __name__ == "__main__":
    main()
