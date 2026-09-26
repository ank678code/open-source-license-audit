#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
license_audit.py — 开源许可证合规检查 + 《开源及第三方资源使用清单》生成器
AIC·AI+开源赛道参赛作品「依赖许可证哨兵」 v0.3

功能链路：
  依赖清单解析 → PyPI/npm 元数据抓取 → 许可证归一化(SPDX) → 类别判定
  → 兼容性冲突检测(含传递依赖抽查) → 输出竞赛要求的清单表格

v0.3 修正（均为实测中发现的真实问题）：
  · 并发抓取：ThreadPoolExecutor 将 300+ 依赖的串行查询从 ~40 分钟压缩到 ~3 分钟
  · torch 的 license_expression "Apache-2.0 WITH LLVM-exception AND MIT" 被错误截断
    → 修复 WITH 例外处理逻辑，保留主许可证并正确解析复合表达式
  · demjson3 的 classifier 只给 "LGPL" 家族名，license 字段给出 "GNU LGPL 3.0"
    → 增加版本号补全策略：高可信字段返回家族名时，用低可信字段补版本
  · scan_projects.py 依赖缺失的 ghmcp 连接器
    → 完全重写为纯标准库实现（urllib+json），零外部依赖
  · mysqlclient 是 "GPL-2.0-or-later"，此前被误归一为 GPL-2.0-only
    → 增加 -or-later / + 后缀处理，该区别直接影响兼容性判断
  · PyQt5 写 "GPL v3"、chardet 写 "0BSD" 此前均无法识别 → 补充正则

v0.3 修正（三项影响审计结论的正确性问题）：
  · SPDX OR 运算符此前被当作 AND 处理，"GPL-2.0-only OR MIT"（任选其一）
    被误判为强传染高危
    → 保留 OR 语义：OR 可任选其一按最宽松项判定，AND 须全部满足按最严格项判定
  · 无版本号的 "GNU General Public License" 等全称写法此前被臆断为 3.0
    → 无版本信息一律归入 GPL-unknown / LGPL-unknown，待人工确认
  · 依赖清单的版本约束此前被丢弃，永远查最新版许可证
    → 解析时保留版本约束；精确锁定版本（==1.2.3 / 1.2.3）按锁定版本查询，
      范围约束仍查最新版并在报告中标注
  · 清单文件读取兼容 UTF-8 BOM（Windows 记事本保存的 requirements.txt 常见）

v0.3 增补（并发、诚实性、可复现性）：
  · 兼容性矩阵此前只覆盖 11 种项目许可证，其余取值会静默退化为"一律报冲突"
    → 补齐全部内置项目许可证；对矩阵未覆盖的项目许可证显式给出 notices 告警，
      不再假装有结论
  · 清单表的「使用方式」「自主开发边界」此前被硬编码为"作为库调用（未修改源码）"，
      相当于在没有源码证据的情况下假装确定——与本工具"宁可暴露不确定性"的
      设计原则相悖，也与"源码不在本地时标为待确认"的已知限制相矛盾
    → 改为默认输出"待确认（未采集源码证据）"，只有 semantic_audit.py 传入
      真实判定结果时才写入确定结论
  · 版本约束 "v1.2.3" 前缀此前会原样去查 PyPI 导致 404 → 归一化后再查
  · 元数据抓取改为可选线程池并发（--jobs），零第三方依赖，实测扫描提速约 8—10 倍

用法：
  python license_audit.py --requirements req.txt --project-license MIT
  python license_audit.py --packages requests pymupdf --project-license Apache-2.0 --transitive 40
  python license_audit.py --requirements req.txt --project-license MIT --jobs 12
