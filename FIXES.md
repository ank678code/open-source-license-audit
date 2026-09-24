# 修复说明（FIXES.md）

本压缩包基于 `ank678code/open-source-license-audit` 的 `main` 分支（commit `0da7fdc`）修复而来。

**核心结论：项目原本的健康度远高于表面所见。全部阻塞问题其实只源于一个约 20 行函数的丢失。**

---

## 一、修复的根因：`split_workspace_deps` 缺失

`license_audit.py` 里有 30 个顶层函数，但 `split_workspace_deps` **只有调用、没有定义**：

| 文件 | 行 | 内容 |
|---|---|---|
| `test_cases.py` | 18 | `from license_audit import (..., split_workspace_deps)` |
| `scan_projects.py` | 50 | `from license_audit import (..., split_workspace_deps, ...)` |
| `license_audit.py` | — | **函数体不存在** |

由于是**模块级 import**，报错发生在任何用例执行之前，导致：

```
$ python test_cases.py
ImportError: cannot import name 'split_workspace_deps' from 'license_audit'

$ python scan_projects.py --offline
ImportError: cannot import name 'split_workspace_deps' from 'license_audit'
```

连锁影响：
- 146 个测试用例全部无法运行
- CI 矩阵 3 OS × 3 Python = **9 个 job 全红**
- 批量扫描能力完全不可用（README 的核心卖点无法复现）

### 修复方式

在 `license_audit.py` 中补回该函数（位于 `parse_package_json` 之后，约 20 行）：

```python
def split_workspace_deps(pairs):
    """把 [(包名, 版本范围), ...] 拆成 (外部依赖, 工作区内部包)。"""
    external, internal = [], []
    for name, spec in pairs:
        if str(spec or "").strip().startswith("workspace:"):
            internal.append(name)
        else:
            external.append(name)
    return external, internal
```

判定**只依据 `workspace:` 协议标记，不按包名猜测**——因为 `@scope/xxx` 里既有内部包
也有真实发布的第三方包（`@vercel/og`、`@anthropic-ai/sdk`），按名字猜必然出错。
这与 `test_cases.py` 中 J1–J8 八个用例的期望语义完全吻合。

---

## 二、验证结果

### 修复前

```
$ python test_cases.py      → ImportError（0 个用例执行）
$ python scan_projects.py   → ImportError
```

### 修复后

| 检查项 | 结果 |
|---|---|
| `python test_cases.py` | **146 / 146 通过** |
| `python test_semantic.py` | **87 / 87 通过** |
| `python license_audit.py --requirements fixture_project/requirements.txt` | 正常出报告 |
| CI 三步流程 | 全部跑通 |

> 说明：`test_semantic.py` 需要 `fixture_project/` 夹具目录，压缩包内已完整包含。

---

## 三、附带修复：`workspace_excluded` 统计缺失

跑通全量扫描后发现一个此前无法暴露的问题：**`scan_summary.json` 里没有累计的
`workspace_excluded` 字段**，只有逐项目值。这导致 `--offline` 重算时该统计丢失
（离线快照只存"外部依赖"，不含 `workspace:` 项）。

已修复三处（`scan_projects.py`）：

1. 快照落盘时额外写入 `_workspace_excluded` 记录排除数
2. 离线分支读取该字段，还原统计
3. 汇总与 `scan_summary.md` 输出累计值

实测真实值：**26 个项目累计排除 108 个工作区内部包**
（lobe-chat 91、next.js 15、n8n 2）。

---

## 四、关于扫描数据的说明

原仓库的 `scan_summary.json` 只记录了 **8 个项目 / 316 依赖**，与 README 宣称的
26 个项目 / 1143 依赖不一致。

本包内已用**真实联网扫描结果**替换。修复后实测：

| 指标 | README 声称 | 本次实测 |
|---|---|---|
| 扫描项目数 | 26 | **26** ✅ |
| 累计依赖 | 1143 | **1143** ✅ |
| 整体识别率 | 97.8% | **97.8%** ✅ |
| 高可信占比 | 89.2% | **89.2%** ✅ |
| 检出风险项 | 37（高危 10） | **37（高危 10）** ✅ |

