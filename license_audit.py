#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
license_audit.py — 开源许可证合规检查 + 《开源及第三方资源使用清单》生成器
AIC·AI+开源赛道参赛作品「依赖许可证哨兵」

版本号以本文件下方的 VERSION 常量为唯一来源，不在此处硬编码——
此前这里写 v0.3，而 VERSION 已到 0.4、pyproject.toml 又是 0.3.0，
三处各说各话（审查报告 L1）。下方按引入版本分节记录修正历史。

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

v0.4.2 修正（第三方检查清单，逐条复现后修复）：
  · CLI 与 Web 入口此前不做 monorepo 工作区内部包排除（只有 scan_projects.py
    做了），与 README 宣称不符；现在两个入口都调用 split_workspace_deps。
  · --out 不含 .md 时 Markdown 报告会被 JSON 静默覆盖。
  · CLI 不跟随 -r 指针清单，纯指针文件会静默解析成 0 个依赖。
  · 证据采集的依赖归属用子串匹配（torch 被 torchvision 命中）。
  · Web 端未限制解压后规模，非法清单类型回 500 而非 400。
  · rescan_failed.py 重建汇总时丢失 v0.4 新增字段、且重抓不带锁定版本。
  · 代码注释里出现高于发布版本的版本标注（检查清单 P2-2）。

v0.4.1 修正（第三方审查报告，逐条复现后修复）：
  · 项目自身许可证未归一化就查 COMPAT_MATRIX，查不到便退化成空集合，
    导致该项目下所有传染性依赖被逐条误报为冲突。matplotlib 的
    "PSF-based"（本就等同 PSF-2.0）使其 3 个 MPL 依赖全被报中危。
    → 新增 resolve_project_license()，先归一化再查表；矩阵确实未覆盖时
      不再逐条下冲突结论，改为合成一条「无法判定」并点名涉及的依赖。
  · 补入 Sustainable-Use-1.0（n8n 等项目自身使用的源码可得许可）。

用法：
  python license_audit.py --requirements req.txt --project-license MIT
  python license_audit.py --packages requests pymupdf --project-license Apache-2.0 --transitive 40
  python license_audit.py --requirements req.txt --project-license MIT --jobs 12
"""

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERSION = "0.4.3"


def _force_utf8_stdio():
    """把 stdout / stderr 强制成 UTF-8，避免 Windows 上打印中文直接崩溃。

    背景：Windows 控制台的默认编码取决于系统区域（英文系统是 cp1252，
    中文系统是 cp936）。本项目大量输出中文，一旦落到 cp1252 就会抛
    UnicodeEncodeError——CI 的 windows-latest 三个 job 全部因此失败，
    本机是英文区域 Windows 的用户同样无法运行。

    放在模块层而不是各脚本里：其余脚本都 import 本模块，导入即生效，
    无需在每个入口脚本重复一遍。Python 3.7+ 支持 reconfigure，
    更早版本静默跳过（3.8 是本项目的最低要求，这里只是保守兜底）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()

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
    # v0.4 增补：Apache-1.1 必须排在通用 Apache 规则之前，否则
    # "Apache License 1.1" 会被通用规则判成 Apache-2.0（两者条款差异很大）。
    (r"^Apache[- ]?1\.1|^Apache[- ]?(Software )?License[- ]?v?1\.1", "Apache-1.1"),
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
    # v0.4 增补：实测 Bottleneck 的 license 字段只写 "Simplified BSD"，
    # 按生态里的通行用法即 BSD-2-Clause；FreeBSD 同为 2-Clause。
    (r"^Simplified BSD", "BSD-2-Clause"),
    (r"^FreeBSD", "BSD-2-Clause"),
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
    # v0.4 修正：BSL-1.0 是 Boost Software License，而 BSL-1.1 是 Business Source
    # License——两者前缀相同、条款完全相反（宽松 vs. 限制商业使用）。
    # 原写法 `BSL[- ]?1` 会把 BSL-1.1 一并吃掉并判成宽松许可，属于高危误判。
    (r"^(?:BSL[- ]?1(?![.\d])|BSL[- ]?1\.0|Boost Software)", "BSL-1.0"),
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
    # ------------------------------------------------------------------
    # v0.4 增补：非 OSI / 源码可得许可，以及真实生态里高频但此前漏掉的写法。
    # 这段必须放在 GPL/LGPL/AGPL 兜底规则之前，否则 "Server Side Public
    # License" 之类的写法会被裸家族兜底规则吃掉。
    # ------------------------------------------------------------------
    (r"^ZPL[- ]?v?2\.1", "ZPL-2.1"),
    (r"^ZPL\b|Zope Public", "ZPL-2.1"),
    (r"^Elastic[- ]?(License )?2|Elastic License", "Elastic-2.0"),
    (r"^BUSL|^(?:BUSL|BSL)[- ]?1\.1|Business Source License", "BSL-1.1"),
    # v0.4.1：n8n 等项目用作自身许可证的源码可得许可，此前落 UNKNOWN，
    # 连带使其在 COMPAT_MATRIX 中查不到，触发 H2 那类系统性误报。
    (r"^Sustainable Use", "Sustainable-Use-1.0"),
    (r"^SSPL|Server Side Public", "SSPL-1.0"),
    (r"^CC[- ]?BY[- ]?SA[- ]?4", "CC-BY-SA-4.0"),
    (r"^CC[- ]?BY[- ]?4|^CC[- ]?BY\b|Creative Commons Attribution", "CC-BY-4.0"),
    (r"^OFL[- ]?1|SIL Open Font", "OFL-1.1"),
    (r"^Ruby\b|Ruby License", "Ruby"),
    (r"^MS[- ]?PL\b|Microsoft Public", "MS-PL"),
    (r"^MS[- ]?RL\b|Microsoft Reciprocal", "MS-RL"),
    (r"^NCSA|University of Illinois", "NCSA"),
    (r"^UPL[- ]?1|Universal Permissive", "UPL-1.0"),
    (r"^Vim\b|Vim License", "Vim"),
    (r"^BSD[- ]?4|4[- ]?Clause BSD", "BSD-4-Clause"),
    (r"^MPL[- ]?v?[- ]?1\.1", "MPL-1.1"),
    (r"^EPL[- ]?v?[- ]?1\.0", "EPL-1.0"),
    (r"^W3C", "W3C"),
    (r"^libpng|PNG Reference Library", "Libpng"),
    (r"^Unicode[- ]?DFS", "Unicode-DFS-2016"),
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
    # ------------------------------------------------------------------
    # v0.4 增补：知识库此前只收录 33 种，实测已造成真实漏判——
    # zope.interface(ZPL-2.1)、arize-phoenix(Elastic-2.0)、
    # todomvc-app-css(CC-BY-4.0) 三个包的源站都给了明确许可证，
    # 工具却一律降级成 UNKNOWN。以下按 SPDX 标准标识补齐。
    # ------------------------------------------------------------------
    # 宽松许可（OSI 认证或等效宽松）
    "ZPL-2.1":        ("permissive",        "保留版权与许可声明；修改需在文件头注明变更（Zope 公共许可证）"),
    "OFL-1.1":        ("permissive",        "保留版权与许可声明；字体衍生品不得使用保留字体名（SIL 开放字体许可）"),
    "Ruby":           ("permissive",        "保留版权与许可声明（Ruby 许可，与 GPL-2.0 双许可可任选其一）"),
    "MS-PL":          ("permissive",        "保留版权与许可声明（微软公共许可）"),
    "NCSA":           ("permissive",        "保留版权与许可声明（伊利诺伊大学 NCSA 开源许可，BSD 风格）"),
    "UPL-1.0":        ("permissive",        "保留版权与许可声明，含明确的专利授权（Oracle 通用许可）"),
    "Vim":            ("permissive",        "保留版权与许可声明；鼓励向乌干达儿童捐款（慈善软件条款，非强制义务）"),
    "BSD-4-Clause":   ("permissive",        "保留版权与许可声明，且所有宣传材料须提及本软件来源（第 3 条广告条款）"),
    "W3C":            ("permissive",        "保留版权与许可声明（W3C 软件许可，含免责声明）"),
    "Libpng":         ("permissive",        "保留版权与许可声明（libpng 许可，zlib 风格）"),
    "Unicode-DFS-2016": ("permissive",      "保留版权与许可声明（Unicode 数据文件许可，仅限数据与软件分发）"),
    "Apache-1.1":     ("permissive",        "保留版权与许可声明及变更说明；与 GPL-2.0 不兼容"),
    # 弱传染
    "MPL-1.1":        ("weak-copyleft",     "MPL 覆盖的文件需以 MPL 开放源码（文件级隔离）"),
    "EPL-1.0":        ("weak-copyleft",     "EPL 覆盖的模块需以 EPL 开放源码（模块级隔离）"),
    "MS-RL":          ("weak-copyleft",     "修改过的源文件需以 MS-RL 开放源码（文件级隔离）"),
    # 强传染 / 网络传染（非 OSI，但条款性质明确）
    "CC-BY-SA-4.0":   ("strong-copyleft",   "衍生作品需以 CC-BY-SA-4.0 同等许可开放（相同方式共享）"),
    "SSPL-1.0":       ("network-copyleft",  "对外提供服务时须开放整个服务栈源码，义务范围比 AGPL 更广"),
    # 源码可得（source-available）：能拿到源码，但附带商业使用限制
    "Elastic-2.0":    ("source-available",  "不得作为托管服务对外提供；不得规避付费功能限制；不得移除版权与许可声明"),
    "BSL-1.1":        ("source-available",  "变更日之前限制生产环境商业使用，到期后转为开源许可"),
    "Sustainable-Use-1.0": ("source-available", "仅允许内部业务与非商业用途；不得将该软件本身作为商业产品对外提供（n8n 等使用）"),
    # 非 OSI 但条款宽松的内容许可，用于软件时解释存在不确定性
    "CC-BY-4.0":      ("permissive",        "署名即可自由使用；不含专利授权条款（内容许可，非软件许可）"),
    "UNKNOWN":        ("unknown",           "许可证未识别，须人工确认后方可分发"),
}