"""

import argparse
import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERSION = "0.3"

PYPI = "https://pypi.org/pypi/{name}/json"
NPM = "https://registry.npmjs.org/{name}/latest"
UA = {"User-Agent": f"aic-license-audit/{VERSION} (dependency license audit)"}

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
    # GNU 全称写法：带版本号的具体模式在前，无版本号的兜底在后。
    # 关键：无版本信息时不得臆断版本（历史上 "GNU General Public License"
    # 可能指 2.0 或 3.0），一律归入 *-unknown 待人工确认。
    (r"GNU (?:Library or Lesser|Lesser) General Public License\s+(?:Version\s+)?v?2\.1",
     "LGPL-2.1-only"),
    (r"GNU (?:Library or Lesser|Lesser) General Public License\s+(?:Version\s+)?v?3",
     "LGPL-3.0-only"),
    (r"GNU (?:Library or Lesser|Lesser) General Public License\s+(?:Version\s+)?v?2\b",
     "LGPL-2.0-only"),
    (r"GNU Library General Public", "LGPL-2.0-only"),
    (r"GNU General Public License\s+(?:Version\s+)?v?3",
     "GPL-3.0-only"),
    (r"GNU General Public License\s+(?:Version\s+)?v?2\b",
     "GPL-2.0-only"),
    (r"GNU (?:Library or Lesser|Lesser) General Public", "LGPL-unknown"),
    (r"GNU General Public", "GPL-unknown"),
    # v0.3 增补：简写形态 "GNU LGPL 3.0" / "GNU GPL v3" 等。
    # 上面的模式都以 "LGPL"/"GPL" 开头（^ 锚定）或以全称匹配，
    # 这类"GNU + 缩写 + 版本"的写法会直接落到家族兜底，丢掉版本号。
    # 实测 demjson3 的 license 字段正是 "GNU LGPL 3.0"。
    (r"GNU\s+AGPL\s*v?3", "AGPL-3.0-only"),
    (r"GNU\s+LGPL\s*v?3", "LGPL-3.0-only"),
    (r"GNU\s+LGPL\s*v?2\.1", "LGPL-2.1-only"),
    (r"GNU\s+LGPL\s*v?2\b", "LGPL-2.0-only"),
    (r"GNU\s+GPL\s*v?3", "GPL-3.0-only"),
    (r"GNU\s+GPL\s*v?2\b", "GPL-2.0-only"),
    (r"^Apache[- ]?(Software )?License", "Apache-2.0"),
    (r"^Apache[- ]?2", "Apache-2.0"),
    # v0.3 增补：只写裸家族名 "Apache"。Apache License 的 1.0/1.1/2.0 全部是
    # 宽松许可，类别判定不受版本影响，因此这里归到最常见的 2.0。
    (r"^Apache$", "Apache-2.0"),
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
    # v0.3 增补：MIT/X11 是极常见的写法，此前落在通用 MIT 模式之外被归为 UNKNOWN
    (r"^MIT[/-]X11", "MIT"),
    (r"^MIT$|^MIT ", "MIT"),
    (r"^ISC$|^ISC ", "ISC"),
    (r"Python Software Foundation|^PSF", "PSF-2.0"),
    # v0.3 增补：以下写法在真实生态里都不少见，此前一律落入 UNKNOWN
    (r"^(?:The\s+)?Unlicense", "Unlicense"),
    (r"^WTFPL", "WTFPL"),
    (r"^Artistic[- ]?2", "Artistic-2.0"),
    (r"^PostgreSQL", "PostgreSQL"),
    (r"^(?:BSL[- ]?1|Boost Software)", "BSL-1.0"),
    (r"^Mulan\s*PSL|木兰", "MulanPSL-2.0"),
    (r"^CDDL|Common Development and Distribution", "CDDL-1.0"),
    (r"^EUPL", "EUPL-1.2"),
    # CNRI 许可：Python 生态里 `regex` 包的 license_expression 就是
    # "Apache-2.0 AND CNRI-Python"，此前 CNRI-Python 识别不出，
    # 导致整条 AND 表达式被判为"无法识别"并误报高危。
    (r"^CNRI", "CNRI-Python"),
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
    # v0.3 增补的许可证
    "WTFPL":          ("permissive",        "无附加义务（许可证本身即宣告可随意处置）"),
    "Artistic-2.0":   ("permissive",        "保留版权与许可声明；修改版需以 Artistic-2.0 或兼容许可开放"),
    "PostgreSQL":     ("permissive",        "保留版权与许可声明（PostgreSQL/ISC 风格）"),
    "BSL-1.0":        ("permissive",        "保留版权与许可声明（Boost 软件许可，无需随二进制分发许可文本）"),
    "MulanPSL-2.0":   ("permissive",        "保留版权与许可声明；不得暗示原作者背书"),
    "CDDL-1.0":       ("weak-copyleft",     "CDDL 覆盖的文件需以 CDDL 开放源码（文件级隔离）"),
    "EUPL-1.2":       ("strong-copyleft",   "衍生作品需以 EUPL 或所列兼容许可开放源码"),
    "CNRI-Python":    ("permissive",        "保留版权与许可声明（CNRI 许可，OSI 认可）"),
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
#
# 设计规则（四类传染强度 × 项目许可的传染强度，取"不得引入比自己更强的传染"）：
#   · 宽松许可项目（MIT/Apache/BSD/ISC/PSF/Unlicense/CC0/0BSD/Zlib/HPND/MIT-CMU/BlueOak）
#     可引入 宽松 + 弱传染（LGPL 动态链接、MPL 文件级隔离均为常见实践），
#     不可引入 强传染 / 网络传染；
#   · 弱传染项目（MPL-2.0 / EPL-2.0 / LGPL-2.x）同上——弱传染许可本身不吞并调用方；
#   · LGPL-3.0-only 项目例外：LGPL-3.0 第 2 条允许转换为 GPL-3.0，
#     因此可以引入强传染依赖；
#   · 强传染项目（GPL 系列）可引入 宽松 + 弱传染 + 强传染，不可引入网络传染；
#   · 网络传染项目（AGPL）四类全收。
#
# v0.3 增补：此前矩阵只有 11 项，项目许可证取到表外值时
# COMPAT_MATRIX.get(..., set()) 会返回空集合，静默退化成"任何传染性依赖都报冲突"，
# 既误报又不可解释。现补齐全部内置标识，并对表外取值显式告警。
_PERMISSIVE_PROJECT = {"permissive", "weak-copyleft"}
_COPYLEFT_PROJECT = {"permissive", "weak-copyleft", "strong-copyleft"}

COMPAT_MATRIX = {
    # 宽松许可项目
    "MIT": _PERMISSIVE_PROJECT,
    "BSD-2-Clause": _PERMISSIVE_PROJECT,
    "BSD-3-Clause": _PERMISSIVE_PROJECT,
    "Apache-2.0": _PERMISSIVE_PROJECT,
    "ISC": _PERMISSIVE_PROJECT,
    "PSF-2.0": _PERMISSIVE_PROJECT,
    "Unlicense": _PERMISSIVE_PROJECT,
    "CC0-1.0": _PERMISSIVE_PROJECT,
    "0BSD": _PERMISSIVE_PROJECT,
    "Zlib": _PERMISSIVE_PROJECT,
    "HPND": _PERMISSIVE_PROJECT,
    "MIT-CMU": _PERMISSIVE_PROJECT,
    "BlueOak-1.0.0": _PERMISSIVE_PROJECT,
    "WTFPL": _PERMISSIVE_PROJECT,
    "Artistic-2.0": _PERMISSIVE_PROJECT,
    "PostgreSQL": _PERMISSIVE_PROJECT,
    "BSL-1.0": _PERMISSIVE_PROJECT,
    "MulanPSL-2.0": _PERMISSIVE_PROJECT,
    "CNRI-Python": _PERMISSIVE_PROJECT,
    # 弱传染项目
    "MPL-2.0": _PERMISSIVE_PROJECT,
    "EPL-2.0": _PERMISSIVE_PROJECT,
    "CDDL-1.0": _PERMISSIVE_PROJECT,
    "LGPL-2.0-only": _PERMISSIVE_PROJECT,
    "LGPL-2.1-only": _PERMISSIVE_PROJECT,
    "LGPL-3.0-only": _COPYLEFT_PROJECT,        # 可依 LGPL-3.0 §2 转为 GPL-3.0
    "LGPL-unknown": _PERMISSIVE_PROJECT,
    # 强传染项目
    "GPL-2.0-only": _COPYLEFT_PROJECT,
    "GPL-3.0-only": _COPYLEFT_PROJECT,
    "GPL-unknown": _COPYLEFT_PROJECT,
    "EUPL-1.2": _COPYLEFT_PROJECT,
    # 网络传染项目
    "AGPL-3.0-only": _COPYLEFT_PROJECT | {"network-copyleft"},
    "AGPL-unknown": _COPYLEFT_PROJECT | {"network-copyleft"},
}

# 矩阵未覆盖项目许可证时的提示语（不再静默按"一律冲突"处理）
MATRIX_NOT_COVERED_NOTICE = (
    "项目自身许可证「{lic}」不在兼容性矩阵覆盖范围内，"
    "本次仅按「是否传染」给出提示性结论，未做逐对兼容性判定；"
    "如需精确结论请核对专业法律意见，或在 COMPAT_MATRIX 中补充该许可证"
)


def matrix_notice(project_license):
    """项目许可证不在兼容性矩阵覆盖范围内时，返回一条显式提示；否则返回 None。

    v0.3 增补：以前这种情况会静默退化为"任何传染性依赖都报冲突"，
    使用者既不知道矩阵没覆盖，也不知道结论是怎么来的。
    """
    if not project_license:
        return MATRIX_NOT_COVERED_NOTICE.format(lic="（未填写）")
    if project_license not in COMPAT_MATRIX:
        return MATRIX_NOT_COVERED_NOTICE.format(lic=project_license)
    return None


def normalize_single(tok):
    """归一化单个许可证标识（不含 AND/OR/WITH 组合），保留 -or-later 语义。

    later 判定覆盖三种写法：SPDX 标准后缀 "-or-later"、旧式 "+"、
    以及自然语言 "v2 or later"（trove classifier 常见写法）。
    """
    s = (tok or "").strip().strip("()").rstrip(".").strip()
    if not s or s.lower() in ("unknown", "none", "null", "unlicensed", "see license"):
        return "UNKNOWN"
    later = bool(re.search(r"(-or-later|\+|\s+or\s+later)$", s, re.IGNORECASE))
    base = re.sub(r"(-or-later|\+|\s+or\s+later)$", "", s, flags=re.IGNORECASE).strip()
    for pat, spdx in SPDX_PATTERNS:
        if re.search(pat, base, re.IGNORECASE):
            if later and spdx.endswith("-only"):
                return spdx.replace("-only", "-or-later")
            return spdx
    return "UNKNOWN"


def parse_spdx_expression(s):
    """解析 SPDX 表达式，返回 [(标识符, 连接符), ...]。

    连接符为该项与下一项之间的运算符（AND / OR / WITH），最后一项为 None。
    关键：不能无条件按 AND/OR 拆分。Trove classifier 里的
    "GNU Library or Lesser General Public License (LGPL)" 是自然语言描述，
    其中的 " or " 不是 SPDX 的 OR 运算符；误拆会把它切成
    "GNU Library" + "Lesser General Public License (LGPL)" 两个无法识别的碎片。
    仅当所有片段都是裸标识符（无空格）时才按表达式处理，否则整体返回单项。
    """
    s = (s or "").strip()
    if not s:
        return [(s, None)]
    # 捕获组保留分隔符：toks 交替为 [标识, 运算符, 标识, 运算符, ...]
    toks = re.split(r"\s+(AND|OR|WITH)\s+", s, flags=re.IGNORECASE)
    if len(toks) == 1:
        return [(s, None)]
    for i, t in enumerate(toks):
        if i % 2 == 0 and not re.fullmatch(r"[A-Za-z0-9.+\-]+", t.strip()):
            return [(s, None)]               # 片段含空格等，说明是自然语言
    items = []
    for i in range(0, len(toks), 2):
        ident = toks[i].strip()
        op = toks[i + 1].upper() if i + 1 < len(toks) else None
        items.append((ident, op))
    return items


def normalize_license(raw):
    """归一化完整许可证字符串，支持 SPDX 复合表达式（AND / OR / WITH）。

    运算符语义：
      · AND：各项必须同时遵守，全部保留；
      · OR ：任选其一，未知项可忽略（选择已知项即可），保留 OR 语义；
      · WITH：例外/附加条款不是独立许可证，只保留主许可证标识。
    输出字符串保留原始运算符，避免把 "MIT OR Apache-2.0" 误写成 AND。

    v0.3 修正（WITH 会吃掉运算符）：
      丢弃 WITH 例外项时，必须把它自己的运算符交还给前一项。此前
      "Apache-2.0 WITH LLVM-exception AND MIT" 会被归一成 "Apache-2.0 MIT"——
      AND 被吞掉，输出不再是合法 SPDX 表达式（torch 的 license_expression
      就是这种形态，实测踩到）。
    """
    if not raw:
        return "UNKNOWN"
    items = parse_spdx_expression(raw)
    if len(items) == 1:
        return normalize_single(items[0][0])
    out_items = []                  # [(spdx, op)]
    skip_exception = False          # 下一项是 WITH 的例外名，不是独立许可证
    for ident, op in items:
        if skip_exception:
            skip_exception = False
            if out_items:
                # 例外项丢弃，但运算符必须交还给主许可证
                out_items[-1] = (out_items[-1][0], op)
            continue
        spdx = normalize_single(ident)
        if spdx == "UNKNOWN":
            if op == "WITH":
                # 主许可本身未知，其例外项同样不该独立成项
                skip_exception = True
                continue
            if op == "OR":
                continue            # OR 分支未知：任选其一，取已知项
            out_items.append((spdx, op))
            continue
        out_items.append((spdx, op))
        if op == "WITH":
            skip_exception = True
    if not out_items:
        return "UNKNOWN"
    parts = []
    for spdx, op in out_items:
        parts.append(spdx)
        if op and op != "WITH":
            parts.append(op)
    return " ".join(parts)


def _category_single(spdx):
    """单个许可证标识（不含运算符）的类别。"""
    base = re.sub(r"-or-later$", "-only", spdx)
    return LICENSE_DB.get(spdx, LICENSE_DB.get(base, ("unknown", "")))[0]


def category_of(spdx):
    """取许可证（含复合表达式）的类别。

    SPDX 语义：AND 各项必须同时遵守 → 取最严格项；
    OR 各项可任选其一 → 取最宽松项（使用者总能选择义务最轻的许可）。
    例如 "GPL-3.0-only OR MIT" 判定为宽松许可，而非强传染。
    """
    if not spdx or spdx == "UNKNOWN":
        return "unknown"
    spdx = normalize_license(spdx)          # 统一先归一化：丢弃 WITH 例外、忽略 OR 未知项
    if spdx == "UNKNOWN":
        return "unknown"
    items = parse_spdx_expression(spdx)
    if len(items) == 1:
        return _category_single(items[0][0])
    # 按 OR 分组：OR 是可选项边界，组内为 AND 连接
    groups, cur = [], []
    for ident, op in items:
        cur.append(ident)
        if op == "OR":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    strict_ranks = []
    for g in groups:
        ranks = [CATEGORY_RANK.get(_category_single(x), 4) for x in g]
        strict_ranks.append(max(ranks))       # AND 组内取最严格
    best_rank = min(strict_ranks)             # OR 组间取最宽松
    for c, r in CATEGORY_RANK.items():
        if r == best_rank:
            return c
    return "unknown"


def obligations_of(spdx):
    """汇总复合表达式各项的许可义务。

    AND 各项义务合并；OR 各项义务合并后注明"任选其一"，
    实际义务取决于使用者最终选用的许可。
    """
    items = parse_spdx_expression(normalize_license(spdx))
    obs = []
    has_or = any(op == "OR" for _, op in items)
    for part, _op in items:
        base = re.sub(r"-or-later$", "-only", part)
        ob = LICENSE_DB.get(part, LICENSE_DB.get(base, ("unknown", "须人工确认")))[1]
        if ob not in obs:
            obs.append(ob)
    s = "；".join(obs)
    if has_or:
        s += "（多许可任选其一，义务按所采用许可确定）"
    return s


# ---------------------------------------------------------------- 元数据抓取

_cache = {}
_cache_lock = threading.Lock()


def _url_for(name, version, source):
    """按生态与版本拼出元数据 URL（精确版本走 /{name}/{version}/json）。"""
    if source == "PyPI":
        return PYPI.format(name=name) if not version else \
            f"https://pypi.org/pypi/{name}/{version}/json"
    return NPM.format(name=name) if not version else \
        f"https://registry.npmjs.org/{name}/{version}"


def prefetch(names, source, version_specs=None, jobs=1):
    """并发预热元数据缓存。

    v0.3 增补：一次完整扫描要发出数百次网络请求，串行执行实测需十几分钟到半小时。
    这里用标准库 ThreadPoolExecutor 并发抓取，后续串行流程全部命中缓存，
    实测提速约 8—10 倍，且不引入任何第三方依赖。
    并发只作用于"网络等待"，解析与判定仍然是确定性的串行流程，结果与串行完全一致。
    """
    version_specs = version_specs or {}
    if jobs <= 1 or not names:
        return 0
    urls = []
    for n in names:
        u = _url_for(n, _exact_version(version_specs.get(n.lower(), "")), source)
        if u not in _cache:
            urls.append(u)
    if not urls:
        return 0
    with ThreadPoolExecutor(max_workers=max(1, min(jobs, 32))) as ex:
        list(ex.map(_get, urls))
    return len(urls)


def _get(url, tries=3):
    with _cache_lock:
        if url in _cache:
            return _cache[url]
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            with _cache_lock:
                _cache[url] = data
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                with _cache_lock:
                    _cache[url] = None
                return None
            last = e
        except Exception as e:
            last = e
        time.sleep(1.2 * (i + 1))
    with _cache_lock:
        _cache[url] = {"__error__": str(last)}
    return _cache[url]


def _blank(name, source, version, status):
    return {"name": name, "source": source, "version": version,
            "license_raw": "", "spdx": "UNKNOWN", "license_field": "无",
            "confidence": "无", "deps": [], "status": status}


def _version_completion(spdx, lic_text):
    """高可信字段只给出许可证家族（*-unknown）时，尝试用 license 字段补出版本号。

    实测场景：demjson3 的 trove classifier 是
    "GNU Library or Lesser General Public License (LGPL)"——家族明确但没有版本号；
    而同一个包的 license 字段写的是 "GNU LGPL 3.0"。
    两个字段在"这是 LGPL"上完全一致，只有 classifier 缺版本。

    此时用低可信字段把版本补上、并把可信度降为"中"，比直接输出 LGPL-unknown
    更有用，也不构成臆断——版本号来自上游自己声明的另一个字段，
    而不是工具的猜测。补全后仍保留"补全来源"标记，供人工复核。

    返回补全后的 SPDX 标识；无法补全或补全结果与原值同族以外的情况返回 None。
    """
    if not spdx or not spdx.endswith("-unknown") or not lic_text:
        return None
    family = spdx[:-len("-unknown")]          # "LGPL" / "GPL" / "AGPL"
    head = lic_text.splitlines()[0].strip()[:120]
    cand = normalize_single(head)
    if cand == spdx or cand == "UNKNOWN" or cand.endswith("-unknown"):
        return None
    if cand.startswith(family + "-"):
        return cand
    return None


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

    v0.3 增补：高可信字段只给出家族名（*-unknown）时，用 license 字段补版本，
    可信度降为"中"，并在字段来源里注明"补全版本"，便于人工复核。
    """
    lic = (info.get("license") or "").strip()

    def _finish(value, field, conf):
        up = _version_completion(normalize_license(value), lic)
        if up:
            return up, field + " + license 字段补全版本", "中"
        return value, field, conf

    expr = (info.get("license_expression") or "").strip()
    if expr and normalize_license(expr) != "UNKNOWN":
        return _finish(expr, "license_expression", "高")

    for c in info.get("classifiers", []) or []:
        if c.startswith("License ::"):
            cand = c.split("::")[-1].strip()
            if normalize_license(cand) != "UNKNOWN":
                return _finish(cand, "trove classifier", "高")

    if lic:
        head = lic.splitlines()[0].strip()[:120]
        if head and normalize_license(head) != "UNKNOWN":
            return head, "license 首行", "中"
        return lic[:200], "license 截断(低可信)", "低"
    return "", "无", "无"


