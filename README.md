# open-source-license-audit

开源许可证合规检查 + 《开源及第三方资源使用清单》生成器。

面向高校开源项目团队与学生开发者：**一条命令查出依赖里藏着的 GPL / AGPL**，并生成一份可以直接提交的合规清单。

## 为什么需要它

绝大多数团队并不知道自己的依赖里有传染性许可。我们实测了一个真实项目：

> [OpenCompass](https://github.com/open-compass/opencompass) 自身是 **Apache-2.0** 许可，但它的直接依赖里有两个强传染许可包：
> - `fuzzywuzzy` → **GPL-2.0-only**
> - `python-Levenshtein` → **GPL-2.0-or-later**

这两个包在中文 NLP 项目里被大量引用。现有工具（ScanCode、FOSSA 等）偏企业级、面向整个仓库而非依赖清单，也不会输出中文的合规字段，学生用不起来。

## 它和"简单查一下许可证"的区别

许可证元数据的脏乱程度远超预期。以下是开发过程中实测踩到的坑，全部已在代码中处理：

| 现象 | 实际情况 | 处理方式 |
|---|---|---|
| `pandas` 被误判为 GPL | 它的 `license` 字段是 **61,643 字符**的完整许可证正文，正文里顺带提到了 "GNU General Public License" | 四级可信度解析链：`license_expression` > trove classifier > license 首行 > 截断兜底，全文关键词匹配被彻底禁用 |
| `numpy` 的许可证 | 值是复合表达式 `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | 按 AND/OR/WITH 拆分逐项归一化；AND 须全部满足取最严格项，OR 可任选其一取最宽松项 |
| `psycopg2` 无法识别 | trove classifier 写的是 `GNU Library or Lesser General Public License (LGPL)`——自然语言里的 " or " 不是 SPDX 运算符 | 增加表达式形态判定，只在分隔符两侧都是裸标识符时才拆分 |
| `mysqlclient` 版本判错 | 是 `GPL-2.0-or-later`，与 `-only` 的兼容性含义不同 | 保留 `-or-later` / `+` / `v2 or later` 后缀语义 |
| 无版本号的许可证名 | trove 只写 `GNU General Public License`（不带版本），臆断为 3.0 会判错版本 | 无版本信息一律归入 `GPL-unknown` / `LGPL-unknown`，标为待人工确认，不假装确定 |
| 双许可写法 | npm / PyPI 常见 `MIT OR Apache-2.0`、`GPL-2.0-only OR MIT` | 保留 `OR` 语义：任选其一时按最宽松项判定，不再误报为强传染 |
| 写法五花八门 | `PyQt5` 写 `"GPL v3"`、`chardet` 写 `"0BSD"`、`protobuf` 写 `"3-Clause BSD License"`（词序颠倒）、`rouge` 只写 `"LICENCE.txt"` | 逐一补充模式；对真的没有信息的（如 `LICENCE.txt`）明确标为"待人工确认"，不假装确定 |
| 包名 ≠ import 名 | `scikit-learn` 实际 import `sklearn`，`PyYAML` 实际 import `yaml`，`pymupdf` 新旧版分别是 `pymupdf` / `fitz` | 维护别名表 + 启发式推断，避免把"已经用了"误判成"没用到" |

## 实测数据

对 **26 个真实开源项目**（累计 **1143 个依赖**）做了批量扫描，覆盖 PyPI 与 npm 两种生态：

| 项目 | 自身许可 | 生态 | 依赖数 | 识别率 | 高可信 | 检出的传染性依赖 |
|---|---|---|---|---|---|---|
| infiniflow/ragflow | Apache-2.0 | PyPI | 70 | 94.3% | 78.6% | `demjson3`(LGPL-3.0-only), `es-core-news-sm`(GPL-3.0-only), `extract-msg`(GPL-unknown) 等 5 个 |
| NVIDIA/NeMo | Apache-2.0 | PyPI | 70 | 95.7% | 90.0% | — |
| EleutherAI/lm-evaluation-harness | MIT | PyPI | 70 | 98.6% | 82.9% | `fuzzywuzzy`(GPL-2.0-only), `kstar-planner`(GPL-3.0-only), `pycountry`(LGPL-2.1-only) 等 4 个 |
| apache/airflow | Apache-2.0 | PyPI | 70 | 91.4% | 91.4% | — |
| lobehub/lobe-chat | Apache-2.0 | npm | 70 | 100.0% | 100.0% | — |
| vercel/next.js | MIT | npm | 70 | 100.0% | 100.0% | `@vercel/og`(MPL-2.0) |
| facebook/react | MIT | npm | 70 | 95.7% | 95.7% | — |
| explodinggradients/ragas | Apache-2.0 | PyPI | 68 | 100.0% | 79.4% | `tqdm`(MPL-2.0 AND MIT) |
| vllm-project/vllm | Apache-2.0 | PyPI | 58 | 98.3% | 77.6% | `tqdm`(MPL-2.0 AND MIT) |
| fastapi/fastapi | MIT | PyPI | 55 | 100.0% | 90.9% | `CairoSVG`(LGPL-3.0-or-later), `PyGithub`(LGPL-unknown) |
| matplotlib/matplotlib | PSF-based | PyPI | 52 | 100.0% | 82.7% | `certifi`(MPL-2.0), `pikepdf`(MPL-2.0), `pytest-rerunfailures`(MPL-2.0) |
| vuejs/core | MIT | npm | 52 | 100.0% | 100.0% | `rollup-plugin-dts`(LGPL-3.0-only) |
| open-compass/opencompass | Apache-2.0 | PyPI | 47 | 97.9% | 85.1% | `func-timeout`(LGPL-2.0-only), `fuzzywuzzy`(GPL-2.0-only), `python-Levenshtein`(GPL-2.0-or-later) 等 4 个 |
| expressjs/express | MIT | npm | 44 | 100.0% | 100.0% | — |
| axios/axios | MIT | npm | 43 | 100.0% | 100.0% | — |
| pandas-dev/pandas | BSD-3-Clause | PyPI | 39 | 100.0% | 84.6% | `PyQt5`(GPL-3.0-only), `psycopg2`(LGPL-unknown), `pyxlsb`(LGPL-3.0-or-later) |
| scrapy/scrapy | BSD-3-Clause | PyPI | 34 | 100.0% | 76.5% | — |
| pallets/flask | BSD-3-Clause | PyPI | 25 | 100.0% | 88.0% | — |
| run-llama/llama_index | MIT | PyPI | 23 | 100.0% | 95.7% | `codespell`(GPL-2.0-only), `pylint`(GPL-2.0-or-later) |
| FlowiseAI/Flowise | Apache-2.0 | npm | 23 | 100.0% | 100.0% | — |
| n8n-io/n8n | Sustainable Use License | npm | 21 | 100.0% | 100.0% | — |
| deepset-ai/haystack | Apache-2.0 | PyPI | 18 | 100.0% | 88.9% | `tqdm`(MPL-2.0 AND MIT) |
| psf/requests | Apache-2.0 | PyPI | 18 | 100.0% | 83.3% | `certifi`(MPL-2.0) |
| huggingface/peft | Apache-2.0 | PyPI | 15 | 100.0% | 80.0% | `tqdm`(MPL-2.0 AND MIT) |
| microsoft/DeepSpeed | Apache-2.0 | PyPI | 11 | 100.0% | 81.8% | `tqdm`(MPL-2.0 AND MIT) |
| modelscope/modelscope | Apache-2.0 | PyPI | 7 | 100.0% | 85.7% | `tqdm`(MPL-2.0 AND MIT) |

**整体识别率 98.3%，高可信度判定占比 89.5%，检出风险项 30 条（高危 10 条），26 个项目里 16 个含传染性依赖。**

#### 识别率这个数字怎么读

98.3% 是一个**混合口径**——它把"没认出来"的原因混在一起统计了，而那些原因性质完全不同：

| 归因 | 含义 | 算不算工具的问题 |
|---|---|---|
| 源站未填许可证 | PyPI / npm 上这个包压根没有许可证字段（如 `rouge`，它的 `license` 字段是字面量 `LICENCE.txt`） | 不算，源站就没有 |
| 源站无此包 | 私有包、已下架、或需手动下载的模型包（如 `apache-airflow-ctl-tests`、spaCy 的 `en-core-web-sm`） | 不算，判定正确 |
| 知识库未收录 | 源站写了非标准写法（如 `nvidia-sphinx-theme` 的 `NVIDIA LICENSE AGREEMENT`），工具的知识库不认 | **算，补进 `LICENSE_DB` 就能降** |
| 网络获取失败 | 并发抓取超时（弱网环境常见） | 不算，重跑即可 |

所以报告里同时给出两个数字：**原始识别率**（98.3%）与
**剔除源站客观无数据后的识别率**（`effective_resolve_rate`）。
只有「知识库未收录」一类归因于工具自身——这也是唯一值得投入改进的方向。
详见 [`CHANGELOG.md`](CHANGELOG.md) 的 0.4.0 条目。

### 样本怎么选的

不是随便挑的，每个项目加入前都实测过能否解析出依赖清单——拿不到清单的项目
只会在汇总里多一行"跳过"，反而削弱数据说服力。26 个项目里：

- **20 个 PyPI、6 个 npm**，用于验证跨生态判定能力
- 覆盖 AI/大模型（ragflow、NeMo、vllm、opencompass、ragas…）、通用 Python
  （pandas、flask、fastapi、requests、airflow…）、前端（next.js、react、vue、axios…）

### 一个真实缺陷：monorepo 内部包

扫描 lobe-chat 时发现识别率只有 58.6%、还多出 29 条风险项。查明原因：
它的 package.json 里有 89 个依赖版本写作 `workspace:*`——**monorepo 内部包，
项目自身代码，根本不发布到 npm**。

它们被当成第三方依赖去查，查不到就记"未识别"、还判成风险项。归因是错的：
不是工具认不出许可证，而是它压根不是第三方依赖。
现已按 `workspace:` 标记识别并排除，lobe-chat 识别率回到 100%、误报归零。

**判定只依据 `workspace:` 标记，不靠包名猜测**——`@scope/xxx` 里既有内部包
也有真实发布的第三方包（`@vercel/og`、`@anthropic-ai/sdk`），按名字猜必然出错。

排除逻辑在**三个入口都生效**：批量扫描（`scan_projects.py`）、命令行
（`license_audit.py --package-json`）与 Web 界面。三者共用同一个
`split_workspace_deps()`，且都会显式打印/返回排除数量，不会静默少算依赖
（v0.4.2 起；此前只有批量扫描做了排除，命令行与 Web 会把内部包当成第三方依赖）。

26 个项目累计排除 **108 个**工作区内部包（lobe-chat 91、next.js 15、n8n 2），
其余项目不含 `workspace:` 依赖。该数字在 `scan_summary.json` 的
`workspace_excluded` 字段里逐项目可查。

### 复现

```bash
python scan_projects.py --jobs 16     # 完整复现，约 10—20 分钟
python scan_projects.py --offline     # 复用快照重算，约 2 分钟，不联网
```

- 只依赖 Python 标准库直连 GitHub REST API，**不需要任何本地连接器脚本或第三方库**
- 可选：设置 `GITHUB_TOKEN` 环境变量提升配额（匿名接口 60 次/小时，26 个项目约需 300 次请求，会限流）
- 结果写入 `scan_summary.md` / `scan_summary.json`，逐项目报告在 `scan/` 下

**结论可独立验证**——不轻信脚本自己的输出，而是重新抓源站元数据对照：

```bash
python verify_sample.py               # 分层随机抽样 40 条，回查 PyPI / npm 原始值
python verify_sample.py --n 100 --seed 42
```

抽查结果见 [`verify_sample.md`](verify_sample.md)：40 条样本中 6 条经查源站确实无此包
（工具标"未识别"正确），其余 34 条判定与源站元数据**逐条一致**。

## 架构

```
┌─ 依赖清单解析 ──────────────────────────────────┐
│  requirements.txt / pyproject.toml / package.json
└────────────────────┬───────────────────────────┘
                     ▼
┌─ 元数据采集 ────────────────────────────────────┐
│  PyPI JSON API / npm registry，带重试与缓存
└────────────────────┬───────────────────────────┘
                     ▼
┌─ 许可证归一化 ──────────────────────────────────┐
│  四级可信度解析链 → SPDX 标识 → 传染强度分级
└────────────────────┬───────────────────────────┘
                     ▼
┌─ 兼容性判定 ────────────────────────────────────┐
│  项目许可证 × 依赖类别矩阵 + 传递依赖抽查
└────────────────────┬───────────────────────────┘
                     ▼
┌─ 语义字段判定（三层闭环）────────────────────────┐
│  ① 证据采集（确定性）：import / vendored / patch / 仅测试
│  ② 语义判定（LLM）：使用方式、自主开发边界、义务适用性
│  ③ 规则校验（确定性）：拦住模型幻觉，不通过则回退
└────────────────────┬───────────────────────────┘
                     ▼
        《开源及第三方资源使用清单》
```

**为什么 AI 在这里是必要的**：清单里"使用方式""自主开发边界"这类字段，取决于该依赖在项目中**实际怎么被用**——是只 import 了一下，还是 fork 了源码做二次开发。这个判断需要语言理解。而"能不能这么说"则由确定性规则把关：证据里有 vendored 副本，就不允许声称"未修改源码"。**AI 负责判断，规则负责否决。**

## 快速开始

无需安装任何第三方依赖，Python 3.8+ 即可。

### 命令行

```bash
# 1. 扫描依赖清单，生成许可证合规报告
python license_audit.py --requirements requirements.txt --project-license MIT

# 也支持 pyproject.toml（PEP 621 / PEP 735 / Poetry）与 package.json
python license_audit.py --pyproject pyproject.toml --project-license Apache-2.0

# 元数据抓取并发（零第三方依赖，纯标准库线程池）——依赖多时提速约 8—10 倍
python license_audit.py --requirements requirements.txt --project-license MIT --jobs 16

# 2. 补全语义字段（使用方式、自主开发边界等），并直接产出竞赛要求的清单
python semantic_audit.py --project-dir . --audit-json license_audit_report.json
```

输出：

```
[1/4] 解析依赖清单：47 个直接依赖（来源 PyPI）
     其中 3 个依赖为精确锁定版本，将按锁定版本查询许可证元数据
     元数据抓取并发数：16
[2/4] 获取许可证元数据：成功 47 / 共 47
[3/4] 许可证类别分布：宽松许可 42，弱传染 2，强传染 2，未识别 1
[4/4] 检出风险项：3 条（高 2 / 中 1）
```

`--jobs` 只影响网络等待，判定逻辑仍是确定性的串行流程，结果与串行完全一致。

两点与清单、输出有关的行为：

- **`-r` 指针清单会自动跟随**：清单内容只有一行 `-r requirements/runtime.txt` 时，
  会递归跟进引用链（带环路与深度保护），只接受清单目录内的相对路径；
  跟随了哪些文件、哪些引用被跳过都会打印出来——不做静默少解析。
- **一次产出两份报告**：`--out` 指定的路径写 Markdown，同名 `.json` 写结构化数据。
  若 `--out` 不以 `.md` 结尾（如 `out.txt`），JSON 会另存为 `out.txt.json`，
  **两份报告不会互相覆盖**。

### Web 界面

```bash
python web/server.py            # 打开 http://127.0.0.1:8770
```

两种用法：

- **粘贴依赖清单** —— 直接查许可证，无需上传源码
- **上传项目 zip** —— 解压后扫描源码，额外判定「使用方式」「自主开发边界」「许可义务是否触发」

上传有三道限额：请求体 20MB；解压后总大小 200MB；解压条目数 2000（防止 zip 炸弹），
超限或清单类型非法会返回 400 并说明原因。

结果页支持一键下载《开源及第三方资源使用清单》（Markdown / CSV）。

### 使用本地模型做语义判定

默认使用确定性规则引擎。要启用模型判定：

```bash
# 本地 Ollama（推荐，无需 API Key）
python semantic_audit.py --project-dir . --audit-json report.json \
    --backend ollama --model qwen2.5-coder:7b

# 任意 OpenAI 兼容接口
export LLM_BASE_URL=https://your-endpoint/v1
export LLM_API_KEY=sk-xxx
python semantic_audit.py --project-dir . --audit-json report.json --backend openai
```

模型不可用或输出不合规时会自动回退到规则结论，并在报告中标注，不会中断流程。

## 输出示例

```markdown
| 资源名称 | 版本 | 许可证 | 使用方式 | 自主开发边界 | 许可义务是否触发 | 判定依据 |
|---|---|---|---|---|---|---|
| requests | 2.34.2 | Apache-2.0 | 作为库调用（未修改源码） | 未修改，仅调用公开 API | 否 | 源码中检出 import 1 次（app.py） |
| pymupdf | 1.28.2 | AGPL-3.0-only | 作为库调用（未修改源码） | 未修改，仅调用公开 API | 是 | 源码中检出 import 1 次（app.py） |
| fuzzywuzzy | 3.0.1 | GPL-3.0-only | 修改源码 / 二次开发 | 已二次开发 | 是 | 存在 vendored 副本：vendor/fuzzywuzzy |
| scikit-learn | 1.9.1 | BSD-3-Clause | 仅测试环节使用（不随产品分发） | 未修改，仅调用公开 API | 否 | 仅出现在 tests/test_app.py |
```

## 测试

```bash
python test_cases.py      # 251 个用例：许可证归一化 + 兼容性判定 + 矩阵覆盖 + 版本约束 + 清单表
                          #            + workspace 识别(J) + 知识库扩容(K) + 未识别归因(L)
                          #            + 跨 Python 版本一致性(M) + 审查报告修复(N) + 检查清单修复(O)
python test_semantic.py   #  95 个用例：证据采集 + 测试文件判定 + 防幻觉校验 + 回退行为
```

共 **346 个用例，全部可离线运行**。**每个用例都对应开发过程中实测发现的真实误判，不是编造的假数据**——
包括 pandas 的 61KB 许可证正文、torch 的 `WITH` 例外吞掉 `AND`、fuzzywuzzy 被误判为 GPL-3.0、
`contest/` 被当成测试目录、`BSL-1.1` 被 Boost 规则抢先匹配成宽松许可等。

## 许可证知识库

当前收录 **53 种**许可证标识（v0.4 起），覆盖五类传染强度：

| 类别 | 说明 | 举例 |
|---|---|---|
| 宽松许可 | 保留声明即可 | MIT、Apache-2.0、BSD-3-Clause、ZPL-2.1、MulanPSL-2.0 |
| 弱传染 | 文件级 / 模块级隔离 | MPL-2.0、EPL-2.0、LGPL-3.0-only |
| 强传染 | 衍生作品需整体开放 | GPL-3.0-only、EUPL-1.2、CC-BY-SA-4.0 |
| 网络传染 | 对外提供服务即触发 | AGPL-3.0-only、SSPL-1.0 |
| 源码可得（非 OSI） | 能拿到源码，但限制商业使用 | Elastic-2.0、BSL-1.1 |

**非 OSI 许可会被显式点出商业风险**，而不是笼统标成"待确认"。例如 `Elastic-2.0`：
工具会告诉你「禁止将本软件作为托管服务对外提供」——这才是使用者真正需要知道的事。

发现漏判欢迎提交 [知识库缺项 Issue](https://github.com/ank678code/open-source-license-audit/issues/new?template=license-db.yml)，
这是本项目最容易上手也最有价值的贡献。

## 已知限制

- **语义字段依赖源码可读**：项目源码不在本地时无法采集证据，相关字段会标为"待确认"
- **包名 → import 名映射是启发式的**：别名表覆盖常见包，冷门包可能漏检；欢迎补充 `IMPORT_ALIASES`
- **必须按生态查询**：npm 依赖不能按 PyPI 查，否则会命中同名的无关包（实测 `husky` 曾因此被误判为 LGPL）
- **只覆盖 Python / npm 生态**：Maven、Go modules 尚未支持
- **不替代法律意见**：许可证兼容性判断基于常见实践，涉及商业分发请咨询专业人士
- 元数据来自 PyPI / npm registry，会随包版本更新变化；依赖清单中**精确锁定的版本**（`==1.2.3` / `1.2.3`）会按锁定版本查询，范围约束（`>=`、`^`、`~` 等）仍查最新版，建议定期重跑

## 贡献

完整指南见 [CONTRIBUTING.md](CONTRIBUTING.md)。几条底线：

- **零第三方依赖**，只用 Python 标准库，Python 3.8+ 必须能跑
- **每个改动都要有真实依据**——请说明你在哪个包、哪个源站字段上遇到了什么值
- **判定逻辑的改动必须带测试用例**，不接受无用例的改动
- **不臆断**：拿不到证据时输出「待确认」，而不是编一个看起来合理的结论

最欢迎的四类贡献：

- 补充 [许可证写法 / 知识库缺项](https://github.com/ank678code/open-source-license-audit/issues/new?template=license-db.yml)
- 报告 [判定错误](https://github.com/ank678code/open-source-license-audit/issues/new?template=bug_report.yml)
- 补充 `IMPORT_ALIASES` 里的包名映射
- [功能建议](https://github.com/ank678code/open-source-license-audit/issues/new?template=feature_request.yml)

参与即视为同意[行为准则](.github/CODE_OF_CONDUCT.md)。

## 许可证

[MIT](LICENSE)