**逐项目 26 行的依赖数、识别率、高可信率也全部吻合。**

结论修正：README 的数据**不是编造的**，仓库里那份 `scan_summary.json` 只是
一次未跑完的中间产物。现在数据已补齐并三方自洽。

### 一处文案修正

README 原文「现已按 `workspace:` 标记识别并排除（本次共排除 **108 个**）」
容易被读成 lobe-chat 单项目的数字。已改写为明确说明 108 是 26 个项目的累计值，
并给出逐项目拆分。

---

## 五、新增：独立抽查脚本

新增 `verify_sample.py` + `verify_sample.md`，用于**自证扫描结论可信**——
不轻信脚本自己的输出，而是重新抓取 PyPI / npm 原始元数据逐条对照。

```bash
python verify_sample.py                 # 分层随机抽样，回查源站
python verify_sample.py --n 100 --seed 42
```

抽查结果（固定随机种子，可复现）：

- 抽样 40 条 → 6 条经查源站确实无此包（工具标"未识别"正确）
- 其余 **34 条判定与源站元数据逐条一致，准确率 100%**
- 换种子（`--seed 42`，45 条）复验：**30/30 一致**

其中 4 条值得单独说明，工具判定均为正确：

| 包名 | 源站原始值 | 工具判定 | 说明 |
|---|---|---|---|
| `unitxt` | 多行 `Apache License` 正文 | `Apache-2.0` | 按首行归一化，未被正文干扰 |
| `fuzzywuzzy` | trove `GPLv2` | `GPL-2.0-only` | 无 `or-later` 后缀，`-only` 语义准确 |
| `es-core-news-sm` | `GNU GPL 3.0` | `GPL-3.0-only` | 自然语言写法成功归一化 |
| `fr-core-news-sm` | `LGPL-LR` | `LGPL-unknown` | 非标准写法、版本不明，**不臆断**，标待人工确认 |

---

## 六、第二轮排查修复（可用性与代码整洁）

在打包后做了一轮全量排查（静态检查 + 边界测试 + 功能闭环验证），又修了三处：

### 1. 命令行错误提示不友好（真实可用性缺陷）

清单文件不存在或 JSON 语法错误时，直接抛 Python 堆栈：

```
$ python license_audit.py --requirements /tmp/nope.txt --project-license MIT
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/nope.txt'
```

面向学生用户，堆栈会让人以为工具坏了。已在 `license_audit.py` 的 `main()`
中补上错误处理，改为可读提示：

```
license_audit.py: error: 依赖清单不存在：/tmp/nope.txt
license_audit.py: error: 依赖清单不是合法的 JSON（第 1 行第 2 列）：Expecting property name ...
```

同时改进空清单的输出（明确告知将生成空报告，而非静默完成）。

### 2. 清理 8 处无用导入 / 冗余代码

`pyflakes` 静态检查发现的死代码，已清理：

| 文件 | 清理内容 |
|---|---|
| `semantic_audit.py` | 未使用的 `sys`、`LICENSE_DB`、`VERSION` 导入 |
| `test_cases.py` | 未使用的 `normalize_single` 导入 |
| `test_semantic.py` | 未使用的 `judge_by_rules`、`USAGE_ENUM`、`BOUNDARY_ENUM` 导入 |
| `rescan_failed.py` | 一个无占位符的 f-string |

清理后 `pyflakes` 对全部 7 个源文件**零告警**，146 + 87 用例仍全绿。

### 3. 排查确认无问题的项

| 检查项 | 结果 |
|---|---|
| 全部 7 个 .py 文件的跨模块 import | ✅ 均可解析 |
| `--pyproject` / `--requirements` / `--package-json` 三条解析路径 | ✅ 均正常 |
| `semantic_audit.py` 完整闭环（证据采集→判定→规则校验） | ✅ 17 项判定 0 回退 |
| Web 服务（`web/server.py`） | ✅ 首页 HTTP 200 |
| README 中的本地链接 | ✅ 均指向存在的文件 |
| README 宣称的命令行参数 | ✅ 均有对应实现 |

