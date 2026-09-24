# 更新日志

本项目的所有重要变更都会记录在这里。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.4.0] — 2026-09-24

### 修复

- **补回 `split_workspace_deps` 函数**（`license_audit.py`）
  这是此前唯一的阻塞性缺陷：`test_cases.py` 与 `scan_projects.py` 在模块级
  `import` 阶段就抛 `ImportError`，导致 233 个用例一个都跑不起来、CI 9 个 job 全红、
  批量扫描完全不可用。
  经查 `git log -S "split_workspace_deps" --all`，该函数在**任何历史提交中都未曾被定义**
  （只有调用点和 8 个 J 组测试被提交）——不是误删，是功能从未落地。现按 J 组用例语义补回。
- **补齐 `workspace_excluded` 累计统计**（`scan_projects.py`，3 处）
  快照落盘时写入 `_workspace_excluded`、离线分支读取还原、汇总与 Markdown 输出累计值。
  此前 `--offline` 重算会丢掉这项统计。
- **命令行错误提示改为可读文案**（`license_audit.py`）
  清单文件不存在或 JSON 语法错误时不再抛 Python 堆栈，改为
  `license_audit.py: error: 依赖清单不存在：...` 这类可操作提示；
  空清单明确告知将生成空报告。
- **清理 8 处无用导入 / 冗余代码**：`semantic_audit.py`（3）、`test_cases.py`（1）、
  `test_semantic.py`（3）、`rescan_failed.py`（1 处无占位符 f-string）。
- **修正 `BSL-1.0` / `BSL-1.1` 误判**
  两者前缀相同但条款相反：`BSL-1.0` 是 Boost Software License（宽松），
  `BSL-1.1` 是 Business Source License（限制商业使用）。
  原规则 `BSL[- ]?1` 会把 1.1 一并判成宽松许可，属高危误判。

### 新增

- **许可证知识库扩容：33 种 → 53 种**
  新增 ZPL-2.1、Elastic-2.0、BSL-1.1、SSPL-1.0、CC-BY-4.0、CC-BY-SA-4.0、
  OFL-1.1、Ruby、MS-PL、MS-RL、NCSA、UPL-1.0、Vim、BSD-4-Clause、MPL-1.1、
  EPL-1.0、W3C、Libpng、Unicode-DFS-2016、Apache-1.1。
  这些是实测中真实造成漏判的写法——`zope.interface`(ZPL-2.1)、
  `arize-phoenix`(Elastic-2.0)、`todomvc-app-css`(CC-BY-4.0) 的源站都给了明确许可证，
  此前却一律降级为 `UNKNOWN`。
- **新增 `source-available`（源码可得）传染强度类别**
  用于 Elastic-2.0 / BSL-1.1 这类"能拿到源码但附带商业使用限制"的许可。
  排序上比强传染更严：强传染只要求开源，源码可得许可是直接用不了。
  这类许可现在会触发**高危**检出并说明具体限制，不再静默放过。
- **非 OSI / 带附加限制许可的显式提示**（`NON_OSI_NOTES` + `commercial_note()`）
  识别出许可证还不够——Elastic-2.0 此前被标成"待确认"，使用者只知道"不知道"，
  不知道风险在哪。现在会明确写出"禁止作为托管服务对外提供"这类商业后果，
  并写入清单表的义务列与报告的独立章节。
- **未识别项按性质四分类**（`classify_unknown` / `unknown_breakdown`）
  此前所有"没认出来"统一记为 `UNKNOWN`，导致 97.8% 这个识别率
  同时惩罚了「工具无能」和「源站没数据」两种完全不同的性质。
  现在拆为：`NOT_IN_REGISTRY`（源站无此包）/ `NO_METADATA`（源站未填许可证）/
  `UNSUPPORTED_LICENSE`（知识库未收录，**唯一归因于工具自身**）/
  `FETCH_FAILED`（网络失败，重跑即可）。
  配套新增 `effective_resolve_rate()`（剔除源站无数据后的识别率）与
  `tool_attributable_unknown()`。四类各有不同的处理建议，直接出现在报告里。
