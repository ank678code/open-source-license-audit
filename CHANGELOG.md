# 更新日志

本项目的所有重要变更都会记录在这里。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.4.2] — 2026-09-25

按第三方《仓库问题检查清单》逐项修复。清单列出 11 项（高 2 / 中 4 / 低 5），
逐条**先复现再改**——其中 1 项（P2-2 的成因判断）复现后与清单描述不同，
已在 FIXES 第十三节说明。本轮含行为变更，按语义化版本作为补丁版升起。

### 修复

- **CLI 与 Web 入口未排除 monorepo 工作区内部包**（P1-1，高危）
  排除逻辑只有 `scan_projects.py` 做了，`license_audit.py --package-json` 与
  `web/server.py` 会把 `workspace:*` 内部包当成第三方依赖去查 npm，返回 404 后
  记成「源站无此包」并报中危——与 README「现已按 `workspace:` 标记识别并排除」
  的宣称直接矛盾。现在三个入口共用 `split_workspace_deps()`，且 CLI 会打印
  排除数量、Web 会在 payload 里返回 `workspace_excluded`，不做静默排除。
- **`--out` 不含 `.md` 时 JSON 报告覆盖 Markdown 报告**（P1-2，高危）
  JSON 路径原先写作 `out.replace(".md", ".json")`；输出名不含 `.md` 时替换不
  生效，两条路径指向同一文件，Markdown 正文被静默覆盖（`--out out.txt` 只剩
  JSON，终端还打印两个相同路径）。改为 `report_paths()` 统一推导：以 `.md`
  结尾才换后缀，否则追加 `.json`，两个路径必定不同。
- **`rescan_failed.py` 重写汇总时丢失 v0.4 新增字段**（P2-3）
  该脚本整份重建 `scan_summary.json`，导致 `unknown_breakdown` /
  `unknown_tool_fault` / `effective_resolve_rate` / `workspace_excluded` /
  `ecosystem` / `manifest` 全部丢失——跑一次重扫就把汇总降级成旧结构。
  现在以既有汇总里的同项目行为基底再更新；同时重抓时带上
  `requested_version`（此前固定抓最新版，回填结果与锁定版本口径不一致）。
- **`FIXES.md` 引用的 `recompute_scan.py` 不在仓库里**（P2-4）
  离线重算脚本只存在于开发机上，读者按 FIXES 第十二节指引无法复现历史数据。
  已通用化后补进仓库（`--data` / `--code` / `--dry-run`），并加入 `py-modules`
  与 `license-audit-recompute` 命令入口。
- **证据采集的依赖归属使用子串匹配**（P3-1）
  `in_requirements` 用「包名小写是否出现在清单文本里」判定，`torch` 会被
  `torchvision` 命中、`pytest` 会被 `pytest-cov` 命中，而这份证据是要喂给
  模型做合规判断的。改为按清单格式解析出包名、按 PEP 503 归一化后精确比对，
  并在证据里注明命中哪份清单。`setup.py` 用受限文本抽取（不执行脚本）。
- **Web 端解压缺少规模限制、非法参数回 500**（P3-2）
  请求体上限只约束压缩包本身，解压后可膨胀到任意大小。新增解压后总大小
  （200MB）与条目数（2000）上限；非法的 `kind` 参数此前 KeyError 被兜底
  `except` 捕获返回 500，现在返回 400 并列出可选值。
- **抽查脚本保留不可达的宽容分支**（P3-3）
  `verify_sample.loose_match()` 仍保留「工具判 UNKNOWN 直接算一致」的分支，
  主流程已前置分流，该分支不可达，却会误导读者以为抽查口径仍然宽松。已删除。
- **CLI 不跟随 `-r` 指针清单**（P3-4 / P3-5）
  真实项目的 `requirements.txt` 常常只是一行 `-r requirements/runtime.txt`；
  CLI 对 `-` 开头的行直接跳过，会静默解析出 0 个依赖且不给任何提示，而
  `scan_projects.py` 与 Web 端却已实现跟随。现统一到
  `parse_requirements_verbose()`：递归跟随、环路与深度保护、路径围栏
  （`path_within()`，拒绝绝对路径 / `~` / 盘符相对路径 / 越界 `..`），
  并把「跟随了几个引用、哪些被跳过」作为提示打印出来。
- **文档与代码里的版本标注冲突**（P2-1 / P2-2）
  README 测试小节仍写 196 / 283（实际 224 / 311）；代码注释与 `verify_sample.md`
  把 v0.4.1 的修复标成 `v0.5`，而 `CHANGELOG` 与 `pyproject.toml` 都没有 0.5。
  已统一为实际发布版本，并新增用例守护「源码中不得出现高于 `VERSION` 的版本标注」。

### 测试

- `test_cases.py` 224 → **251**（新增 O 组 26 个：入口一致性、输出路径、
  `-r` 跟随与围栏、版本标注守护、解压限制、清单类型校验；N 组补 1 个）
- `test_semantic.py` 87 → **95**（新增 J 组 8 个：依赖归属精确匹配）
- 合计 **346 个用例**，全部可离线运行，`pyflakes` 零告警
- 因 `web/server.py` 的 `detect_and_parse()` 返回值新增「已排除的工作区内部包」
  一项，N19–N21 三个用例的解包方式同步更新（断言语义未变）