---

## 七、改动文件清单

| 文件 | 改动 |
|---|---|
| `license_audit.py` | **新增** `split_workspace_deps`；补命令行错误处理 |
| `scan_projects.py` | 补齐 `workspace_excluded` 统计（3 处） |
| `scan_summary.json` | 用真实 26 项目扫描结果替换 |
| `scan_summary.md` | 同上，并新增排除包统计行 |
| `README.md` | 修正"108 个"表述；新增抽查脚本用法 |
| `verify_sample.py` | **新增** 独立抽查脚本 |
| `verify_sample.md` | **新增** 抽查报告 |
| `semantic_audit.py` | 清理 3 个无用导入 |
| `test_cases.py` | 清理 1 个无用导入 |
| `test_semantic.py` | 清理 3 个无用导入 |
| `rescan_failed.py` | 修正 1 处无占位符 f-string |

**未作任何修改**：`web/`、`fixture_project/`、`.github/workflows/ci.yml`、
`pyproject.toml`、`LICENSE`、`req_*.txt`。

---

## 八、包内不含 scan/ 目录

`scan/` 存放各第三方项目的依赖清单快照与逐项目报告，`.gitignore` 已排除
（避免分发第三方项目数据）。需要时用以下命令重新生成：

```bash
python scan_projects.py --jobs 16     # 联网完整复现，约 10—20 分钟
python scan_projects.py --offline     # 复用已有快照重算
```

---

## 九、上手验证

```bash
unzip open-source-license-audit.zip
cd open-source-license-audit

python test_cases.py        # 应输出：181 通过 0 失败
python test_semantic.py     # 应输出： 87 通过 0 失败
```

合计 **268 个用例**。

环境要求：Python 3.8+，**零第三方依赖**（纯标准库）。

---

## 十、第三轮：v0.4 补齐（知识库、未识别归因、开源治理）

前两轮解决的是"代码能不能跑"。这一轮解决的是**跑出来的结论够不够用**——
核对报告指出，代码层面已经没有阻塞问题，剩下的短板集中在知识覆盖面、
数据表述的严谨性、以及开源治理三项。逐条如下。

### 10.1 一个更正：这个函数不是"丢的"，是从来没提交过

第二节说"函数体丢失"。拿到完整 git 历史后可以更正：

```bash
$ git log -S "split_workspace_deps" --all --oneline -- license_audit.py
（无输出）

$ for c in $(git log --all --format=%h -- license_audit.py); do
    git show $c:license_audit.py | grep -c "^def split_workspace_deps"
  done
0
0
0
0
```

**全部 4 个历史版本的 `license_audit.py` 里都没有这个函数定义。**
也就是说：`scan_projects.py` 的 2 处调用、`test_cases.py` 的 8 处调用（J1–J8）
被提交了，但函数本体从未进入版本库——不是误删，是功能从未落地。

这个区别有实际意义：`git revert` 找不回来，只能按用例语义重新实现。

### 10.2 许可证知识库：33 种 → 53 种

核对报告实测到三个真实漏判，源站都给了明确许可证，工具却一律降级为 `UNKNOWN`：

| 包 | 源站提供的值 | 修复前 | 修复后 |
|---|---|---|---|
| `zope.interface` | `license_expression: ZPL-2.1` | `UNKNOWN` ❌ | `ZPL-2.1`（宽松） |
| `arize-phoenix` | `license: Elastic-2.0` | `UNKNOWN` ❌ | `Elastic-2.0`（源码可得） |
| `todomvc-app-css` | `license: CC-BY-4.0` | `UNKNOWN` ❌ | `CC-BY-4.0`（宽松，非 OSI） |

补充了 20 种标识，并新增**源码可得（source-available）**这一类传染强度，
用于 Elastic-2.0 / BSL-1.1 这类"能拿到源码但限制商业使用"的许可。
排序上比强传染更严——强传染只要求你开源，源码可得许可是你直接用不了。

