# 真实开源项目批量扫描结果

- 工具版本：**v0.4.2**
- 扫描项目数：**26** 个
- 累计依赖数：**1143**
- 许可证识别率：**98.3%**（1124/1143）
- 高可信度判定占比：**89.5%**（1023/1143）
- 检出风险项：**30** 条（其中高危 10 条）
- 含传染性依赖的项目：**16/26**
- 排除的 monorepo 工作区内部包：**108** 个（`workspace:` 标记，不发布到 registry，不参与审计）

### 未识别项归因（v0.4）

- 未识别合计：**19** 条
- 源站无此包：**7** 条（工具无能为力）
- 源站未填许可证：**11** 条（须人工核对上游仓库）
- 知识库未收录该写法：**1** 条（**应由工具改进**，补进 LICENSE_DB 即可降低）
- 网络获取失败：**0** 条（重跑即可）

> 原始识别率 98.3% 把上述四种性质混在一起统计；剔除「源站客观无数据」后为 **99.9%**。两者并列展示，才能让识别率这个数字站得住。

| 项目 | 自身许可 | 依赖数 | 识别率 | 高可信 | 风险(高) | 传染性依赖 |
|---|---|---|---|---|---|---|
| infiniflow/ragflow | Apache-2.0 | 70 | 94.3% | 78.6% | 6(2) | demjson3(LGPL-3.0-only), es-core-news-sm(GPL-3.0-only), extract-msg(GPL-unknown), fr-core-news-sm(LGPL-unknown), hypothesis(MPL-2.0) |
| NVIDIA/NeMo | Apache-2.0 | 70 | 95.7% | 90.0% | 3(0) | — |
| EleutherAI/lm-evaluation-harness | MIT | 70 | 98.6% | 82.9% | 3(2) | fuzzywuzzy(GPL-2.0-only), kstar-planner(GPL-3.0-only), pycountry(LGPL-2.1-only), tqdm(MPL-2.0 AND MIT) |
| explodinggradients/ragas | Apache-2.0 | 68 | 100.0% | 79.4% | 1(1) | tqdm(MPL-2.0 AND MIT) |
| vllm-project/vllm | Apache-2.0 | 58 | 98.3% | 77.6% | 1(0) | tqdm(MPL-2.0 AND MIT) |
| open-compass/opencompass | Apache-2.0 | 47 | 97.9% | 85.1% | 3(2) | func-timeout(LGPL-2.0-only), fuzzywuzzy(GPL-2.0-only), python-Levenshtein(GPL-2.0-or-later), tqdm(MPL-2.0 AND MIT) |
| run-llama/llama_index | MIT | 23 | 100.0% | 95.7% | 2(2) | codespell(GPL-2.0-only), pylint(GPL-2.0-or-later) |
| deepset-ai/haystack | Apache-2.0 | 18 | 100.0% | 88.9% | 0(0) | tqdm(MPL-2.0 AND MIT) |
| modelscope/modelscope | Apache-2.0 | 7 | 100.0% | 85.7% | 0(0) | tqdm(MPL-2.0 AND MIT) |
| huggingface/peft | Apache-2.0 | 15 | 100.0% | 80.0% | 0(0) | tqdm(MPL-2.0 AND MIT) |
| microsoft/DeepSpeed | Apache-2.0 | 11 | 100.0% | 81.8% | 0(0) | tqdm(MPL-2.0 AND MIT) |
| apache/airflow | Apache-2.0 | 70 | 91.4% | 91.4% | 7(0) | — |
| matplotlib/matplotlib | PSF-based | 52 | 100.0% | 82.7% | 0(0) | certifi(MPL-2.0), pikepdf(MPL-2.0), pytest-rerunfailures(MPL-2.0) |
| fastapi/fastapi | MIT | 55 | 100.0% | 90.9% | 0(0) | CairoSVG(LGPL-3.0-or-later), PyGithub(LGPL-unknown) |
| pandas-dev/pandas | BSD-3-Clause | 39 | 100.0% | 84.6% | 1(1) | PyQt5(GPL-3.0-only), psycopg2(LGPL-unknown), pyxlsb(LGPL-3.0-or-later) |
| scrapy/scrapy | BSD-3-Clause | 34 | 100.0% | 76.5% | 0(0) | — |
| pallets/flask | BSD-3-Clause | 25 | 100.0% | 88.0% | 0(0) | — |
| psf/requests | Apache-2.0 | 18 | 100.0% | 83.3% | 0(0) | certifi(MPL-2.0) |
| lobehub/lobe-chat | Apache-2.0 | 70 | 100.0% | 100.0% | 0(0) | — |
| vercel/next.js | MIT | 70 | 100.0% | 100.0% | 0(0) | @vercel/og(MPL-2.0) |
| facebook/react | MIT | 70 | 95.7% | 95.7% | 3(0) | — |
| vuejs/core | MIT | 52 | 100.0% | 100.0% | 0(0) | rollup-plugin-dts(LGPL-3.0-only) |
| expressjs/express | MIT | 44 | 100.0% | 100.0% | 0(0) | — |
| axios/axios | MIT | 43 | 100.0% | 100.0% | 0(0) | — |
| n8n-io/n8n | Sustainable Use License | 21 | 100.0% | 100.0% | 0(0) | — |
| FlowiseAI/Flowise | Apache-2.0 | 23 | 100.0% | 100.0% | 0(0) | — |