## [0.4.1] — 2026-09-24

按第三方审查报告（`open-source-license-audit 项目审查报告.docx`）逐项修复。
报告列出 8 个问题，逐条先复现再改；本轮含**改变既有判定结论**与**安全**修复，
故按语义化版本作为补丁版升起。

### 修复

- **兼容矩阵未覆盖引发系统性误报**（H2，高危）
  `detect_conflicts` 直接用**原始写法**查 `COMPAT_MATRIX`，查不到就
  `get(..., set())` 拿到空集合，退化成「该项目下所有传染性依赖都冲突」。
  `scan_projects.py` 里 matplotlib 的项目许可证写的是 `PSF-based`，
  于是 `certifi` / `pikepdf` / `pytest-rerunfailures` 三个 MPL-2.0 依赖
  被全部报成中危。而 `PSF-based` 归一化后本就是 `PSF-2.0`，矩阵里一直有它。
  改为先经 `resolve_project_license()` 归一化再查表；矩阵确实未覆盖时
  **不再逐条下冲突结论**，改为合成一条「无法判定」并点名涉及的依赖。
  全库风险项 33 → **30**（即那 3 条误报），高危仍为 10。
- **Web 端任意文件读取面**（M1，安全）
  `web/server.py` 跟随 `-r` 引用时计算 `q = p.parent / inc`；`inc` 为绝对路径时
  pathlib 会用绝对路径**替换**基础路径，`-r /etc/passwd` 即可读到解压目录外的
  任意文件并回显解析结果。新增 `path_within()` 围栏（v0.4.2 起提到 `license_audit` 供 CLI 与 Web 共用），
  用 `resolve()` 后路径判定是否
  落在解压目录内（覆盖 `..` 与符号链接），并拒绝绝对路径 / `~` / 盘符相对路径。
- **模型提示词注入面**（M2）
  上传压缩包的文件名/路径被直接拼进发给模型的提示词。新增
  `sanitize_evidence_token()` 按字符白名单清洗并截断；`build_user_prompt`
  中的包名同样清洗。规则校验层仍是兜底，但不再由它独自承担。
- **抽查口径偏宽**（M3）
  `verify_sample.py` 的 `loose_match` 把工具判 `UNKNOWN` 的条目**直接计为一致**，
  源站明确给了值却没认出来的真实漏判被算成了正确。现改为同时输出
  **严口径**（只做机械规范化、不查映射表）与**宽口径**两个数字，
  并拆成五个互斥桶：严一致 / 仅宽一致 / 家族已识别版本待确认 /
  源站有值但工具未识别 / 不一致。
- **交付数据滞后于代码**（H1）
  仓库内 `scan_summary.json` 的 `tool_version` 仍为 `0.3`，整组数字是旧口径；
  README 实测数据段同理。已用当前判定逻辑重算 26 个项目并全部刷新。
  同时修正 README 归因表中两处**过时举例**（`azure-identity` 已被识别为 MIT、
  `ZPL-2.1` 已进知识库），改用当前真实例子。
- **版本号三处不一致**（L1）
  `pyproject.toml`(0.3.0) / 代码 `VERSION`("0.4") / `CHANGELOG`([0.4.0]) 互不相同。
  现统一为 `0.4.1`，并新增用例直接比对代码与 `pyproject.toml`，防止再次漂移。
- **文档用例数与实际不符**（L2）
  README / FIXES 写「190 个用例、合计 277」，实际为 196 / 283。
- 清理 `web/server.py` 中 4 个未使用的导入。

### 新增

- **`Sustainable-Use-1.0`**（n8n 等项目自身使用的源码可得许可）
  补入 `SPDX_PATTERNS` / `LICENSE_DB` / `NON_OSI_NOTES` / `COMPAT_MATRIX`。
  此前归一化不到，连带使 n8n 在矩阵中查不到——与 H2 是同一类问题。
- **`resolve_project_license()`**：项目自身许可证的归一化入口，
  `matrix_notice()` 一并改用它，避免对可归一化的写法误报告警。
- **打包入口补齐**（L3）
  `pyproject.toml` 此前只打包 `license_audit` / `semantic_audit` 两个模块，
  批量扫描、抽查脚本与 Web 界面都没有分发入口。现补 `py-modules`（5 个）、
  `web` 包（新增 `__init__.py`）、6 个 console scripts，并把 `web/index.html`
  纳入 package-data。
- **CI 增加静态检查**：pyflakes（含 `web/`），只在单个矩阵格子里跑。

### 测试

- 回归用例 196 → **224**（新增 N 组 26 个、B9b/B9c 2 个），
  连同 `test_semantic.py` 的 87 个，合计 **311 个**，全部离线可运行。
- `B9` 按新契约重写：矩阵未覆盖时不再逐条报冲突，但必须在「无法判定」中
  点名涉及的依赖——**暴露不确定性不等于丢掉信息**。

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

- 测试用例 146 → **196**（新增 K 组许可证知识库 25 个、L 组未识别归因 16 个、
  M 组跨 Python 版本一致性 9 个），连同 `test_semantic.py` 的 87 个，
  合计 **283 个**用例仍全部可离线运行。

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