# 非 OSI 认证许可 / 带附加限制的许可：识别出来还不够，必须显式说出商业后果。
#
# 这类许可此前一律落进 UNKNOWN 标成"待确认"。保守是对的，但对使用者没有帮助——
# 他只知道"不知道"，不知道"风险在哪"。这里给出可执行的提示。
NON_OSI_NOTES = {
    "Elastic-2.0": "非 OSI 认证许可：禁止将本软件作为托管服务对外提供，"
                   "禁止规避付费功能限制——商业 SaaS 场景需单独取得商业授权",
    "BSL-1.1": "非 OSI 认证许可：变更日之前不得用于生产环境商业用途，"
               "到期后自动转为开源许可（具体日期见上游 LICENSE 文件）",
    "Sustainable-Use-1.0": "非 OSI 认证许可（n8n 自定条款，非 SPDX 标准标识）："
                           "仅限内部业务与非商业用途，不得把该软件本身作为商业产品提供；"
                           "条款由厂商单方修订，商业使用前须核对当期文本",
    "SSPL-1.0": "非 OSI 认证许可（SSPL 未获 OSI 批准，部分发行版不视为开源）："
                "对外提供服务时须开放整个服务栈源码，义务范围比 AGPL 更广",
    "CC-BY-4.0": "非 OSI 认证许可：这是 Creative Commons 内容许可，不是软件许可，"
                 "不含专利授权条款，用于软件时条款解释存在不确定性",
    "CC-BY-SA-4.0": "非 OSI 认证许可：相同方式共享条款会传染衍生作品，"
                    "且不含专利授权，与多数软件许可证不兼容",
    "BSD-4-Clause": "含第 3 条广告条款（宣传材料须提及本软件来源），"
                    "与 GPL 系列不兼容，已被 BSD-3-Clause 取代",
    "Apache-1.1": "Apache-1.1 已过时且与 GPL-2.0 不兼容（专利终止条款），建议上游升级到 Apache-2.0",
    "Ruby": "Ruby 许可与 GPL-2.0 双许可，可任选其一；选择 GPL 分支时须遵守 GPL 义务",
    "Vim": "Vim 许可含慈善捐款请求（非强制义务），但条款明确要求保留声明",
    "OFL-1.1": "字体许可：衍生字体不得使用保留字体名（RFN），且不得单独出售字体文件",
    "Unicode-DFS-2016": "仅限 Unicode 数据与配套软件：未经许可不得用于其他数据集",
    "MPL-1.1": "MPL-1.1 与 GPL 不兼容（1.1 版无 GPL 兼容条款），MPL-2.0 才解决该问题",
}


