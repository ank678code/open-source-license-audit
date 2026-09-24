#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_cases.py — license_audit 回归测试用例集

每个用例都对应开发过程中实测发现的真实误判，不是编造的假数据。
不依赖网络，可直接 `python test_cases.py` 运行。

用法：
  python test_cases.py            # 跑全部用例
  python test_cases.py -v         # 打印每个用例的详细判定过程
"""

import sys

from license_audit import (normalize_license, category_of,
                           resolve_license_pypi, detect_conflicts,
                           split_workspace_deps)

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0
FAILURES = []


def check(case_id, desc, got, want):
    global PASS, FAIL
    ok = got == want
    if ok:
        PASS += 1
        if VERBOSE:
            print(f"  PASS  [{case_id}] {desc}")
            print(f"        判定 = {got}")
    else:
        FAIL += 1
        FAILURES.append((case_id, desc, got, want))
        print(f"  FAIL  [{case_id}] {desc}")
        print(f"        期望 = {want}")
        print(f"        实际 = {got}")


# ============================================================ A. 许可证归一化

print("\nA. 许可证字符串归一化（对应实测发现的误判）")

# A1 pandas：PyPI 的 license 字段是 61,643 字符的完整许可证正文，
#    正文里顺带提到 "GNU General Public License"，全文关键词匹配会误判为 GPL。
#    正确行为：走 trove classifier，得到 BSD-3-Clause。
pandas_info = {
    "license_expression": None,
    "license": "BSD 3-Clause License\n\nCopyright (c) 2008-2011, AQR Capital Management\n"
               + "x" * 60000 +
               "\n         previously distributed under the GNU General Public License (GPL), the\n",
    "classifiers": ["License :: OSI Approved :: BSD License"],
}
got, field, conf = resolve_license_pypi(pandas_info)
check("A1", "pandas 61KB 许可证正文不得被误判为 GPL（应走 classifier）",
      (normalize_license(got), field), ("BSD-3-Clause", "trove classifier"))

# A2 numpy：license_expression 是复合表达式，须保留全部成分，不能只取第一个匹配。
numpy_expr = "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0"
check("A2", "numpy 复合许可证表达式应完整保留",
      normalize_license(numpy_expr), "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0")
check("A3", "numpy 复合许可证类别应取最严格者（全宽松 → 宽松）",
      category_of(normalize_license(numpy_expr)), "permissive")

# A4 mysqlclient：-or-later 与 -only 的兼容性含义不同，不得丢失后缀。
check("A4", "GPL-2.0-or-later 不得被归一为 GPL-2.0-only",
      normalize_license("GPL-2.0-or-later"), "GPL-2.0-or-later")

# A5 PyQt5：license 字段写作 "GPL v3"，中间的 v 此前导致无法识别。
check("A5", '"GPL v3" 应识别为 GPL-3.0-only',
      normalize_license("GPL v3"), "GPL-3.0-only")

# A6 chardet：0BSD 此前不在知识库中，应识别为宽松许可。
check("A6", '"0BSD" 应识别为 0BSD',
      normalize_license("0BSD"), "0BSD")
check("A7", "0BSD 应归入宽松许可",
      category_of("0BSD"), "permissive")

# A8 psycopg2：trove classifier 是自然语言
#    "GNU Library or Lesser General Public License (LGPL)"，
#    其中的 " or " 不是 SPDX 的 OR 运算符，误拆会切成两个无法识别的碎片。
psycopg_info = {
    "license_expression": None,
    "license": "LGPL with exceptions",
    "classifiers": ["License :: OSI Approved :: "
                    "GNU Library or Lesser General Public License (LGPL)"],
}
got, field, conf = resolve_license_pypi(psycopg_info)
check("A8", "自然语言中的 \" or \" 不得被当作 SPDX 的 OR 运算符拆分；"
            "无版本号的 GNU 全称不得臆断为 3.0",
      normalize_license(got), "LGPL-unknown")

# A9 tqdm：合法的 SPDX 复合表达式，必须正常拆分。
check("A9", '"MPL-2.0 AND MIT" 应被拆分为两项',
      normalize_license("MPL-2.0 AND MIT"), "MPL-2.0 AND MIT")
check("A10", "MPL-2.0 AND MIT 的类别应取最严格者（弱传染）",
      category_of(normalize_license("MPL-2.0 AND MIT")), "weak-copyleft")

# A11 pymupdf：双许可写法，含自然语言前缀。
check("A11", "双许可描述应识别出 AGPL 成分",
      normalize_license("Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License"),
      "AGPL-3.0-only")
check("A12", "AGPL 应归入网络传染",
      category_of("AGPL-3.0-only"), "network-copyleft")

# A13 只写家族名不写版本号：类别可判定，但必须标明版本待确认，
#     不能给出一个看似确定的错误版本。
check("A13", "仅写 LGPL 无版本号时应标明版本待确认",
      normalize_license("LGPL with exceptions"), "LGPL-unknown")
check("A14", "LGPL-unknown 的类别仍应为弱传染",
      category_of("LGPL-unknown"), "weak-copyleft")

# A15 空值与无效值
check("A15", "空字符串应为 UNKNOWN", normalize_license(""), "UNKNOWN")
check("A16", '"see license" 等占位写法应为 UNKNOWN',
      normalize_license("see license"), "UNKNOWN")

# A17 宽松许可的常见写法
check("A17", '"MIT License" 应识别为 MIT', normalize_license("MIT License"), "MIT")
check("A18", '"Apache Software License" 应识别为 Apache-2.0',
      normalize_license("Apache Software License"), "Apache-2.0")
check("A19", '"BSD License"（无版本号）按最常见的 3-Clause 推断',
      normalize_license("BSD License"), "BSD-3-Clause")
# A20 protobuf：license 字段写作 "3-Clause BSD License"，词序与常见写法相反。
check("A20", '"3-Clause BSD License"（词序颠倒）应识别为 BSD-3-Clause',
      normalize_license("3-Clause BSD License"), "BSD-3-Clause")
check("A21", '"2-Clause BSD License"（词序颠倒）应识别为 BSD-2-Clause',
      normalize_license("2-Clause BSD License"), "BSD-2-Clause")
# A22 占位写法：rouge 的 license 字段只写了文件名。
check("A22", '"LICENCE.txt" 等占位值应为 UNKNOWN',
      normalize_license("LICENCE.txt"), "UNKNOWN")
# A23 npm 生态新许可证：rimraf 等包已改用 Blue Oak Model License。
check("A23", '"BlueOak-1.0.0" 应识别为宽松许可',
      (normalize_license("BlueOak-1.0.0"), category_of("BlueOak-1.0.0")),
      ("BlueOak-1.0.0", "permissive"))
# A24 Pillow 的 license_expression 是 "MIT-CMU"，须在通用 MIT 模式之前匹配，
#     否则会被吃掉或（更早的版本里）完全识别不出来。
check("A24", '"MIT-CMU"（Pillow）应识别为 MIT-CMU 而非泛化 MIT',
      normalize_license("MIT-CMU"), "MIT-CMU")
check("A25", "MIT-CMU 应归入宽松许可",
      category_of("MIT-CMU"), "permissive")
# A26 回归保护：通用 MIT 写法不能被 MIT-CMU 规则影响
check("A26", '"MIT License" 仍应识别为 MIT',
      normalize_license("MIT License"), "MIT")

# A27-A31：SPDX 运算符语义（OR 任选其一 / WITH 例外）
check("A27", '"MIT OR Apache-2.0" 应保留 OR 语义而非误归一为 AND',
      normalize_license("MIT OR Apache-2.0"), "MIT OR Apache-2.0")
check("A28", "OR 双许可任选其一（可选 MIT）应取最宽松项判定",
      category_of("GPL-3.0-only OR MIT"), "permissive")
check("A29", '"GPL-2.0-only OR Apache-2.0" 不应被误判为强传染',
      category_of("GPL-2.0-only OR Apache-2.0"), "permissive")
check("A30", '"GPL-2.0-only WITH Classpath-exception-2.0" 应只保留主许可证',
      normalize_license("GPL-2.0-only WITH Classpath-exception-2.0"), "GPL-2.0-only")
check("A31", "WITH 例外不影响主许可证类别",
      category_of("GPL-2.0-only WITH Classpath-exception-2.0"), "strong-copyleft")

# A32-A35：无版本号全称不得臆断版本
check("A32", "无版本号的 GNU GPL 全称应标为 GPL-unknown 而非 GPL-3.0-only",
      normalize_license("GNU General Public License"), "GPL-unknown")
check("A33", "带版本号的 GNU GPL 全称应识别为对应版本",
      normalize_license("GNU General Public License v3"), "GPL-3.0-only")
check("A34", '"v2 or later" 自然语言写法应识别为 GPL-2.0-or-later',
      normalize_license("GNU General Public License v2 or later"), "GPL-2.0-or-later")
check("A35", "无版本号的 GNU LGPL 全称应标为 LGPL-unknown",
      normalize_license("GNU Lesser General Public License"), "LGPL-unknown")


# ============================================================ B. 兼容性冲突检测

print("\nB. 兼容性冲突检测（兼容性矩阵）")


def mk(name, spdx, conf="高", field="license_expression", status="OK"):
    return {"name": name, "source": "PyPI", "version": "1.0", "license_raw": spdx,
            "spdx": spdx, "license_field": field, "confidence": conf,
            "deps": [], "status": status}


def levels(findings, pkg):
    return [f["level"] for f in findings if f["pkg"] == pkg]


# B1 MIT 项目引入 GPL-2.0 依赖 → 高危
f = detect_conflicts("MIT", [mk("mysqlclient", "GPL-2.0-or-later")])
check("B1", "MIT 项目 + GPL 依赖应报高危", levels(f, "mysqlclient"), ["高"])

# B2 Apache-2.0 项目引入 AGPL 依赖 → 高危（网络传染）
f = detect_conflicts("Apache-2.0", [mk("pymupdf", "AGPL-3.0-only")])
check("B2", "Apache-2.0 项目 + AGPL 依赖应报高危", levels(f, "pymupdf"), ["高"])

# B3 GPL-3.0 项目引入 GPL 依赖 → 不冲突
f = detect_conflicts("GPL-3.0-only", [mk("mysqlclient", "GPL-2.0-or-later")])
check("B3", "GPL 项目 + GPL 依赖不应报冲突", levels(f, "mysqlclient"), [])

# B4 MIT 项目引入 LGPL 依赖 → 不冲突（动态链接下兼容），
#    但许可义务必须仍被记录进清单，不能因为"不冲突"就丢掉信息。
f = detect_conflicts("MIT", [mk("psycopg2", "LGPL-3.0-only")])
check("B4", "MIT 项目 + LGPL 依赖不应报冲突（兼容）", levels(f, "psycopg2"), [])
from license_audit import obligations_of
check("B4b", "LGPL 依赖的许可义务应仍被记录（不因不冲突而丢失）",
      obligations_of("LGPL-3.0-only") != "" and "LGPL" in obligations_of("LGPL-3.0-only"), True)

# B5 复合许可证含传染成分 → 按最严格项报高危
f = detect_conflicts("MIT", [mk("weird", "MIT AND GPL-3.0-only")])
check("B5", "复合许可证含 GPL 成分应按最严格项报高危", levels(f, "weird"), ["高"])

# B6 许可证无法识别且可信度低 → 中危（而非高危），并提示人工复核
f = detect_conflicts("MIT", [mk("mystery", "UNKNOWN", conf="低", field="license 截断(低可信)")])
check("B6", "低可信度未识别项应报中危", levels(f, "mystery"), ["中"])

# B7 全部宽松许可 → 无任何冲突
f = detect_conflicts("MIT", [mk("a", "MIT"), mk("b", "Apache-2.0"),
                            mk("c", "BSD-3-Clause"), mk("d", "0BSD")])
check("B7", "全宽松许可依赖不应报任何冲突", f, [])

# B8 元数据获取失败 → 中危，提示人工核对
f = detect_conflicts("MIT", [mk("ghost", "UNKNOWN", conf="无", field="无", status="NOT_FOUND")])
check("B8", "元数据缺失的依赖应报中危", levels(f, "ghost"), ["中"])

# B9 项目自身许可证不在矩阵中（如自定义/专有许可证）→ 仍应检出传染性依赖，
#    同时不误报宽松许可依赖（MIT/Apache 用在专有项目里是允许的）。
f = detect_conflicts("Proprietary", [mk("a", "MIT"), mk("b", "LGPL-3.0-only")])
check("B9", "项目许可证未知时应检出传染性依赖且不误报宽松依赖",
      sorted(x["pkg"] for x in f), ["b"])

# B10 OR 双许可（任选其一，含宽松选项）不应报高危
f = detect_conflicts("MIT", [mk("dual", "GPL-3.0-only OR MIT")])
check("B10", "OR 双许可任选其一（可选 MIT）不应报高危", levels(f, "dual"), [])

# B11 修复 OR 后 AND 复合含传染成分仍应报高危（不因宽松化而漏报）
f = detect_conflicts("MIT", [mk("strict", "MIT AND GPL-3.0-only")])
check("B11", "AND 复合含 GPL 成分仍应报高危", levels(f, "strict"), ["高"])


# ============================================================ C. 依赖清单解析

print("\nC. 依赖清单解析（pyproject.toml 三种主流写法）")

import tempfile
from pathlib import Path as _Path
from license_audit import parse_pyproject, parse_requirements, parse_package_json

_tmp = _Path(tempfile.mkdtemp())


def _write(name, text):
    p = _tmp / name
    p.write_text(text, encoding="utf-8")
    return p


# C1 PEP 621
pep621 = _write("c1.toml", """
[project]
name = "demo"
dependencies = [
  "requests>=2.31.0",
  "pandas",
  "pymupdf>=1.24",
]