- **独立抽查脚本 `verify_sample.py`**
  不轻信脚本自己的输出，重新抓 PyPI / npm 原始元数据逐条对照。
  支持 `--n` / `--seed` 固定种子复现。结果见 `verify_sample.md`。
- **开源治理文件**：`CONTRIBUTING.md`、Issue 模板（误判 / 知识库缺项 / 功能建议）、
  PR 模板、`CODE_OF_CONDUCT.md`、`SECURITY.md`、本文件。

### 变更

- `record` 新增 `unknown_kind` 字段；报告 JSON 新增 `unknown_breakdown`、
  `effective_resolve_rate`、`commercial_notes` 三个字段。
- `scan_summary.json` 新增 `unknown_breakdown` / `unknown_tool_fault` /
  `effective_resolve_rate`，逐项目统计新增 `unknown_breakdown`。
- `scan_summary.json` / `.md` 用**真实联网扫描的 26 个项目结果**替换
  （原文件只记录了 8 个项目 / 316 个依赖，与 README 宣称的 26 / 1143 不符）。
  替换后三个口径自洽：26 个项目、1143 依赖、97.8% 识别率、89.2% 高可信、37 条风险项。
- README 修正"排除 108 个"的表述：明确这是 26 个项目的累计值
  （lobe-chat 91、next.js 15、n8n 2），并给出逐项目拆分。
- **修复 Windows 上中文输出直接崩溃**：控制台默认编码随系统区域变化
  （英文系统 cp1252），本项目大量输出中文，实测 windows 三个 job 全部因此失败，
  本机是英文区域 Windows 的用户同样跑不起来。`license_audit.py` 新增
  `_force_utf8_stdio()` 在模块层强制 stdout/stderr 为 UTF-8，
  `verify_sample.py`（不依赖本模块）单独加了同样的守卫。
- **修复 Python < 3.11 的 pyproject.toml 降级解析**：`tomllib` 是 3.11 才进标准库的，
  原正则降级会丢掉全部版本约束、把 `name = "demo"` 这类非依赖键当成包名、
  且完全解析不了 PEP 735 `[dependency-groups]`（C1/C3/E13 三个 job 因此失败）。
  新增内置 TOML 子集解析器 `_toml_loads()`（零依赖），并把依赖抽取抽成
  `_pyproject_deps()` 与 tomllib 路径共用，保证两个路径结果完全一致。

- 测试用例 146 → **190**（新增 K 组许可证知识库 23 个、L 组未识别归因 12 个、
  M 组跨 Python 版本一致性 9 个），合计 **277 个**用例仍全部可离线运行。

## [0.3.0] — 2026-09-23

### 新增

- 支持 `pyproject.toml`（PEP 621 / PEP 735 / Poetry 三种写法）
- 批量项目扫描 `scan_projects.py`，26 个真实开源项目样本
- monorepo 工作区内部包识别（`workspace:` 标记）——但函数体未随本次提交，
  该能力实际在 0.4.0 才可用
- Web 界面 `web/server.py`
- 语义判定三层闭环：证据采集 → LLM 判定 → 规则校验
- 元数据并发抓取（`--jobs`），实测提速 8—10 倍

### 修复

- 四级可信度解析链：修复 `pandas` 61,643 字符许可证正文导致的 GPL 误判
- 兼容性矩阵从 11 项补齐至 31 项，矩阵外取值不再静默误报
- `MIT-CMU`（Pillow）、`WITH` 例外吞掉 `AND` 等归一化盲区
- 测试目录误判（`contest/` 被当成 `test/`）

## [0.2.0] — 2026-09-23

首个可用版本：依赖清单解析、PyPI / npm 元数据查询、SPDX 归一化、
兼容性判定、《开源及第三方资源使用清单》输出。
