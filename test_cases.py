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

from license_audit import (normalize_license, normalize_single, category_of,
                           resolve_license_pypi, detect_conflicts)

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
check("A8", "自然语言中的 \" or \" 不得被当作 SPDX 的 OR 运算符拆分",
      normalize_license(got), "LGPL-3.0-only")

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


# ============================================================ 汇总

print("\n" + "=" * 60)
print(f"用例总数 {PASS + FAIL}　通过 {PASS}　失败 {FAIL}")
if FAILURES:
    print("\n失败用例：")
    for cid, desc, got, want in FAILURES:
        print(f"  [{cid}] {desc}\n      期望 {want}\n      实际 {got}")
print("=" * 60)
sys.exit(1 if FAIL else 0)