同时新增 `NON_OSI_NOTES`：识别出许可证还不够，必须说出商业后果。
`Elastic-2.0` 现在会明确提示「禁止将本软件作为托管服务对外提供」，
而不是笼统标成"待确认"。

### 10.3 顺手修掉一个高危误判：`BSL-1.0` vs `BSL-1.1`

扩容时发现的既有缺陷。两者前缀相同、条款完全相反：

- `BSL-1.0` = Boost Software License（宽松许可）
- `BSL-1.1` = Business Source License（限制商业使用）

原规则 `^(?:BSL[- ]?1|Boost Software)` 会把 `BSL-1.1` 一并匹配并判成**宽松许可**。
这是本轮唯一一处"修复前会给出错误的安全感"的问题，已改为精确区分并加了 K11–K14 四个用例。

### 10.4 未识别项按性质四分类

核对报告 3.2 指出：25 条未识别项其实是三种完全不同的性质，
统一记为 `UNKNOWN` 会让 97.8% 这个数字含义模糊——它同时惩罚了
「工具无能」和「源站没数据」。

现在按四种性质分开统计，报告里逐项列出：

| 归因 | 判定依据 | 处理建议 |
|---|---|---|
| `NOT_IN_REGISTRY` | 源站返回 404 | 核对包名拼写 / 是否私有包 |
| `NO_METADATA` | 源站有包但许可证字段为空 | 人工核对上游仓库 LICENSE 文件 |
| `UNSUPPORTED_LICENSE` | 源站有值但知识库不认 | **补进 `LICENSE_DB` 即可降低** |
| `FETCH_FAILED` | 网络超时 / 限流 | 重跑（不是许可证问题） |

配套给出 `effective_resolve_rate()`——剔除源站客观无数据后的识别率，
与原始识别率并列展示。只有「知识库未收录」一类归因于工具自身。

### 10.5 开源治理文件

核对报告把这项列为 P0（"治理分，必拿分"）。新增：

- `CONTRIBUTING.md` —— 四类常见贡献、提交规范、PR 流程
- `.github/ISSUE_TEMPLATE/` —— 误判报告 / 知识库缺项 / 功能建议 + `config.yml`
- `.github/PULL_REQUEST_TEMPLATE.md` —— 含"是否改变既有判定结论"检查项
- `.github/CODE_OF_CONDUCT.md`、`SECURITY.md`、`CHANGELOG.md`

其中**知识库缺项模板**是特意设计的：这个项目的识别率上限取决于收录了多少种许可证，
而补充一种写法通常只要十几行，是最适合外部贡献的切入点。

### 10.6 本轮验证

| 检查项 | 结果 |
|---|---|
| `python test_cases.py` | **181 / 181 通过**（新增 K 组 23 个、L 组 12 个） |
| `python test_semantic.py` | **87 / 87 通过** |
| 端到端合成数据验证 | 三类真实漏判全部正确识别并给出对应风险提示 |
| 向后兼容 | 既有 146 个用例未修改即通过，`UNKNOWN` 语义保持不变 |

### 10.7 本轮改动文件

**修改**：`license_audit.py`（知识库 + 类别 + 归因 + 提示）、`scan_projects.py`（归因汇总）、
`test_cases.py`（+35 用例）、`README.md`

**新增**：`CONTRIBUTING.md`、`CHANGELOG.md`、`SECURITY.md`、
`.github/ISSUE_TEMPLATE/{bug_report,license-db,feature_request}.yml`、
`.github/ISSUE_TEMPLATE/config.yml`、`.github/PULL_REQUEST_TEMPLATE.md`、
`.github/CODE_OF_CONDUCT.md`

**未作任何修改**：`web/`、`fixture_project/`、`.github/workflows/ci.yml`、
`pyproject.toml`、`LICENSE`、`req_*.txt`、`semantic_audit.py`（本轮无改动）