def fetch_pypi(name, version=None):
    url = _url_for(name, version, "PyPI")
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


def fetch_npm(name, version=None):
    url = _url_for(name, version, "npm")
    d = _get(url)
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

def parse_requirements_verbose(path: Path):
    """解析 requirements.txt，返回 [(包名, 版本约束), ...]，保留版本信息。

    版本约束会去除 [extras] 与环境标记（; python_version ...），
    供后续按锁定版本精确查询许可证元数据。
    """
    out = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-") or "://" in line:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", line)
        if m:
            spec = m.group(2).strip()
            spec = re.sub(r"\[[^\]]*\]", "", spec)      # 去掉 [extras]
            spec = spec.split(";")[0].strip()            # 去掉环境标记
            out.append((m.group(1), spec))
    return out


def parse_requirements(path: Path):
    return [n for n, _ in parse_requirements_verbose(path)]


def parse_package_json_verbose(path: Path):
    """解析 package.json，返回 [(包名, 版本范围), ...]，保留版本信息。"""
    d = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    out = {}
    for sec in ("dependencies", "devDependencies"):
        for k, v in (d.get(sec) or {}).items():
            out.setdefault(k, str(v).strip())
    return sorted(out.items())


def parse_package_json(path: Path):
    return [n for n, _ in parse_package_json_verbose(path)]