def commercial_note(spdx):
    """返回该许可证的非 OSI / 附加限制提示；没有则返回 None。

    与 obligations_of 分开的原因：义务是"你必须做什么"，而这里是
    "这个许可本身有什么坑"——后者在知识库收录之后才可能被说出来。
    """
    if not spdx or spdx == "UNKNOWN":
        return None
    base = re.sub(r"-or-later$", "-only", spdx)
    return NON_OSI_NOTES.get(spdx) or NON_OSI_NOTES.get(base)


# ---------------------------------------------------------------- 未识别项归因
#
# 修复前：所有"没识别出许可证"的依赖统一记为 UNKNOWN、统一计入未识别，
# 于是 97.8% 这个识别率同时惩罚了两种完全不同性质的事：
#   · 源站根本没有这个包 / 根本没填许可证  → 工具无能为力，不该算工具的错
#   · 源站写了 ZPL-2.1，工具的知识库不认  → 这才是工具该改进的地方
# 混在一起，识别率这个数字既不能指导改进，也不能对外解释。
#
# 现在按四种性质分开统计，其中只有 UNSUPPORTED_LICENSE 归因于工具自身。
UNKNOWN_KINDS = ("NOT_IN_REGISTRY", "NO_METADATA", "UNSUPPORTED_LICENSE",
                 "FETCH_FAILED")

UNKNOWN_KIND_CN = {
    "NOT_IN_REGISTRY": "源站无此包",
    "NO_METADATA": "源站未填许可证",
    "UNSUPPORTED_LICENSE": "知识库未收录",
    "FETCH_FAILED": "网络获取失败",
}

# 是否应归因于工具自身（唯一会拉低"工具真实识别能力"的一类）
UNKNOWN_KIND_TOOL_FAULT = {"UNSUPPORTED_LICENSE"}

UNKNOWN_KIND_ADVICE = {
    "NOT_IN_REGISTRY": "确认包名拼写、是否为私有包或已下架；私有包请人工补填许可证后再纳入清单",
    "NO_METADATA": "源站未提供许可证字段，须人工核对上游仓库 LICENSE 文件后补填",
    "UNSUPPORTED_LICENSE": "源站已给出许可证但本工具知识库未收录该写法，"
                           "可将该写法提交到 SPDX_PATTERNS / LICENSE_DB 补充",
    "FETCH_FAILED": "网络超时或限流导致未取到元数据，重跑即可（非许可证问题）",
}


# 源站偶尔会把"许可证所在的文件名"或纯占位词直接填进 license 字段。
# 实测 rouge 的字段值就是字面量 "LICENCE.txt"。
# 这不是"工具知识库没收录该写法"，而是"源站压根没给许可证信息"——
# 若归到 UNSUPPORTED_LICENSE，就把源站的问题算到工具头上，
# 让"应由工具改进"这个指标虚高，反而失去指导意义。
_PLACEHOLDER_RE = re.compile(
    r"^(?:see\s+licen[cs]e|licen[cs]e(?:[-_ ]?file)?(?:\.(?:txt|md|rst|html?))?"
    r"|copying(?:\.txt)?|unknown|none|null|unlicensed|todo|n/a|—|-|\?+)$",
    re.IGNORECASE)


def is_placeholder_license(value):
    """该值是否只是占位/指向文件，而不含任何许可证信息。"""
    s = (value or "").strip()
    if not s:
        return True
    if len(s) > 60:               # 长文本里可能藏着真许可证正文，不视为占位
        return False
    return bool(_PLACEHOLDER_RE.match(s))


def classify_unknown(rec):
    """对一条 spdx == UNKNOWN 的记录做性质归类；已识别的记录返回 None。

    归类只依据记录自身已有的字段（status / license_raw），不重新发起网络请求，
    因此对离线快照同样有效。
    """
    if not rec or rec.get("spdx") != "UNKNOWN":
        return None
    st = rec.get("status")
    if st == "NOT_FOUND":
        return "NOT_IN_REGISTRY"
    if st == "FETCH_ERROR":
        return "FETCH_FAILED"
    # 空值与占位值同属"源站没给数据"，都在工具能力之外
    if is_placeholder_license(rec.get("license_raw")):
        return "NO_METADATA"
    kind = rec.get("unknown_kind")
    if kind in UNKNOWN_KINDS:
        return kind
    return "UNSUPPORTED_LICENSE"


def unknown_breakdown(records):
    """未识别项的四分类计数，未出现的类别也补 0，便于直接落 JSON。"""
    out = {k: 0 for k in UNKNOWN_KINDS}
    for r in records or []:
        k = classify_unknown(r)
        if k:
            out[k] += 1
    return out


def tool_attributable_unknown(records):
    """应当归因于工具自身的未识别项数量（知识库未收录的写法）。

    这是唯一"补知识库就能降下来"的数字，与源站数据质量无关。
    """
    return sum(1 for r in records or []
               if classify_unknown(r) in UNKNOWN_KIND_TOOL_FAULT)


def effective_resolve_rate(records):
    """剔除源站客观无数据后的识别率，与原始识别率并列展示。

    effective = 1 - (知识库未收录 + 网络失败) / 总数
    即：把"源站压根没给数据"的部分从分母里排除，只看工具该认出来而没认出来的。
    """
    recs = list(records or [])
    if not recs:
        return 0.0
    b = unknown_breakdown(recs)
    bad = b["UNSUPPORTED_LICENSE"] + b["FETCH_FAILED"]
    return round(100.0 * (len(recs) - bad) / len(recs), 1)

CATEGORY_CN = {
    "permissive": "宽松许可",
    "weak-copyleft": "弱传染",
    "strong-copyleft": "强传染",
    "network-copyleft": "网络传染",
    "source-available": "源码可得（非 OSI）",
    "unknown": "未识别",
}
# 传染强度排序，用于复合表达式取最严格者。
# v0.4 增补 source-available：源码能拿到，但附带商业使用限制。
# 它比强传染更"严"——强传染只要求开源，源码可得许可是直接用不了，
# 因此排在 network-copyleft 之后、unknown 之前。
CATEGORY_RANK = {"permissive": 0, "weak-copyleft": 1, "strong-copyleft": 2,
                 "network-copyleft": 3, "source-available": 4, "unknown": 5}