[project.optional-dependencies]
dev = ["pytest>=7", "ruff"]
""")
got = parse_pyproject(pep621)
check("C1", "PEP 621 dependencies 应被解析（含可选依赖组）",
      sorted(got), ["pandas", "pymupdf", "pytest", "requests", "ruff"])

# C2 Poetry
poetry = _write("c2.toml", """
[tool.poetry.dependencies]
python = "^3.10"
flask = "^3.0"
mysqlclient = "*"

[tool.poetry.group.dev.dependencies]
black = "^24.0"
""")
got = parse_pyproject(poetry)
check("C2", "Poetry 依赖应被解析且排除 python 自身",
      sorted(got), ["black", "flask", "mysqlclient"])

# C3 PEP 735 dependency-groups
pep735 = _write("c3.toml", """
[dependency-groups]
test = ["pytest", "coverage"]
docs = ["mkdocs"]
""")
check("C3", "PEP 735 dependency-groups 应被解析",
      sorted(parse_pyproject(pep735)), ["coverage", "mkdocs", "pytest"])

# C4 带环境标记与附加项的写法
req = _write("c4.txt", """
# 注释行
requests>=2.31.0
flask
uvicorn[standard]>=0.30
torch ; python_version < "3.13"
-r other.txt
git+https://github.com/x/y.git#egg=y
""")
check("C4", "requirements.txt 应跳过注释、-r、URL 行并保留包名",
      sorted(parse_requirements(req)), ["flask", "requests", "torch", "uvicorn"])

# C5 package.json
pkg = _write("c5.json", '{"dependencies":{"express":"^4"},"devDependencies":{"jest":"^29"}}')
check("C5", "package.json 的 dependencies 与 devDependencies 应合并去重",
      sorted(parse_package_json(pkg)), ["express", "jest"])

# C6 空清单不应崩溃
empty = _write("c6.txt", "# 只有注释\n")
check("C6", "空依赖清单应返回空列表而非崩溃", parse_requirements(empty), [])

# C7-C11 版本约束保留与精确版本提取
from license_audit import parse_requirements_verbose, _exact_version
vreq = _write("c7.txt", 'requests==2.31.0\nflask>=3.0\n'
                         'uvicorn[standard]~=0.30 ; python_version < "3.13"\nplain\n')
got = parse_requirements_verbose(vreq)
check("C7", "requirements 版本约束应被提取（extras/环境标记去除）",
      sorted(got), [("flask", ">=3.0"), ("plain", ""),
                    ("requests", "==2.31.0"), ("uvicorn", "~=0.30")])
check("C8", "pip 精确锁定版本应可提取", _exact_version("==2.31.0"), "2.31.0")
check("C9", "pip 范围约束不应被当作精确版本", _exact_version(">=3.0"), None)
check("C10", "npm 纯版本号应视为精确锁定", _exact_version("1.2.3"), "1.2.3")
check("C11", "npm 范围写法不应被当作精确版本", _exact_version("^1.2.3"), None)


# ============================================================ D. 兼容性矩阵覆盖

print("\nD. 兼容性矩阵覆盖（v0.3 修复：矩阵外取值不再静默误报）")

from license_audit import (COMPAT_MATRIX, matrix_notice, CATEGORY_RANK,
                           LICENSE_DB, to_checklist_table, PENDING_USAGE,
                           VERSION)

# D1 矩阵必须覆盖全部内置项目许可证标识。
#    修复前只有 11 项，其余取值会让 COMPAT_MATRIX.get(..., set()) 返回空集合，
#    静默退化成"任何传染性依赖都报冲突"——既误报又不可解释。
_MUST_COVER = [
    "MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "PSF-2.0",
    "Unlicense", "CC0-1.0", "0BSD", "Zlib", "HPND", "MIT-CMU", "BlueOak-1.0.0",
    "WTFPL", "Artistic-2.0", "PostgreSQL", "BSL-1.0", "MulanPSL-2.0",
    "MPL-2.0", "EPL-2.0", "CDDL-1.0", "LGPL-2.0-only", "LGPL-2.1-only",
    "LGPL-3.0-only", "LGPL-unknown",
    "GPL-2.0-only", "GPL-3.0-only", "GPL-unknown", "EUPL-1.2",
    "AGPL-3.0-only", "AGPL-unknown",
]
check("D1", "兼容性矩阵应覆盖全部内置项目许可证标识",
      [x for x in _MUST_COVER if x not in COMPAT_MATRIX], [])
check("D2", "矩阵取值只允许是四类传染强度中的合法组合",
      sorted({c for v in COMPAT_MATRIX.values() for c in v} - set(CATEGORY_RANK)),
      [])
check("D3", "许可证知识库里出现的类别都应能在矩阵中表达",
      sorted({v[0] for v in LICENSE_DB.values()} - set(CATEGORY_RANK)), [])

# D4-D12 修复前会误报的"项目许可证在表外"场景，现在都应有确定结论
check("D4", "Unlicense 项目 + LGPL 依赖不应报冲突（修复前会误报）",
      levels(detect_conflicts("Unlicense", [mk("a", "LGPL-3.0-only")]), "a"), [])
check("D5", "CC0-1.0 项目 + GPL 依赖应报高危",
      levels(detect_conflicts("CC0-1.0", [mk("a", "GPL-3.0-only")]), "a"), ["高"])
check("D6", "Zlib 项目 + MPL 依赖不应报冲突",
      levels(detect_conflicts("Zlib", [mk("a", "MPL-2.0")]), "a"), [])
check("D7", "0BSD 项目 + AGPL 依赖应报高危",
      levels(detect_conflicts("0BSD", [mk("a", "AGPL-3.0-only")]), "a"), ["高"])
check("D8", "MIT-CMU 项目 + LGPL 依赖不应报冲突",
      levels(detect_conflicts("MIT-CMU", [mk("a", "LGPL-2.1-only")]), "a"), [])
check("D9", "BlueOak-1.0.0 项目 + GPL 依赖应报高危",
      levels(detect_conflicts("BlueOak-1.0.0", [mk("a", "GPL-2.0-only")]), "a"), ["高"])
check("D10", "EPL-2.0 项目 + LGPL 依赖不应报冲突",
      levels(detect_conflicts("EPL-2.0", [mk("a", "LGPL-3.0-only")]), "a"), [])
check("D11", "MulanPSL-2.0 项目 + LGPL 依赖不应报冲突",
      levels(detect_conflicts("MulanPSL-2.0", [mk("a", "LGPL-2.1-only")]), "a"), [])
check("D12", "CDDL-1.0 项目 + GPL 依赖应报高危",
      levels(detect_conflicts("CDDL-1.0", [mk("a", "GPL-3.0-only")]), "a"), ["高"])

# D13-D16 传染强度单调性：项目许可自身传染性越强，能容纳的依赖范围越大
check("D13", "LGPL-3.0-only 项目可引入强传染依赖（LGPL-3.0 §2 允许转为 GPL-3.0）",
      levels(detect_conflicts("LGPL-3.0-only", [mk("a", "GPL-3.0-only")]), "a"), [])
check("D14", "LGPL-2.1-only 项目引入强传染依赖应报高危（无版本升级条款）",
      levels(detect_conflicts("LGPL-2.1-only", [mk("a", "GPL-3.0-only")]), "a"), ["高"])
check("D15", "GPL 项目可引入 GPL 依赖",
      levels(detect_conflicts("GPL-2.0-only", [mk("a", "GPL-2.0-or-later")]), "a"), [])
check("D16", "AGPL 项目可引入 GPL 依赖",
      levels(detect_conflicts("AGPL-3.0-only", [mk("a", "GPL-3.0-only")]), "a"), [])
check("D17", "GPL 项目引入 AGPL 依赖应报高危（强传染容不下网络传染）",
      levels(detect_conflicts("GPL-3.0-only", [mk("a", "AGPL-3.0-only")]), "a"), ["高"])
check("D18", "EUPL-1.2 项目可引入强传染依赖",
      levels(detect_conflicts("EUPL-1.2", [mk("a", "GPL-3.0-only")]), "a"), [])

# D19-D21 矩阵未覆盖时必须有显式提示，而不是假装有结论
check("D19", "矩阵外项目许可证应返回显式提示（不再静默）",
      matrix_notice("Proprietary") is not None, True)
check("D20", "提示语里应点名该许可证，便于使用者定位",
      "Proprietary" in (matrix_notice("Proprietary") or ""), True)
check("D21", "矩阵内项目许可证不应产生提示",
      matrix_notice("MIT"), None)
check("D22", "项目许可证为空时也应给出提示",
      matrix_notice("") is not None, True)


# ============================================================ E. 版本约束与锁定

print("\nE. 版本约束提取（v0.3 修复：v 前缀不再导致误报 404）")

# E1-E4 v0.3 修复：以前 "v1.2.3" 会被原样拿去查 PyPI，命中 404 后被误报为
#        "无法获取元数据"，而不是回退查最新版。
check("E1", '"v1.2.3" 应归一化为 "1.2.3"', _exact_version("v1.2.3"), "1.2.3")
check("E2", '"=v1.2.3" 应归一化为 "1.2.3"', _exact_version("=v1.2.3"), "1.2.3")
check("E3", '"V2.0" 应归一化为 "2.0"', _exact_version("V2.0"), "2.0")
check("E4", '"== 1.2.3"（等号后带空格）应可提取', _exact_version("== 1.2.3"), "1.2.3")

# E5-E10 各种范围/通配写法一律不得当作精确版本
check("E5", '"==1.2.3.*" 通配应返回 None', _exact_version("==1.2.3.*"), None)
check("E6", '">=1.0,<2.0" 组合范围应返回 None', _exact_version(">=1.0,<2.0"), None)
check("E7", '"1.2.3 - 2.0.0" npm 区间应返回 None', _exact_version("1.2.3 - 2.0.0"), None)
check("E8", '"latest" 这类非版本串应返回 None', _exact_version("latest"), None)
check("E9", '"*" 应返回 None', _exact_version("*"), None)
check("E10", "空串应返回 None", _exact_version(""), None)
check("E11", '"~=0.30" 兼容范围应返回 None', _exact_version("~=0.30"), None)

# E12-E14 版本约束要能从清单里带出来
from license_audit import parse_package_json_verbose, parse_pyproject_verbose
_pj = _write("e12.json", '{"dependencies":{"express":"^4"},"devDependencies":{"jest":"^29"}}')
check("E12", "package.json 应保留版本范围原文",
      parse_package_json_verbose(_pj), [("express", "^4"), ("jest", "^29")])
check("E13", "Poetry 依赖应保留版本约束",
      parse_pyproject_verbose(poetry),
      [("black", "^24.0"), ("flask", "^3.0"), ("mysqlclient", "*")])
check("E14", "requirements.txt 精确锁定应可识别",
      _exact_version(dict(parse_requirements_verbose(vreq)).get("requests", "")), "2.31.0")


# ============================================================ F. 清单表诚实性

print("\nF. 清单表诚实性（v0.3 修复：不再臆断使用方式）")


def _rec(name, spdx, status="OK", conf="高", field="license_expression"):
    return {"name": name, "source": "PyPI", "version": "1.0", "license_raw": spdx,
            "spdx": spdx, "license_field": field, "confidence": conf,
            "deps": [], "status": status}


# F1-F3 v0.3 修复：以前无论有没有源码证据，清单表都把「使用方式」硬编码为
#        "作为库调用（未修改源码）"、"自主开发边界"硬编码为"未修改，仅调用公开 API"，
#        等于在没有证据时假装确定，也与 README 已知限制自相矛盾。
_t = to_checklist_table([_rec("requests", "Apache-2.0")])
check("F1", "未采集源码证据时不得声称「作为库调用（未修改源码）」",
      "作为库调用（未修改源码）" not in _t, True)
check("F2", "未采集源码证据时应标注为待确认",
      PENDING_USAGE in _t, True)
check("F3", "清单表应说明这两列需要源码证据",
      "semantic_audit" in _t or "待确认" in _t, True)

# F4-F5 传入真实判定时应写入确定结论
_t2 = to_checklist_table(
    [_rec("fuzzywuzzy", "GPL-2.0-only")],
    {"fuzzywuzzy": {"使用方式": "修改源码 / 二次开发", "自主开发边界": "已二次开发"}})
check("F4", "传入语义判定后应写入真实的使用方式",
      "修改源码 / 二次开发" in _t2, True)
check("F5", "传入语义判定后应写入真实的自主开发边界",
      "已二次开发" in _t2, True)

# F6-F8 合规状态映射
check("F6", "许可证未识别的条目合规状态应为「待确认」",
      "待确认" in to_checklist_table([_rec("mystery", "UNKNOWN", conf="低")]), True)
check("F7", "元数据未找到的条目合规状态应为「未找到」",
      "未找到" in to_checklist_table([_rec("ghost", "UNKNOWN", status="NOT_FOUND", conf="无")]), True)
check("F8", "抓取失败的条目合规状态应为「获取失败」",
      "获取失败" in to_checklist_table([_rec("flaky", "UNKNOWN", status="FETCH_ERROR", conf="无")]), True)

# F9 表结构自洽：表头列数应与分隔行列数一致
_lines = to_checklist_table([_rec("a", "MIT")]).splitlines()
check("F9", "清单表表头与分隔行列数应一致",
      _lines[0].count("|") == _lines[1].count("|"), True)


# ============================================================ G. 更多真实许可证写法

print("\nG. 更多真实许可证写法（补齐生态中实际出现的写法）")

# G1-G13 生态中常见、且此前已覆盖的写法（回归保护）
check("G1", '"Apache License, Version 2.0" → Apache-2.0',
      normalize_license("Apache License, Version 2.0"), "Apache-2.0")
check("G2", '"MPL 2.0" → MPL-2.0', normalize_license("MPL 2.0"), "MPL-2.0")
check("G3", '"Eclipse Public License 2.0" → EPL-2.0',
      normalize_license("Eclipse Public License 2.0"), "EPL-2.0")
check("G4", '"CC0 1.0 Universal" → CC0-1.0',
      normalize_license("CC0 1.0 Universal"), "CC0-1.0")
check("G5", '"ISC License" → ISC', normalize_license("ISC License"), "ISC")
check("G6", '"Python Software Foundation License" → PSF-2.0',
      normalize_license("Python Software Foundation License"), "PSF-2.0")
check("G7", '"zlib/libpng License" → Zlib', normalize_license("zlib/libpng License"), "Zlib")
check("G8", '"AGPL-3.0" → AGPL-3.0-only', normalize_license("AGPL-3.0"), "AGPL-3.0-only")
check("G9", '"LGPL-2.1" → LGPL-2.1-only', normalize_license("LGPL-2.1"), "LGPL-2.1-only")
check("G10", '"GPL-2.0+" 旧式加号 → GPL-2.0-or-later',
      normalize_license("GPL-2.0+"), "GPL-2.0-or-later")
check("G11", '"GNU Affero General Public License v3" → AGPL-3.0-only',
      normalize_license("GNU Affero General Public License v3"), "AGPL-3.0-only")
check("G12", '"BSD"（裸家族名）→ BSD-3-Clause',
      normalize_license("BSD"), "BSD-3-Clause")
check("G13", '"GNU General Public License v2.0" → GPL-2.0-only',
      normalize_license("GNU General Public License v2.0"), "GPL-2.0-only")
check("G14", '"LGPLv3"（无连字符）→ LGPL-3.0-only',
      normalize_license("LGPLv3"), "LGPL-3.0-only")
check("G15", '"GPLv2"（无连字符）→ GPL-2.0-only',
      normalize_license("GPLv2"), "GPL-2.0-only")

# G16-G24 v0.3 增补：以下写法在真实生态里都不少见，此前一律落入 UNKNOWN
check("G16", '"MIT/X11"（极常见写法）→ MIT',
      normalize_license("MIT/X11"), "MIT")
check("G17", '"The Unlicense"（带定冠词）→ Unlicense',
      normalize_license("The Unlicense"), "Unlicense")
check("G18", '"WTFPL" → WTFPL（宽松）',
      (normalize_license("WTFPL"), category_of("WTFPL")), ("WTFPL", "permissive"))
check("G19", '"Artistic-2.0"（Perl 生态）→ Artistic-2.0（宽松）',
      (normalize_license("Artistic-2.0"), category_of("Artistic-2.0")),
      ("Artistic-2.0", "permissive"))
check("G20", '"PostgreSQL" → PostgreSQL（宽松）',
      (normalize_license("PostgreSQL"), category_of("PostgreSQL")),
      ("PostgreSQL", "permissive"))
check("G21", '"Boost Software License 1.0" → BSL-1.0（宽松）',
      (normalize_license("Boost Software License 1.0"), category_of("BSL-1.0")),
      ("BSL-1.0", "permissive"))
check("G22", '"Mulan PSL v2"（木兰许可）→ MulanPSL-2.0（宽松）',
      (normalize_license("Mulan PSL v2"), category_of("MulanPSL-2.0")),
      ("MulanPSL-2.0", "permissive"))
check("G23", '"MulanPSL-2.0"（无空格写法）→ MulanPSL-2.0',
      normalize_license("MulanPSL-2.0"), "MulanPSL-2.0")
check("G24", '"CDDL-1.0" → CDDL-1.0（弱传染，文件级隔离）',
      (normalize_license("CDDL-1.0"), category_of("CDDL-1.0")),
      ("CDDL-1.0", "weak-copyleft"))
check("G25", '"EUPL-1.2" → EUPL-1.2（强传染）',
      (normalize_license("EUPL-1.2"), category_of("EUPL-1.2")),
      ("EUPL-1.2", "strong-copyleft"))

# G26-G28 把"实测事实"钉进回归集：
#        fuzzywuzzy 的 PyPI 元数据是 GPLv2（不是 GPL-3.0）。
#        这条事实曾被文档写错，现在由测试守住，避免再次回归。
_fuzzy_info = {
    "license_expression": None,
    "license": "GPLv2",
    "classifiers": ["License :: OSI Approved :: GNU General Public License v2 (GPLv2)"],
}
_got, _field, _conf = resolve_license_pypi(_fuzzy_info)
check("G26", "fuzzywuzzy 的真实元数据（GPLv2 classifier）应判定为 GPL-2.0-only",
      normalize_license(_got), "GPL-2.0-only")
check("G27", "fuzzywuzzy 的判定来源应为 trove classifier 且可信度为高",
      (_field, _conf), ("trove classifier", "高"))
check("G28", "fuzzywuzzy 归入强传染，MIT 项目引入应报高危",
      levels(detect_conflicts("MIT", [mk("fuzzywuzzy", normalize_license(_got))]),
             "fuzzywuzzy"), ["高"])

# G29-G30 复合表达式与类别判定的组合边界
check("G29", '"Apache-2.0 OR MIT" 两个宽松项应判定为宽松',
      category_of("Apache-2.0 OR MIT"), "permissive")
check("G30", '"MIT AND Apache-2.0" 两个宽松项应判定为宽松',
      category_of("MIT AND Apache-2.0"), "permissive")
check("G31", '"LGPL-3.0-only OR GPL-3.0-only" 应取最宽松项（弱传染）',
      category_of("LGPL-3.0-only OR GPL-3.0-only"), "weak-copyleft")
check("G32", '三组 OR："MIT OR GPL-2.0-only OR AGPL-3.0-only" 应取最宽松（宽松）',
      category_of("MIT OR GPL-2.0-only OR AGPL-3.0-only"), "permissive")
check("G33", '"GPL-2.0-only OR LGPL-2.1-only" 应取最宽松（弱传染）',
      category_of("GPL-2.0-only OR LGPL-2.1-only"), "weak-copyleft")
check("G34", "UNKNOWN 与已知项 OR 时应取已知项的类别",
      category_of("MIT OR SomeUnknownThing"), "permissive")

# G35 版本号暴露（报告里要能追溯到工具版本）
check("G35", "工具版本应为 0.4", VERSION, "0.4")


# ============================================================ J. monorepo 工作区内部依赖
# 来源：扫描 lobehub/lobe-chat 时实测。它的 package.json 里有 89 个依赖
# 版本写作 "workspace:*"，是 monorepo 内部包、不发布到 npm。
# 修复前这些包会被当成第三方依赖去查 npm：查不到 → 记「未识别」，
# 识别率被拉到 58.6%，还误报出 29 条风险项。修复后识别率 100%、误报 0。

check("J1", "workspace:* 应被识别为工作区内部包",
      split_workspace_deps([("@lobechat/agent-runtime", "workspace:*")]),
      ([], ["@lobechat/agent-runtime"]))

check("J2", "workspace:^ 也应识别为内部包",
      split_workspace_deps([("@lobechat/core", "workspace:^")]),
      ([], ["@lobechat/core"]))

check("J3", "普通版本范围是外部依赖，不应误判为内部包",
      split_workspace_deps([("express", "^4.18.0")]),
      (["express"], []))

# 关键：@scope/ 开头的包既可能是内部包也可能是真实发布的第三方包
# （@vercel/og、@anthropic-ai/sdk 都在 npm 上），按包名猜测必然出错，
# 只能依据 workspace 标记判断。
check("J4", "@scope 包带普通版本时必须是外部依赖（不能按名字猜）",
      split_workspace_deps([("@vercel/og", "^0.6.0")]),
      (["@vercel/og"], []))
check("J5", "@scope 包带精确版本时必须是外部依赖",
      split_workspace_deps([("@anthropic-ai/sdk", "0.32.1")]),
      (["@anthropic-ai/sdk"], []))

check("J6", "空版本说明符应视为外部依赖",
      split_workspace_deps([("left-pad", "")]), (["left-pad"], []))

check("J7", "混合清单应正确分离且保持原有顺序",
      split_workspace_deps([("axios", "^1.7.0"),
                            ("@lobechat/builtin-tools", "workspace:*"),
                            ("react", "18.3.1"),
                            ("@lobechat/types", "workspace:*")]),
      (["axios", "react"], ["@lobechat/builtin-tools", "@lobechat/types"]))

check("J8", "空清单不应报错", split_workspace_deps([]), ([], []))


# ============================================================ K. 许可证知识库扩容（v0.4）
# 来源：核对报告实测。仓库 LICENSE_DB 只收录 33 种，已造成真实漏判——
# zope.interface(ZPL-2.1)、arize-phoenix(Elastic-2.0)、
# todomvc-app-css(CC-BY-4.0) 的源站都给了明确许可证，工具却一律判 UNKNOWN。

from license_audit import (commercial_note, classify_unknown, unknown_breakdown,
                           effective_resolve_rate, tool_attributable_unknown,
                           CATEGORY_RANK, UNKNOWN_KINDS)

print("\nK. 许可证知识库扩容（v0.4：非 OSI / 源码可得许可与常见遗漏写法）")

# K1-K10 此前一律落 UNKNOWN 的真实案例
_K_CASES = [
    ("K1", "ZPL-2.1", "ZPL-2.1", "permissive"),
    ("K2", "Elastic-2.0", "Elastic-2.0", "source-available"),
    ("K3", "CC-BY-4.0", "CC-BY-4.0", "permissive"),
    ("K4", "CC-BY-SA-4.0", "CC-BY-SA-4.0", "strong-copyleft"),
    ("K5", "SSPL-1.0", "SSPL-1.0", "network-copyleft"),
    ("K6", "OFL-1.1", "OFL-1.1", "permissive"),
    ("K7", "MS-PL", "MS-PL", "permissive"),
    ("K8", "MS-RL", "MS-RL", "weak-copyleft"),
    ("K9", "MPL-1.1", "MPL-1.1", "weak-copyleft"),
    ("K10", "Unicode-DFS-2016", "Unicode-DFS-2016", "permissive"),
]
for cid, raw, want_spdx, want_cat in _K_CASES:
    check(cid, f"{raw} 应被识别且类别为 {want_cat}",
          (normalize_license(raw), category_of(normalize_license(raw))),
          (want_spdx, want_cat))

# K11-K14 同源前缀但条款相反的许可证必须区分开。
# BSL-1.0 是 Boost Software License（宽松），BSL-1.1 是 Business Source
# License（限制商业使用）。修复前 `BSL[- ]?1` 会把 1.1 一并判成宽松许可。
check("K11", "BSL-1.1 应判为源码可得（Business Source License）",
      category_of(normalize_license("BSL-1.1")), "source-available")
check("K12", "BSL-1.0 应判为宽松（Boost Software License）",
      category_of(normalize_license("BSL-1.0")), "permissive")
check("K13", "BUSL-1.1 与 BSL-1.1 同义",
      normalize_license("BUSL-1.1"), "BSL-1.1")
check("K14", "裸写 BSL-1 仍应归到 Boost（保持 v0.3 既有行为）",
      normalize_license("BSL-1"), "BSL-1.0")

# K15-K18 非 OSI 许可识别出来之后，必须能把商业风险说出来
check("K15", "Elastic-2.0 应给出商业限制提示",
      "商业" in (commercial_note("Elastic-2.0") or ""), True)
check("K16", "SSPL-1.0 应提示未获 OSI 认证",
      "OSI" in (commercial_note("SSPL-1.0") or ""), True)
check("K17", "MIT 这类普通宽松许可不应产生附加提示",
      commercial_note("MIT"), None)
check("K18", "UNKNOWN 不应产生附加提示", commercial_note("UNKNOWN"), None)

# K19-K22 源码可得许可必须能触发风险检出，不能因为"已识别"就静默放过
check("K19", "MIT 项目引入 Elastic-2.0 应报高危",
      levels(detect_conflicts("MIT", [mk("phoenix", "Elastic-2.0")]), "phoenix"),
      ["高"])
check("K20", "MIT 项目引入 BSL-1.1 应报高危",
      levels(detect_conflicts("MIT", [mk("busl", "BSL-1.1")]), "busl"), ["高"])
check("K21", "源码可得许可的风险说明里应点出限制内容",
      "限制" in "".join(f["reason"] for f in
                        detect_conflicts("MIT", [mk("p", "Elastic-2.0")])), True)
check("K22", "复合表达式中源码可得项应按最严格项判定",
      category_of("MIT AND Elastic-2.0"), "source-available")

# K23 v0.4 新增类别必须能参与排序，且 unknown 仍是最严的
check("K23", "新增类别后 unknown 仍排在最严位置",
      CATEGORY_RANK["unknown"] > CATEGORY_RANK["source-available"]
      > CATEGORY_RANK["network-copyleft"], True)


# ============================================================ L. 未识别项归因（v0.4）
# 来源：核对报告 3.2。此前所有"没认出来"统一记为 UNKNOWN 并计入未识别，
# 导致 97.8% 这个识别率同时惩罚了「工具无能」和「源站没数据」两种性质，
# 既不能指导改进也不能对外解释。现在按四种性质分开统计。

print("\nL. 未识别项归因（v0.4：让识别率这个数字可解释）")

check("L1", "源站无此包应归因 NOT_IN_REGISTRY",
      classify_unknown({"spdx": "UNKNOWN", "status": "NOT_FOUND",
                        "license_raw": ""}), "NOT_IN_REGISTRY")
check("L2", "源站有包但没填许可证应归因 NO_METADATA",
      classify_unknown({"spdx": "UNKNOWN", "status": "OK",
                        "license_raw": ""}), "NO_METADATA")
check("L3", "源站填了但知识库不认应归因 UNSUPPORTED_LICENSE",
      classify_unknown({"spdx": "UNKNOWN", "status": "OK",
                        "license_raw": "SomeWeirdLicense 1.0"}),
      "UNSUPPORTED_LICENSE")
check("L4", "网络失败应归因 FETCH_FAILED（重跑即可，不是许可证问题）",
      classify_unknown({"spdx": "UNKNOWN", "status": "FETCH_ERROR",
                        "license_raw": ""}), "FETCH_FAILED")
check("L5", "已识别的记录不应有归因",
      classify_unknown({"spdx": "MIT", "status": "OK", "license_raw": "MIT"}), None)

_L_RECS = [
    {"spdx": "MIT", "status": "OK", "license_raw": "MIT"},
    {"spdx": "UNKNOWN", "status": "NOT_FOUND", "license_raw": ""},
    {"spdx": "UNKNOWN", "status": "OK", "license_raw": ""},
    {"spdx": "UNKNOWN", "status": "OK", "license_raw": "ZPL-9.9"},
]
check("L6", "四分类计数应正确", unknown_breakdown(_L_RECS),
      {"NOT_IN_REGISTRY": 1, "NO_METADATA": 1,
       "UNSUPPORTED_LICENSE": 1, "FETCH_FAILED": 0})
check("L7", "只有知识库未收录一类归因于工具自身",
      tool_attributable_unknown(_L_RECS), 1)
check("L8", "剔除源站无数据后的识别率应高于原始识别率",
      effective_resolve_rate(_L_RECS) > 100.0 * 1 / 4, True)
check("L9", "归因类别应全部有中文名与处理建议",
      all(k in UNKNOWN_KINDS for k in unknown_breakdown(_L_RECS)), True)
check("L10", "空记录集不应报错", unknown_breakdown([]),
      {k: 0 for k in UNKNOWN_KINDS})

# L11-L12 归因要真正影响给使用者的建议，而不只是统计数字
_f = detect_conflicts("MIT", [{"name": "ghost", "source": "PyPI", "version": "?",
                               "license_raw": "", "spdx": "UNKNOWN",
                               "license_field": "无", "confidence": "无",
                               "deps": [], "status": "NOT_FOUND"}])
check("L11", "源站无此包的处理建议应指向核对包名，而不是泛泛的人工核对",
      "私有包" in "".join(x["advice"] for x in _f), True)
_f = detect_conflicts("MIT", [{"name": "weird", "source": "PyPI", "version": "1.0",
                               "license_raw": "ZPL-9.9", "spdx": "UNKNOWN",
                               "license_field": "license_expression",
                               "confidence": "高", "deps": [], "status": "OK"}])
check("L12", "知识库未收录的处理建议应指向补充知识库",
      "知识库" in "".join(x["advice"] for x in _f), True)


# ============================================================ M. 跨 Python 版本一致性（v0.4）
# 来源：CI。补回 split_workspace_deps 之后测试终于能跑起来，第一次全矩阵执行
# 就暴露出两个此前被 ImportError 掩盖的既有缺陷：
#   · Windows 三个 job：控制台默认 cp1252，打印中文直接 UnicodeEncodeError
#   · Python 3.8 三个 job：无 tomllib（3.11 才进标准库），pyproject 走正则降级，
#     丢版本约束、还把 `name = "demo"` 当成包名（C1/C3/E13）
# 这两个都不是本轮引入的，但既然暴露了就一并修掉。

from license_audit import (_toml_loads, _pyproject_deps, _force_utf8_stdio,
                           parse_pyproject_verbose)

print("\nM. 跨 Python 版本一致性（v0.4：Windows 编码 / 无 tomllib 降级）")

_M_CASES = {
    "pep621": """
