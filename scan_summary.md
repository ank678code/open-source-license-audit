# 真实开源项目批量扫描结果

- 工具版本：**v0.3**
- 扫描项目数：**8** 个
- 累计依赖数：**316**
- 许可证识别率：**97.5%**（308/316）
- 高可信度判定占比：**83.5%**（264/316）
- 检出风险项：**16** 条（其中高危 8 条）
- 含传染性依赖的项目：**7/8**

| 项目 | 自身许可 | 依赖数 | 识别率 | 高可信 | 风险(高) | 传染性依赖 |
|---|---|---|---|---|---|---|
| open-compass/opencompass | Apache-2.0 | 47 | 97.9% | 85.1% | 3(2) | func-timeout(LGPL-2.0-only), fuzzywuzzy(GPL-2.0-only), python-Levenshtein(GPL-2.0-or-later), tqdm(MPL-2.0 AND MIT) |
| EleutherAI/lm-evaluation-harness | MIT | 70 | 98.6% | 82.9% | 3(2) | fuzzywuzzy(GPL-2.0-only), kstar-planner(GPL-3.0-only), pycountry(LGPL-2.1-only), tqdm(MPL-2.0 AND MIT) |
| vllm-project/vllm | Apache-2.0 | 58 | 98.3% | 77.6% | 1(0) | tqdm(MPL-2.0 AND MIT) |
| run-llama/llama_index | MIT | 23 | 100.0% | 95.7% | 2(2) | codespell(GPL-2.0-only), pylint(GPL-2.0-or-later) |
| chatchat-space/Langchain-Chatchat | Apache-2.0 | — | — | — | — | 跳过(no_snapshot) |
| modelscope/modelscope | Apache-2.0 | 7 | 100.0% | 85.7% | 0(0) | tqdm(MPL-2.0 AND MIT) |
| QwenLM/Qwen-Agent | Apache-2.0 | — | — | — | — | 跳过(no_snapshot) |
| deepset-ai/haystack | Apache-2.0 | 18 | 100.0% | 88.9% | 0(0) | tqdm(MPL-2.0 AND MIT) |
| infiniflow/ragflow | Apache-2.0 | 70 | 92.9% | 77.1% | 7(2) | demjson3(LGPL-3.0-only), es-core-news-sm(GPL-3.0-only), extract-msg(GPL-unknown), fr-core-news-sm(LGPL-unknown), hypothesis(MPL-2.0) |
| FlowiseAI/Flowise | Apache-2.0 | 23 | 100.0% | 100.0% | 0(0) | — |