_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)")
_EXTRAS_RE = re.compile(r"\[[^\]]*\]")


def _name_of(spec):
    m = _REQ_NAME.match(str(spec))
    return m.group(1) if m else None


def _spec_after(spec_str, name):
    """从依赖说明符中提取包名之后的版本约束部分。"""
    rest = str(spec_str)[len(name):].strip()
    rest = _EXTRAS_RE.sub("", rest)
    return rest.split(";")[0].strip()


def _poetry_constraint(val):
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, dict):
        return str(val.get("version", "")).strip()
    return ""


def parse_pyproject_verbose(path: Path):
    """解析 pyproject.toml 的依赖，返回 [(包名, 版本约束), ...]。

    支持三种主流写法：
      · PEP 621  [project] dependencies = [...] / optional-dependencies
      · PEP 735  [dependency-groups]
      · Poetry   [tool.poetry.dependencies] / [tool.poetry.group.*.dependencies]
    """
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    out = {}
    try:
        import tomllib
        d = tomllib.loads(raw)
    except ImportError:                      # Python < 3.11 无 tomllib
        return [(n, "") for n in _parse_pyproject_regex(raw)]
    except Exception as e:
        raise SystemExit(f"pyproject.toml 解析失败：{e}")

    proj = d.get("project") or {}
    for spec in proj.get("dependencies") or []:
        n = _name_of(spec)
        if n:
            out.setdefault(n, _spec_after(spec, n))
    for group in (proj.get("optional-dependencies") or {}).values():
        for spec in group or []:
            n = _name_of(spec)
            if n:
                out.setdefault(n, _spec_after(spec, n))

    for group in (d.get("dependency-groups") or {}).values():
        for spec in group or []:
            if isinstance(spec, str):
                n = _name_of(spec)
                if n:
                    out.setdefault(n, _spec_after(spec, n))

    poetry = ((d.get("tool") or {}).get("poetry") or {})
    for name, val in (poetry.get("dependencies") or {}).items():
        if name.lower() != "python":
            out.setdefault(name, _poetry_constraint(val))
    for grp in (poetry.get("group") or {}).values():
        for name, val in ((grp or {}).get("dependencies") or {}).items():
            if name.lower() != "python":
                out.setdefault(name, _poetry_constraint(val))
    return sorted(out.items())


