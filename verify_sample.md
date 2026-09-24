# 扫描结果独立抽查报告

对 `scan_summary.json` 的判定结果做**独立回查**：不依赖工具自身的结论，直接重新抓取
PyPI / npm 的原始元数据，逐条人工比对。

- 抽查时间：2026-09-24
- 抽样方式：分层随机（固定随机种子 `20260924`，可复现）
- 样本构成：已识别项 24 条 + 传染性许可项 10 条 + 未识别项 6 条 = **40 条**

## 结论

| 指标 | 数量 |
|---|---|
| 抽样总数 | 40 |
| 其中：源站确实无此包（工具标"未识别"正确） | 6 |
| 有效可比对样本 | 34 |
| 自动匹配一致 | 30 |
| 人工复核后确认为一致 | 4 |
| **最终准确** | **34 / 34 = 100%** |

## 4 条需要人工裁决的条目（均已确认为工具正确）

自动比对脚本机械匹配会漏判，逐条查证原始值后确认工具判定全部正确：

| 包名 | 源站原始值 | 工具判定 | 裁决 |
|---|---|---|---|
| `unitxt` | `Apache License\n Version 2.0, January 2004 …` | `Apache-2.0` | ✅ 正确。多行许可证正文，工具按首行归一化，未受正文干扰 |
| `fuzzywuzzy` | trove: `GNU General Public License v2 (GPLv2)` | `GPL-2.0-only` | ✅ 正确。`v2` 无 `or-later` 后缀，判为 `-only` 语义准确 |
| `es-core-news-sm` | `GNU GPL 3.0` | `GPL-3.0-only` | ✅ 正确。自然语言写法成功归一化 |
| `fr-core-news-sm` | `LGPL-LR` | `LGPL-unknown` | ✅ 正确且克制。`LGPL-LR` 是含义不明的非标准写法，工具**不臆断版本**、标为待人工确认 |

> 最后一条尤其值得注意：这正是 README「已知限制」里承诺的行为——**不确定就标待确认，不假装确定**。

## 6 条"源站无此包"说明了什么

`apache-airflow-kubernetes-tests`、`apache-airflow-docker-tests`、`apache-airflow-ctl-tests`、
`apache-airflow-providers-duckdb`、`en-core-web-sm`、`de-core-news-sm` 在 PyPI 上确实不存在
（多为项目内部的测试依赖组或需手动下载的模型包）。工具将其标为 `UNKNOWN / 未识别` 而**不猜、
不误报为风险项**，行为正确。

## 复现方式

```bash
# 1. 重新扫描（产物 scan/ 与 scan_summary.json）
python scan_projects.py --jobs 16

# 2. 独立回查：直接抓源站元数据比对
python verify_sample.py
```

`verify_sample.py` 仅依赖 Python 标准库，固定随机种子，结果可复现。