# 取不到类别时按 unknown 计，写死 4 会在 v0.4 之后错误地落到 source-available
UNKNOWN_RANK = CATEGORY_RANK["unknown"]

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
    "SSPL-1.0": _COPYLEFT_PROJECT | {"network-copyleft"},
    # v0.4 增补：知识库扩容后同步补齐矩阵，避免这些标识作为项目自身许可证时
    # 静默退化成"任何传染性依赖都报冲突"
    "ZPL-2.1": _PERMISSIVE_PROJECT,
    "OFL-1.1": _PERMISSIVE_PROJECT,
    "Ruby": _PERMISSIVE_PROJECT,
    "MS-PL": _PERMISSIVE_PROJECT,
    "NCSA": _PERMISSIVE_PROJECT,
    "UPL-1.0": _PERMISSIVE_PROJECT,
    "Vim": _PERMISSIVE_PROJECT,
    "BSD-4-Clause": _PERMISSIVE_PROJECT,
    "W3C": _PERMISSIVE_PROJECT,
    "Libpng": _PERMISSIVE_PROJECT,
    "Unicode-DFS-2016": _PERMISSIVE_PROJECT,
    "Apache-1.1": _PERMISSIVE_PROJECT,
    "CC-BY-4.0": _PERMISSIVE_PROJECT,
    "MPL-1.1": _PERMISSIVE_PROJECT,
    "EPL-1.0": _PERMISSIVE_PROJECT,
    "MS-RL": _PERMISSIVE_PROJECT,
    "CC-BY-SA-4.0": _COPYLEFT_PROJECT,
    # 源码可得项目：自身受商业限制，但引入的依赖仍按"不得引入更强传染"判定
    "Elastic-2.0": _PERMISSIVE_PROJECT,
    "BSL-1.1": _PERMISSIVE_PROJECT,
    "Sustainable-Use-1.0": _PERMISSIVE_PROJECT,
}

# 矩阵未覆盖项目许可证时的提示语（不再静默按"一律冲突"处理）
MATRIX_NOT_COVERED_NOTICE = (
    "项目自身许可证「{lic}」不在兼容性矩阵覆盖范围内，"
    "本次仅按「是否传染」给出提示性结论，未做逐对兼容性判定；"
    "如需精确结论请核对专业法律意见，或在 COMPAT_MATRIX 中补充该许可证"
)


def resolve_project_license(project_license):
    """把项目自身许可证归一到矩阵可查的规范标识；归一不到（矩阵未覆盖）返回 None。

    v0.4.1 修正（审查报告 H2「兼容矩阵未覆盖引发系统性误报」）：
    此前直接用原始写法查 COMPAT_MATRIX。项目许可证往往写的是上游自称的
    非规范写法——matplotlib 的 pyproject 里写的就是 "PSF-based"，
    n8n 写的是 "Sustainable Use License"。这类写法查不到 → `get(..., set())`
    返回空集合 → 该项目下**所有**弱传染/强传染依赖被逐条报成冲突。

    实测复现：以 "PSF-based" 为项目许可扫描 matplotlib 依赖，
    certifi / pikepdf / pytest-rerunfailures 三个 MPL-2.0 依赖全部被报中危；
    换成归一化后的 "PSF-2.0" 则为 0 条。

    因此这里先做归一化再查表——"PSF-based" 归一化后本就是 PSF-2.0。
    """
    if not project_license:
        return None
    if project_license in COMPAT_MATRIX:
        return project_license
    norm = normalize_license(project_license)
    if norm in COMPAT_MATRIX:
        return norm
    return None


def matrix_notice(project_license):
    """项目许可证不在兼容性矩阵覆盖范围内时，返回一条显式提示；否则返回 None。

    v0.3 增补：以前这种情况会静默退化为"任何传染性依赖都报冲突"，
    使用者既不知道矩阵没覆盖，也不知道结论是怎么来的。
    """
    if not project_license:
        return MATRIX_NOT_COVERED_NOTICE.format(lic="（未填写）")
    if resolve_project_license(project_license) is None:
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
        ranks = [CATEGORY_RANK.get(_category_single(x), UNKNOWN_RANK) for x in g]
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
            "confidence": "无", "deps": [], "status": status,
            "unknown_kind": "FETCH_FAILED" if status == "FETCH_ERROR"
                            else "NOT_IN_REGISTRY"}


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
    spdx = normalize_license(raw)
    return {"name": info.get("name") or name, "source": "PyPI",
            "version": info.get("version") or version or "?",
            "license_raw": raw[:160], "spdx": spdx,
            "license_field": field, "confidence": conf,
            "deps": deps, "status": "OK",
            "unknown_kind": classify_unknown({"spdx": spdx, "status": "OK",
                                              "license_raw": raw[:160]})}


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
    spdx = normalize_license(str(raw))
    return {"name": d.get("name") or name, "source": "npm",
            "version": d.get("version") or "?",
            "license_raw": str(raw)[:160], "spdx": spdx,
            "license_field": "license",
            "confidence": "高" if str(raw).strip() else "无",
            "deps": list((d.get("dependencies") or {}).keys()), "status": "OK",
            "unknown_kind": classify_unknown({"spdx": spdx, "status": "OK",
                                              "license_raw": str(raw)[:160]})}


# ---------------------------------------------------------------- 清单解析

_INCLUDE_RE = re.compile(r"^(?:-r|--requirement)(?:\s+|=)(\S+)", re.I)
MAX_INCLUDE_DEPTH = 5      # -r 引用链最大深度（防病态嵌套/死循环）
MAX_INCLUDE_FILES = 50     # 单次解析最多跟随的清单文件数


def path_within(base, target):
    """target 解析后是否落在 base 目录内（跟随符号链接后再判定）。

    v0.4.2：CLI 与 Web 都要跟随 `-r` 引用，而引用路径来自使用者提供的清单，
    必须做目录围栏——`p.parent / inc` 在 inc 为绝对路径时会被 pathlib 直接
    替换成该绝对路径，于是 `-r /etc/passwd` 可以读到清单目录之外的文件。
    web/server.py 复用本函数，避免两处各写一份判定。
    """
    try:
        base_r = Path(base).resolve()
        tgt_r = Path(target).resolve()
    except (OSError, RuntimeError):        # RuntimeError: resolve 遇到符号链接环
        return False
    return tgt_r == base_r or base_r in tgt_r.parents