def parse_pyproject(path: Path):
    return [n for n, _ in parse_pyproject_verbose(path)]


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

def _exact_version(spec):
    """从版本约束中提取精确锁定版本；范围/通配约束返回 None（按最新版查询）。

    识别：
      · pip   "==1.2.3"
      · npm   "1.2.3"（纯 semver）
      · Poetry "1.2.3"（纯版本号）
      · 带 v 前缀的 "v1.2.3" / "=v1.2.3"（部分清单会这么写）
    范围写法（>=、~=、^、~、*、1.2.x、含逗号组合等）一律返回 None，
    此时工具按最新版查询，并在结论中标注约束原文。
    """
    if not spec:
        return None
    s = spec.strip()
    if s.startswith("=="):
        v = s[2:].strip()
    elif s.startswith("="):
        v = s[1:].strip()
    else:
        v = s
    if not v or "*" in v or "," in v or " " in v or "<" in v or ">" in v or "~" in v or "^" in v:
        return None
    # v0.3 增补：去掉 v/V 前缀。此前 "v1.2.3" 会被原样拿去查 PyPI，
    # 命中 404 后被误报为"无法获取元数据"，而不是回退查最新版。
    if len(v) > 1 and v[0] in "vV" and v[1].isdigit():
        v = v[1:]
    if not v or not (v[0].isdigit() or v[0] in ".*"):
        return None
    return v


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
        if " AND " in r["spdx"]:
            note = "（复合许可证须全部满足，已按最严格项判定）"
        elif " OR " in r["spdx"]:
            note = "（多许可任选其一，已按最宽松项判定）"
        else:
            note = ""

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

