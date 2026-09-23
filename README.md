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

对 8 个真实开源项目（累计 316 个依赖）做了批量扫描：

| 项目 | 自身许可 | 依赖数 | 识别率 | 高可信 | 检出的传染性依赖 |
|---|---|---|---|---|---|
| open-compass/opencompass | Apache-2.0 | 47 | 97.9% | 85.1% | `fuzzywuzzy`(GPL-3.0)、`python-Levenshtein`(GPL-2.0-or-later)、`func-timeout`(LGPL-3.0) |
| EleutherAI/lm-evaluation-harness | MIT | 70 | 95.7% | 81.4% | `fuzzywuzzy`(GPL-3.0)、`kstar-planner`(GPL-3.0)、`pycountry`(LGPL-2.1) |
| infiniflow/ragflow | Apache-2.0 | 70 | 92.9% | 78.6% | `demjson3`(LGPL-3.0)、`extract-msg`(GPL)、`es-core-news-sm`(GPL) |
| vllm-project/vllm | Apache-2.0 | 58 | 96.6% | 75.9% | `tqdm`(MPL-2.0 AND MIT) |
| run-llama/llama_index | MIT | 23 | 100% | 95.7% | `codespell`(GPL-2.0)、`pylint`(GPL-2.0-or-later) |
| FlowiseAI/Flowise | Apache-2.0 | 23 | 100% | 100% | — |
| deepset-ai/haystack | Apache-2.0 | 18 | 100% | 88.9% | `tqdm`(MPL-2.0 AND MIT) |
| modelscope/modelscope | Apache-2.0 | 7 | 100% | 85.7% | `tqdm`(MPL-2.0 AND MIT) |

**整体识别率 97.5%，高可信度判定占比 83.5%，检出风险项 16 条（高危 8 条），8 个项目里 7 个含传染性依赖。**

复现：`python scan_projects.py --jobs 16`

- 只依赖 Python 标准库直连 GitHub REST API，**不需要任何本地连接器脚本或第三方库**
- 可选：设置 `GITHUB_TOKEN` 环境变量可提升配额上限（匿名接口 60 次/小时也够一次完整扫描）
- 单次完整扫描约 2—4 分钟（并发抓取；串行约需 20—30 分钟）
- 结果写入 `scan_summary.md` / `scan_summary.json`，逐项目报告在 `scan/` 下

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

### Web 界面

```bash
python web/server.py            # 打开 http://127.0.0.1:8770
```

两种用法：

- **粘贴依赖清单** —— 直接查许可证，无需上传源码
- **上传项目 zip** —— 解压后扫描源码，额外判定「使用方式」「自主开发边界」「许可义务是否触发」

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
python test_cases.py      # 138 个用例：许可证归一化 + 兼容性判定 + 矩阵覆盖 + 版本约束 + 清单表
python test_semantic.py   #  87 个用例：证据采集 + 测试文件判定 + 防幻觉校验 + 回退行为
```

共 **225 个用例，全部可离线运行**。**每个用例都对应开发过程中实测发现的真实误判，不是编造的假数据**——
包括 pandas 的 61KB 许可证正文、torch 的 `WITH` 例外吞掉 `AND`、fuzzywuzzy 被误判为 GPL-3.0、
`contest/` 被当成测试目录等。

## 已知限制

- **语义字段依赖源码可读**：项目源码不在本地时无法采集证据，相关字段会标为"待确认"
- **包名 → import 名映射是启发式的**：别名表覆盖常见包，冷门包可能漏检；欢迎补充 `IMPORT_ALIASES`
- **必须按生态查询**：npm 依赖不能按 PyPI 查，否则会命中同名的无关包（实测 `husky` 曾因此被误判为 LGPL）
- **只覆盖 Python / npm 生态**：Maven、Go modules 尚未支持
- **不替代法律意见**：许可证兼容性判断基于常见实践，涉及商业分发请咨询专业人士
- 元数据来自 PyPI / npm registry，会随包版本更新变化；依赖清单中**精确锁定的版本**（`==1.2.3` / `1.2.3`）会按锁定版本查询，范围约束（`>=`、`^`、`~` 等）仍查最新版，建议定期重跑

## 贡献

欢迎提交 Issue 和 PR，特别是：
- 补充 `IMPORT_ALIASES` 里的包名映射
- 补充新的许可证写法模式（附上你遇到的实际值）
- 新增测试用例

## 许可证

[MIT](LICENSE)