def collect_requirement_files(path, include_root=None):
    """展开 requirements 清单的 -r / --requirement 引用链。

    真实项目的 requirements.txt 常常只是一个指针文件（内容就一行
    `-r requirements/runtime.txt`）。不跟随就只能解析出 0 个依赖，而且不报错
    ——用户会以为项目真的没有依赖。v0.4.2 起 CLI 与此前已实现跟随的
    scan_projects.py / web 端行为一致。

    返回 (文件列表, 提示列表)：文件列表含自身，按引用顺序去重；
    提示列表是可读说明（跟随了几个文件、哪些引用被跳过），供调用方打印，
    避免"静默少解析"这种最难排查的失败方式。
    """
    path = Path(path)
    base = Path(include_root) if include_root else path.parent
    files, seen = [], set()
    notes, unsafe, missing, too_deep = [], [], [], []

    def walk(cur, depth):
        try:
            key = cur.resolve()
        except OSError:
            key = cur
        if key in seen:                       # 环路：a 引用 b、b 又引用 a
            return
        seen.add(key)
        files.append(cur)
        if depth >= MAX_INCLUDE_DEPTH:
            too_deep.append(str(cur))
            return
        if len(files) >= MAX_INCLUDE_FILES:
            return
        try:
            text = cur.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return
        for line in text.splitlines():
            m = _INCLUDE_RE.match(line.split("#")[0].strip())
            if not m:
                continue
            inc = m.group(1).strip().strip("\"'")
            # 绝对路径、~ 家目录、盘符相对路径（C:foo）一律不接受
            if not inc or Path(inc).is_absolute() or inc[0] in ("~", "\\") \
                    or ":" in inc.split("/")[0]:
                unsafe.append(inc)
                continue
            q = cur.parent / inc
            if not path_within(base, q) or not q.is_file():
                missing.append(inc)
                continue
            walk(q, depth + 1)

    walk(path, 0)
    if len(files) > 1:
        shown = "、".join(str(f) for f in files[1:6])
        notes.append(f"清单为指针文件，已跟随 {len(files) - 1} 个 -r 引用：{shown}")
    if unsafe:
        notes.append(f"已忽略 {len(unsafe)} 处绝对路径或越界引用（安全围栏）："
                     + "、".join(unsafe[:3]))
    if missing:
        notes.append(f"有 {len(missing)} 处 -r 引用的文件不存在或不可读："
                     + "、".join(missing[:3]))
    if too_deep:
        notes.append(f"引用链超过 {MAX_INCLUDE_DEPTH} 层，已停止跟随："
                     + "、".join(too_deep[:2]))
    return files, notes


def _parse_requirements_lines(path):
    """解析单个 requirements 文件的依赖行（不跟随引用）。"""
    out = []
    for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
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


def parse_requirements_verbose(path, include_root=None, notes=None, follow=True):
    """解析 requirements.txt，返回 [(包名, 版本约束), ...]，保留版本信息。

    版本约束会去除 [extras] 与环境标记（; python_version ...），
    供后续按锁定版本精确查询许可证元数据。

    v0.4.2：默认跟随 -r / --requirement 引用。此前 CLI 入口对 `-r` 开头的行
    直接跳过（web 与 scan_projects 却已实现跟随），同一份清单换个入口就得到
    不同结果。`include_root` 指定允许的引用范围，默认取清单自身所在目录；
    web 端传入解压目录以保持原有围栏。`notes` 传入列表时追加可读提示。
    """
    if not follow:
        return _parse_requirements_lines(path)
    files, msgs = collect_requirement_files(path, include_root)
    if notes is not None:
        notes.extend(msgs)
    out, seen = [], set()
    for f in files:
        try:
            pairs = _parse_requirements_lines(f)
        except OSError:
            continue
        for name, spec in pairs:
            key = re.sub(r"[-_.]+", "-", name).lower()   # PEP 503 归一化后去重
            if key not in seen:
                seen.add(key)
                out.append((name, spec))
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


def split_workspace_deps(pairs):
    """把 [(包名, 版本范围), ...] 拆成 (外部依赖, 工作区内部包)。

    monorepo 里版本写作 "workspace:*" / "workspace:^" 的依赖是项目自身
    代码、根本不发布到 npm。若不排除，它们会被当成第三方依赖去查：
    查不到就记「未识别」，还判成风险项——归因完全错了。
    实测 lobe-chat 因此被误报 29 条风险项、识别率被拉到 58.6%。

    判定只依据 `workspace:` 协议标记，绝不按包名猜测：`@scope/xxx` 里既
    有内部包，也有真实发布的第三方包（`@vercel/og`、`@anthropic-ai/sdk`），
    按名字猜必然出错。

    返回两个列表，各自保持输入顺序：工作区内部包会被审计流程排除。
    """
    external, internal = [], []
    for name, spec in pairs:
        if str(spec or "").strip().startswith("workspace:"):
            internal.append(name)
        else:
            external.append(name)
    return external, internal


def exclude_workspace_deps(pairs):
    """按 workspace: 标记过滤 [(包名, 版本约束)]，返回 (保留的对, 排除的包名)。

    split_workspace_deps() 返回的是包名列表，适合统计；但调用方往往还需要
    保留版本约束（去做锁定版本查询），直接拿包名列表当依赖对用会崩溃。
    这里提供"保留原始对"的版本，CLI 与 Web 两个入口都用它，保证行为一致。
    """
    external, internal = split_workspace_deps(pairs)
    keep = set(external)
    return [(n, s) for n, s in pairs if n in keep], internal


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
    try:
        import tomllib
        d = tomllib.loads(raw)
    except ImportError:                      # Python < 3.11 无 tomllib
        d = _toml_loads(raw)                 # 内置 TOML 子集解析，保证结果一致
    except Exception as e:
        raise SystemExit(f"pyproject.toml 解析失败：{e}")
    return _pyproject_deps(d)


def parse_pyproject(path: Path):
    return [n for n, _ in parse_pyproject_verbose(path)]