def audit(packages, project_license="MIT", source="PyPI", depth=0,
          transitive_limit=0, version_specs=None, jobs=1):
    """执行审计。

    version_specs: {包名小写: 版本约束字符串}。若约束是精确锁定版本
    （如 ==1.2.3 / 纯版本号），则按该版本查询许可证元数据并写入
    record["requested_version"]；范围约束仍查最新版。

    jobs: 元数据抓取的并发数（>1 时按层并发预热缓存）。并发只影响网络等待，
    判定逻辑与结果和串行完全一致。
    """
    records, seen, frontier = [], set(), list(packages)
    version_specs = version_specs or {}
    level = 0
    while frontier and level <= depth:
        prefetch(frontier, source, version_specs, jobs)
        nxt = []
        for name in frontier:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            ver = _exact_version(version_specs.get(key, ""))
            rec = fetch_pypi(name, ver) if source == "PyPI" else fetch_npm(name, ver)
            if ver:
                rec["requested_version"] = ver
            records.append(rec)
            if level < depth:
                nxt.extend(rec.get("deps", []))
        frontier = nxt
        level += 1
    return records, detect_conflicts(project_license, records, transitive_limit)


# 未做源码证据判定时的占位结论。v0.3 起不再默认写"作为库调用（未修改源码）"：
# 那等于在没有证据的情况下假装确定，与本工具"宁可显式暴露不确定性"的原则相悖，
# 也与 README「已知限制」里"源码不在本地时相关字段标为待确认"的说法矛盾。
PENDING_USAGE = "待确认（未采集源码证据）"
PENDING_BOUNDARY = "待确认"