[project]
name = "demo"
dependencies = [
  "requests>=2.31.0",
  "pandas",
  "pymupdf>=1.24",
]

[project.optional-dependencies]
dev = ["pytest>=7", "ruff"]
""",
    "poetry": """
[tool.poetry.dependencies]
python = "^3.10"
flask = "^3.0"
mysqlclient = "*"

[tool.poetry.group.dev.dependencies]
black = "^24.0"
""",
    "pep735": """
[dependency-groups]
test = ["pytest", "coverage"]
docs = ["mkdocs"]
""",
    # 内联表与行尾注释是真实 pyproject.toml 里最常见的两种写法
    "inline": """
[tool.poetry.dependencies]
python = "^3.10"
flask = { version = "^3.0", optional = true }   # 内联表
requests = ">=2.0"   # 行尾注释带 # 号
""",
}

# 期望结果（由高版本 tomllib 实测产出，作为无 tomllib 时的对照基准）
_M_EXPECT = {
    "pep621": [("pandas", ""), ("pymupdf", ">=1.24"), ("pytest", ">=7"),
               ("requests", ">=2.31.0"), ("ruff", "")],
    "poetry": [("black", "^24.0"), ("flask", "^3.0"), ("mysqlclient", "*")],
    "pep735": [("coverage", ""), ("mkdocs", ""), ("pytest", "")],
    "inline": [("flask", "^3.0"), ("requests", ">=2.0")],
}

# M1-M4 内置解析器必须与 tomllib 给出完全一致的结果，
# 否则同一份 pyproject.toml 在不同 Python 版本上会得到不同结论。
# 注意：tomllib 是 3.11 才有的，这里必须兼容 3.8——否则整个测试文件
# 在 3.8 上会因 import 失败而崩掉，正是我们要避免的那类问题。
try:
    import tomllib as _tomllib
except ImportError:                      # Python < 3.11
    _tomllib = None

for _i, (_name, _txt) in enumerate(_M_CASES.items(), start=1):
    if _tomllib is not None:
        check(f"M{_i}", f"内置 TOML 解析应与 tomllib 一致（{_name}）",
              _pyproject_deps(_toml_loads(_txt)),
              _pyproject_deps(_tomllib.loads(_txt)))
    else:
        # 无 tomllib 时（Python < 3.11）仍必须与高版本结论一致，
        # 基准值取自上表，同样是 tomllib 实测产出。
        check(f"M{_i}", f"无 tomllib 时内置解析应与基准一致（{_name}）",
              _pyproject_deps(_toml_loads(_txt)), _M_EXPECT[_name])

# M5 必须保留版本约束——旧正则降级全部返回空串，导致按锁定版本查询失效
check("M5", "无 tomllib 时 Poetry 版本约束仍应保留",
      _pyproject_deps(_toml_loads(_M_CASES["poetry"])),
      [("black", "^24.0"), ("flask", "^3.0"), ("mysqlclient", "*")])

# M6 不能把 `name = "demo"` 这类非依赖键当包名（旧降级会误收 "name"）
check("M6", "解析结果不应混入非依赖键",
      "name" in [n for n, _s in _pyproject_deps(_toml_loads(_M_CASES["pep621"]))],
      False)

# M7 注释里的 # 号不应被当成值的一部分
check("M7", "行尾注释应被正确剥离",
      dict(_pyproject_deps(_toml_loads(_M_CASES["inline"])))["requests"], ">=2.0")

# M8-M9 UTF-8 输出守卫
check("M8", "应提供强制 UTF-8 输出的守卫函数", callable(_force_utf8_stdio), True)
check("M9", "守卫执行后 stdout 编码应为 UTF-8（Windows 控制台不再崩）",
      (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", ""),
      "utf8")


# ============================================================ 汇总

print("\n" + "=" * 60)
print(f"用例总数 {PASS + FAIL}　通过 {PASS}　失败 {FAIL}")
if FAILURES:
    print("\n失败用例：")
    for cid, desc, got, want in FAILURES:
        print(f"  [{cid}] {desc}\n      期望 {want}\n      实际 {got}")
print("=" * 60)
sys.exit(1 if FAIL else 0)