def _parse_pyproject_regex(raw):
    """无 tomllib 时的降级解析（保留兼容，内部已改用 _toml_loads）。"""
    return sorted({n for n, _s in _pyproject_deps(_toml_loads(raw))})


# ---------------------------------------------------------------- 最小 TOML 解析
#
# tomllib 是 Python 3.11 才进标准库的，而本项目声明支持 Python 3.8+，
# 又坚持零第三方依赖（不能引入 tomli）。
# 原来的降级方案是正则扫 dependencies 数组，实测在 Python 3.8/3.9/3.10 上
# 会丢版本约束、还会把 `name = "demo"` 这种无关键当成包名（C1/C3/E13 三个
# CI job 因此失败）。这里改用一个够用的 TOML 子集解析器：
# 只支持注释、表头、字符串、字符串数组、内联表——恰好覆盖
# PEP 621 / PEP 735 / Poetry 三种写法。

def _toml_strip_comment(s):
    """去掉行尾注释，但不影响引号内的 # 号。"""
    out, quote = [], None
    for ch in s:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch == "#":
            break
        else:
            if ch in "\"'":
                quote = ch
            out.append(ch)
    return "".join(out).strip()


def _toml_split_top(s, sep=","):
    """按顶层分隔符切分，引号内与嵌套括号内的分隔符不参与切分。"""
    parts, buf, quote, depth = [], [], None, 0
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _toml_key(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _toml_scalar(s):
    s = _toml_strip_comment(s).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s in ("true", "false"):
        return s == "true"
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            continue
    return s


def _toml_inline_table(s):
    s = s.strip()
    if s.startswith("{"):
        s = s[1:]
    if s.endswith("}"):
        s = s[:-1]
    out = {}
    for part in _toml_split_top(s):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        out[_toml_key(k)] = _toml_scalar(v)
    return out


def _toml_array(s):
    s = _toml_strip_comment(s).strip()
    inner = s[1:].rstrip()
    if inner.endswith("]"):
        inner = inner[:-1]
    items = []
    for part in _toml_split_top(inner):
        if part.startswith("{"):
            items.append(_toml_inline_table(part))
        else:
            items.append(_toml_scalar(part))
    return items


def _toml_loads(raw):
    """解析 TOML 子集，返回嵌套 dict。用于 Python < 3.11（无 tomllib）。"""
    root, cur = {}, None
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = _toml_strip_comment(lines[i])
        i += 1
        if not line:
            continue
        if line.startswith("["):
            if "]" not in line:
                continue
            path = [_toml_key(p) for p in _toml_split_top(line[1:line.index("]")], ".")]
            node = root
            for key in path:
                nxt = node.get(key)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[key] = nxt
                node = nxt
            cur = node
            continue
        if "=" not in line or cur is None:
            continue
        key, _, rest = line.partition("=")
        key = _toml_key(key)
        rest = rest.strip()
        # 数组 / 内联表可能跨行，先拼完整再解析
        if rest.startswith("[") and "]" not in rest:
            while i < len(lines) and "]" not in rest:
                rest += " " + _toml_strip_comment(lines[i])
                i += 1
            cur[key] = _toml_array(rest)
        elif rest.startswith("{") and "}" not in rest:
            while i < len(lines) and "}" not in rest:
                rest += " " + _toml_strip_comment(lines[i])
                i += 1
            cur[key] = _toml_inline_table(rest)
        elif rest.startswith("["):
            cur[key] = _toml_array(rest)
        elif rest.startswith("{"):
            cur[key] = _toml_inline_table(rest)
        else:
            cur[key] = _toml_scalar(rest)
    return root


def _pyproject_deps(d):
    """从已解析的 pyproject 字典里抽出 [(包名, 版本约束), ...]。

    与 tomllib 路径共用同一套抽取逻辑，保证有无 tomllib 的结果一致。
    """
    out = {}
    proj = d.get("project") or {}
    for spec in proj.get("dependencies") or []:
        if not isinstance(spec, str):
            continue
        n = _name_of(spec)
        if n:
            out.setdefault(n, _spec_after(spec, n))
    for group in (proj.get("optional-dependencies") or {}).values():
        for spec in group or []:
            if not isinstance(spec, str):
                continue
            n = _name_of(spec)
            if n:
                out.setdefault(n, _spec_after(spec, n))

    for group in (d.get("dependency-groups") or {}).values():
        for spec in group or []:
            if not isinstance(spec, str):
                continue
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
    resolved = resolve_project_license(project_license)
    covered = resolved is not None
    allowed = COMPAT_MATRIX.get(resolved, set())
    # 矩阵未覆盖时，我们并不知道该项目许可证与各类传染许可的兼容关系，
    # 逐条输出"是冲突"等于用一个不存在的依据下判断（H2 的系统性误报来源）。
    # 先把这类依赖收集起来，最后合成一条显式说明，把不确定性交给使用者。
    undecided = []

    for r in records:
        if r["status"] != "OK":
            kind = classify_unknown(r) or "NOT_IN_REGISTRY"
            findings.append({
                "level": "中", "pkg": r["name"], "license": r["spdx"],
                "reason": f"无法从 {r['source']} 获取元数据（{r['status']}）——"
                          f"归因：{UNKNOWN_KIND_CN.get(kind, kind)}，许可证状态未知",
                "advice": UNKNOWN_KIND_ADVICE.get(kind, "人工核对仓库 LICENSE 文件后补填清单"),
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
            # v0.4：同样是"没认出来"，性质完全不同，处理建议也不同——
            # 源站压根没填 → 只能人工补；知识库没收录 → 补知识库就能修好。
            kind = classify_unknown(r) or "NO_METADATA"
            findings.append({
                "level": "中" if low else "高", "pkg": r["name"], "license": r["spdx"],
                "reason": f"许可证无法识别{note}——归因：{UNKNOWN_KIND_CN.get(kind, kind)}"
                          f"（字段来源：{r.get('license_field','?')}，"
                          f"可信度 {r.get('confidence','?')}，原始值：{r['license_raw'] or '空'}）",
                "advice": UNKNOWN_KIND_ADVICE.get(
                    kind, "人工核对仓库 LICENSE 文件后补填清单，不得直接纳入分发范围"),
            })
        elif cat == "source-available":
            # 源码可得许可此前会落进 UNKNOWN 标成"待确认"，使用者只知道"不知道"，
            # 不知道风险在哪。这里显式说出商业限制。
            note2 = commercial_note(r["spdx"]) or "附带商业使用限制"
            findings.append({
                "level": "高", "pkg": r["name"], "license": r["spdx"],
                "reason": f"源码可得许可（非 OSI）{note}：{note2}",
                "advice": "确认使用场景是否落入限制范围（尤其是作为托管服务对外提供）；"
                          "否则替换为 Apache-2.0 / MIT 等 OSI 认证替代品",
            })
        elif cat == "network-copyleft":
            findings.append({
                "level": "高", "pkg": r["name"], "license": r["spdx"],
                "reason": f"AGPL 依赖{note}：只要项目以网络服务形式对外提供，即触发整体源码开放义务",
                "advice": "确认是否接受开源全部服务端代码；否则替换该依赖或隔离为独立进程",
            })
        elif cat == "strong-copyleft":
            if not covered:
                undecided.append((r["name"], r["spdx"], "强传染"))
            elif cat not in allowed:
                findings.append({
                    "level": "高", "pkg": r["name"], "license": r["spdx"],
                    "reason": f"{project_license} 项目引入强传染依赖{note}，衍生作品可能需整体以 GPL 开放",
                    "advice": "改用宽松许可替代品，或将该依赖隔离为独立可执行程序并通过进程边界调用",
                })
        elif cat == "weak-copyleft":
            if not covered:
                undecided.append((r["name"], r["spdx"], "弱传染"))
            elif cat not in allowed:
                findings.append({
                    "level": "中", "pkg": r["name"], "license": r["spdx"],
                    "reason": f"弱传染依赖{note}：修改库本体需按原许可开放",
                    "advice": "保持动态链接、不改动库本体，并在清单中注明",
                })

    if undecided:
        # 合成一条，而不是逐条报"冲突"：结论的强度必须配得上证据的强度。
        shown = "、".join(f"{n}({s})" for n, s, _ in undecided[:6])
        more = f" 等共 {len(undecided)} 个" if len(undecided) > 6 else ""
        findings.append({
            "level": "中", "pkg": "（项目整体）", "license": "—",
            "reason": f"项目自身许可证「{project_license}」未纳入兼容性矩阵，"
                      f"无法逐对判定兼容性：涉及 {len(undecided)} 个传染性依赖"
                      f"（{shown}{more}）。本条不是「检出冲突」，而是「无法判定」。",
            "advice": "在 COMPAT_MATRIX 中补入该项目许可证（或改用其规范标识）后重跑；"
                      "此前不要把这批依赖当作已确认冲突处理",
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
        # v0.4：非 OSI / 带附加限制的许可，把"这个许可本身有什么坑"写进义务列，
        # 否则使用者只知道许可证名字，看不出商业风险。
        obs = obligations_of(r["spdx"])
        cn = commercial_note(r["spdx"])
        if cn:
            obs += "；⚠ " + cn
        lines.append("| {} | {} 依赖包 | {} | {} | {} | {}（可信度{}） | "
                     "{} | {} | {} | {} | "
                     "随项目一并声明许可 |".format(
                         r["name"], r["source"], r["version"], r["source"],
                         r["spdx"] if r["spdx"] != "UNKNOWN" else "未识别",
                         r.get("license_field", "?"), r.get("confidence", "?"),
                         usage, obs, boundary, status))
    return "\n".join(lines)


def render_report(project_name, project_license, records, findings, locked=0):
    """把审计结果渲染成 (Markdown 文本, JSON 可序列化字典)。

    从 main() 里抽出来的纯函数，不读不写文件、不依赖命令行参数——
    这样"重算历史扫描数据"这类离线场景可以复用同一套渲染逻辑，
    产出的报告与重新跑一遍工具完全一致，不会因为两处模板各写一遍而对不上。
    """
    notice = matrix_notice(project_license)
    stats = {}
    for r in records:
        c = category_of(r["spdx"])
        stats[c] = stats.get(c, 0) + 1
    ub = unknown_breakdown(records)
    _notes = [(r["name"], r["spdx"], commercial_note(r["spdx"]))
              for r in records if commercial_note(r["spdx"])]

    md = ["# 《开源及第三方资源使用清单》（自动生成）", "",
          f"**项目名称**：{project_name}　**项目自身许可证**：{project_license}　"
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

    # 未识别项归因：让"识别率"这个数字可解释
    if sum(ub.values()):
        md += ["", "## 一·补、未识别项归因", "",
               "| 归因 | 数量 | 是否属于工具的问题 | 处理方式 |",
               "|---|---|---|---|"]
        _tool_fault = {"UNSUPPORTED_LICENSE": "是", "NOT_IN_REGISTRY": "否",
                       "NO_METADATA": "否", "FETCH_FAILED": "否（重跑即可）"}
        for k, v in sorted(ub.items(), key=lambda x: -x[1]):
            if v:
                md.append(f"| {UNKNOWN_KIND_CN.get(k, k)} | {v} | "
                          f"{_tool_fault.get(k, '—')} | {UNKNOWN_KIND_ADVICE.get(k, '')} |")
        resolved = len(records) - sum(ub.values())
        raw_rate = round(100.0 * resolved / len(records), 1) if records else 0.0
        md += ["", f"> 原始识别率 **{raw_rate}%**（{resolved}/{len(records)}）；"
                   f"剔除「源站客观无数据」后为 **{effective_resolve_rate(records)}%**。"
                   "其中只有「知识库未收录」一类属于工具自身的不足，"
                   "补进 SPDX_PATTERNS / LICENSE_DB 即可降低。", ""]

    # 非 OSI / 带附加限制的许可：识别出来还不够，得说清商业后果
    if _notes:
        md += ["", "## 一·再补、非 OSI / 带附加限制的许可", "",
               "| 资源 | 许可证 | 需要注意 |", "|---|---|---|"]
        for n, s, t in _notes:
            md.append(f"| {n} | {s} | {t} |")
        md += ["", "> 这些许可已被正确识别（不再是「未识别」），但条款本身有特殊限制，"
                   "商业使用前请逐条确认。", ""]

    md += ["", "## 二、资源清单", "", to_checklist_table(records), "",
           f"> 项目自身许可证：{project_license}　|　清单由脚本自动生成，"
           f"「识别依据」列标注了每个许可证的判定来源，可信度非「高」的项须人工复核　|　共 {len(records)} 项",
           "> 「使用方式」「自主开发边界」两列需要源码证据，本报告未采集，"
           "统一标注为「待确认」；运行 semantic_audit.py 可补齐这两列"]
    if locked:
        md.append(f"> 版本说明：{locked} 个依赖按清单锁定的精确版本查询许可证（见 JSON 的 requested_version 字段）；"
                  "其余为范围约束，按最新版查询，历史版本许可可能与最新版不同，请定期重跑核对")

    report = {"project": project_name, "project_license": project_license,
              "tool_version": VERSION,
              "records": records, "findings": findings, "stats": stats,
              "unknown_breakdown": ub,
              "effective_resolve_rate": effective_resolve_rate(records),
              "commercial_notes": {n: t for n, _s, t in _notes},
              "notices": [notice] if notice else []}
    return "\n".join(md), report


def report_paths(out):
    """由 --out 推导 (Markdown 报告路径, JSON 报告路径)，两者必定不同。

    v0.4.2：此前 JSON 路径写作 out.replace(".md", ".json")。输出名不含 ".md" 时
    该替换不生效，两条路径指向同一个文件，Markdown 正文被 JSON 静默覆盖
    （`--out out.txt` → 只剩 JSON，终端还打印两个相同路径）。现在只在确实以
    .md 结尾时替换后缀，否则追加 .json，从根上排除同名覆盖。
    """
    out_path = Path(out)
    md_path = out_path
    if out_path.suffix.lower() == ".md":
        json_path = out_path.with_suffix(".json")
    else:
        json_path = Path(str(out_path) + ".json")
    if json_path == md_path:                     # 理论上到不了，兜底
        json_path = Path(str(out_path) + ".json")
    return md_path, json_path


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
    manifest_notes = []      # 清单解析过程中的可读提示（-r 跟随情况等）
    ws_deps = []             # monorepo 工作区内部包（workspace: 标记）
    # 清单解析失败（文件不存在、JSON/TOML 语法错误等）时给出可读提示，
    # 而不是抛出 Python 堆栈——面向学生用户，堆栈会让人以为工具坏了。
    try:
        if a.requirements:
            path = Path(a.requirements)
            if not path.exists():
                ap.error(f"依赖清单不存在：{a.requirements}")
            vpkgs = parse_requirements_verbose(path, notes=manifest_notes)
            pkgs, source = [n for n, _ in vpkgs], "PyPI"
            specs = {n.lower(): s for n, s in vpkgs}
        elif a.pyproject:
            path = Path(a.pyproject)
            if not path.exists():
                ap.error(f"依赖清单不存在：{a.pyproject}")
            vpkgs = parse_pyproject_verbose(path)
            pkgs, source = [n for n, _ in vpkgs], "PyPI"
            specs = {n.lower(): s for n, s in vpkgs}
        elif a.package_json:
            path = Path(a.package_json)
            if not path.exists():
                ap.error(f"依赖清单不存在：{a.package_json}")
            vpkgs = parse_package_json_verbose(path)
            # v0.4.2：CLI 此前不做工作区排除，monorepo 的内部包会被当成第三方
            # 依赖去查 npm，查不到就记「源站无此包」并报中危——与 README 宣称的
            # 「已按 workspace: 标记识别并排除」不符（检查清单 P1-1）。
            vpkgs, ws_deps = exclude_workspace_deps(vpkgs)
            pkgs, source = [n for n, _ in vpkgs], "npm"
            specs = {n.lower(): s for n, s in vpkgs}
        elif a.packages:
            pkgs, source = a.packages, "PyPI"
        else:
            ap.error("需要 --requirements / --pyproject / --package-json / --packages 之一")
    except SystemExit:
        raise
    except json.JSONDecodeError as e:
        ap.error(f"依赖清单不是合法的 JSON（第 {e.lineno} 行第 {e.colno} 列）：{e.msg}")
    except Exception as e:
        ap.error(f"解析依赖清单失败：{type(e).__name__}: {e}")

    if not pkgs:
        print(f"[1/4] 解析依赖清单：0 个直接依赖（来源 {source}）")
        print("     清单里没有可解析的依赖项，将生成一份空清单报告。")
    else:
        print(f"[1/4] 解析依赖清单：{len(pkgs)} 个直接依赖（来源 {source}）")
    # 清单结构与排除项都显式说出来：静默少解析 / 静默排除是最难排查的两类问题
    for _n in manifest_notes:
        print(f"     · {_n}")
    if ws_deps:
        shown = "、".join(ws_deps[:5]) + ("…" if len(ws_deps) > 5 else "")
        print(f"     · 已排除 {len(ws_deps)} 个 monorepo 工作区内部包"
              f"（workspace: 标记，不发布到 npm、不参与审计）：{shown}")

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

    # v0.4：把"未识别"拆开说清楚。97.8% 这种单一数字既惩罚了工具无能，
    # 也惩罚了源站没数据；拆开之后才知道该补知识库还是该去人工核对。
    ub = unknown_breakdown(records)
    if sum(ub.values()):
        print("     未识别项归因：" + "，".join(
            f"{UNKNOWN_KIND_CN[k]} {v}" for k, v in
            sorted(ub.items(), key=lambda x: -x[1]) if v))
        print(f"     · 应由工具改进的（知识库未收录）：{ub['UNSUPPORTED_LICENSE']} 条")
        print(f"     · 剔除源站无数据后的识别率：{effective_resolve_rate(records)}%"
              f"（原始 {round(100.0 * (len(records) - sum(ub.values())) / len(records), 1) if records else 0.0}%）")

    md_text, report = render_report(a.project_name, a.project_license,
                                    records, findings, locked)
    md_path, json_path = report_paths(a.out)
    md_path.write_text(md_text, encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"报告已写出：{md_path}（Markdown） / {json_path}（JSON）")
    if Path(a.out).suffix.lower() != ".md":
        print(f"     提示：--out 未以 .md 结尾，JSON 报告已另存为 {json_path.name}，"
              "两份报告互不覆盖")


if __name__ == "__main__":
    main()