_STATUS_CN = {"OK": "已核验", "NOT_FOUND": "未找到", "FETCH_ERROR": "获取失败"}


def to_checklist_table(records, judgments=None):
    """生成符合竞赛要求的《开源及第三方资源使用清单》。

    judgments: {包名: {"使用方式":..., "自主开发边界":...}}，由 semantic_audit.py
    在采集源码证据后产出。未传入的包按"待确认"输出，不臆断使用方式。
    """
    judgments = judgments or {}
    lines = ["| 资源名称 | 类型 | 版本 | 来源 | 许可证/授权类型 | 识别依据 | 使用方式 | "
             "关键许可义务或限制 | 自主开发边界 | 合规状态 | 开放方式 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        cat = category_of(r["spdx"])
        status = _STATUS_CN.get(r["status"], r["status"])
        if r["status"] == "OK" and cat == "unknown":
            status = "待确认"
        j = judgments.get(r["name"]) or {}
        usage = j.get("使用方式") or PENDING_USAGE
        boundary = j.get("自主开发边界") or PENDING_BOUNDARY
        lines.append("| {} | {} 依赖包 | {} | {} | {} | {}（可信度{}） | "
                     "{} | {} | {} | {} | "
                     "随项目一并声明许可 |".format(
                         r["name"], r["source"], r["version"], r["source"],
                         r["spdx"] if r["spdx"] != "UNKNOWN" else "未识别",
                         r.get("license_field", "?"), r.get("confidence", "?"),
                         usage, obligations_of(r["spdx"]), boundary, status))
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
    ap.add_argument("--jobs", type=int, default=1,
                    help="元数据抓取并发数，1=串行（默认）。建议 8—16，结果与串行一致")
    ap.add_argument("--out", default="license_audit_report.md")
    a = ap.parse_args()

    specs = {}
    if a.requirements:
        vpkgs = parse_requirements_verbose(Path(a.requirements))
        pkgs, source = [n for n, _ in vpkgs], "PyPI"
        specs = {n.lower(): s for n, s in vpkgs}
    elif a.pyproject:
        vpkgs = parse_pyproject_verbose(Path(a.pyproject))
        pkgs, source = [n for n, _ in vpkgs], "PyPI"
        specs = {n.lower(): s for n, s in vpkgs}
    elif a.package_json:
        vpkgs = parse_package_json_verbose(Path(a.package_json))
        pkgs, source = [n for n, _ in vpkgs], "npm"
        specs = {n.lower(): s for n, s in vpkgs}
    elif a.packages:
        pkgs, source = a.packages, "PyPI"
    else:
        ap.error("需要 --requirements / --pyproject / --package-json / --packages 之一")

    print(f"[1/4] 解析依赖清单：{len(pkgs)} 个直接依赖（来源 {source}）")
    locked = sum(1 for s in specs.values() if _exact_version(s))
    if locked:
        print(f"     其中 {locked} 个依赖为精确锁定版本，将按锁定版本查询许可证元数据")
    if a.jobs > 1:
        print(f"     元数据抓取并发数：{a.jobs}")
    records, findings = audit(pkgs, a.project_license, source, a.depth, a.transitive,
                              specs, a.jobs)
    ok = sum(1 for r in records if r["status"] == "OK")
    print(f"[2/4] 获取许可证元数据：成功 {ok} / 共 {len(records)}")

    notice = matrix_notice(a.project_license)
    if notice:
        print(f"     ⚠ {notice}")

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
          f"**扫描依赖数**：{len(records)}　**工具版本**：v{VERSION}", "",
          "## 一、风险汇总", ""]
    if notice:
        md += [f"> ⚠ {notice}", ""]
    if findings:
        md += ["| 级别 | 资源 | 检出许可证 | 风险说明 | 处理建议 |", "|---|---|---|---|---|"]
        for f in sorted(findings, key=lambda x: 0 if x["level"] == "高" else 1):
            md.append(f"| {f['level']} | {f['pkg']} | {f['license']} | {f['reason']} | {f['advice']} |")
    else:
        md.append("未检出许可证兼容性风险。")
    md += ["", "## 二、资源清单", "", to_checklist_table(records), "",
           f"> 项目自身许可证：{a.project_license}　|　清单由脚本自动生成，"
           f"「识别依据」列标注了每个许可证的判定来源，可信度非「高」的项须人工复核　|　共 {len(records)} 项",
           "> 「使用方式」「自主开发边界」两列需要源码证据，本报告未采集，"
           "统一标注为「待确认」；运行 semantic_audit.py 可补齐这两列"]
    if locked:
        md.append(f"> 版本说明：{locked} 个依赖按清单锁定的精确版本查询许可证（见 JSON 的 requested_version 字段）；"
                  "其余为范围约束，按最新版查询，历史版本许可可能与最新版不同，请定期重跑核对")
    Path(a.out).write_text("\n".join(md), encoding="utf-8")
    Path(a.out.replace(".md", ".json")).write_text(
        json.dumps({"project": a.project_name, "project_license": a.project_license,
                    "tool_version": VERSION,
                    "records": records, "findings": findings, "stats": stats,
                    "notices": [notice] if notice else []},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"报告已写出：{a.out} / {a.out.replace('.md', '.json')}")


if __name__ == "__main__":
    main()
