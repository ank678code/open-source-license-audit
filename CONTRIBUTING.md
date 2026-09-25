# 贡献指南

感谢你愿意为这个项目花时间。它面向的是高校开源团队和学生开发者，
所以我们对贡献的要求只有一条：**结论要能站得住，不能靠猜。**

## 先读这三件事

1. **不臆断。** 工具拿不到证据时，输出「待确认」，而不是编一个看起来合理的结论。
   这条原则贯穿全部代码，也是评审 PR 时的第一标准。
2. **每个改动都要有真实依据。** 新增判定逻辑时，请说明你在哪个真实包上遇到了什么值。
   「我觉得应该这样」不构成理由；「`zope.interface` 的 `license_expression` 是 `ZPL-2.1`，
   工具判成了 UNKNOWN」才是。
3. **零第三方依赖。** 本项目只用 Python 标准库，Python 3.8+ 必须能跑。
   引入任何外部依赖的 PR 会被直接拒绝。

## 本地跑起来

```bash
git clone https://github.com/ank678code/open-source-license-audit.git
cd open-source-license-audit

python test_cases.py      # 251 个用例：归一化 + 兼容性 + 知识库 + 归因 + workspace + 审查报告/检查清单修复
python test_semantic.py   #  95 个用例：证据采集 + 防幻觉校验 + 回退行为
```

提交前**两个都必须全绿**，且不能有 `pyflakes` 告警：

```bash
python -m pyflakes *.py      # 应无输出
```

改了代码、加了用例或改了扫描数据之后，再跑一次**一致性闸门**：

```bash
python check_docs_consistency.py
```

它会实际跑一遍两个测试文件，核对 `README.md` / `CONTRIBUTING.md` 里声明的
用例数、扫描指标，以及 `VERSION` / `pyproject.toml` / `CHANGELOG` /
`scan_summary.json` 四处版本号是否一致。**CI 里也会跑这一步。**

> 为什么要专门做这道闸门：「文档落后于代码」已连续两轮被外部检查指出
> （交付数据滞后、版本号不一致、文档用例数过时）。数字硬编码在文档里，
> 每加一组用例就会漂移，靠人工每轮核对必然漏，所以交给 CI 拦。

## 四类常见的贡献

### 1. 补充许可证写法（最欢迎，也最容易上手）

发现某个包的许可证被判成「未识别」时，先确认源站到底给了什么值：

```bash
python -c "from license_audit import normalize_license; print(normalize_license('这里填源站原始值'))"
```

如果源站确实给了明确许可证、而工具不认，那就是知识库缺了这种写法。
改动两处：

- `license_audit.py` 的 `SPDX_PATTERNS` —— 加匹配规则（**注意位置**，见下）
- `license_audit.py` 的 `LICENSE_DB` —— 加 `(类别, 关键义务摘要)`
- 若该许可证不是宽松许可，还要在 `COMPAT_MATRIX` 里补一行，否则会静默退化成「一律报冲突」
- 若该许可证非 OSI 认证或带附加限制，在 `NON_OSI_NOTES` 里补一条商业风险提示

⚠️ **加规则前先想清楚顺序。** `SPDX_PATTERNS` 是从上往下第一个命中即返回，
前缀相同的许可证必须精确区分。真实教训：`BSL-1.0` 是 Boost Software License（宽松），
`BSL-1.1` 是 Business Source License（限制商业使用），两者前缀相同、条款相反，
早期写法 `BSL[- ]?1` 把 1.1 判成了宽松许可——这是高危误判。

### 2. 修正误判（请带上复现证据）

提 Issue 或 PR 时请给出：

- 包名、生态（PyPI / npm）
- 源站 `license_expression` / `license` / classifier 的原始值
- 工具当前判成了什么、应该判成什么
- 为什么（引用许可证条款或 SPDX 定义）

然后**把这条真实值写成测试用例**——本项目 346 个用例每一个都对应一次真实误判，
不是编造的假数据。新增用例放在 `test_cases.py` 对应的分组里（K 组是许可证知识库，
L 组是未识别归因）。

### 3. 补充包名 → import 名映射

`semantic_audit.py` 里的 `IMPORT_ALIASES`。`scikit-learn` → `sklearn`、`PyYAML` → `yaml`
这类映射目前是启发式的，冷门包会漏检。欢迎补充，请附上你在哪个包上发现的。

### 4. 功能开发

先开 Issue 讨论再动手。这个项目刻意保持小而确定，
我们更愿意把「识别率」和「结论可信度」做扎实，而不是堆功能。

## 提交规范

Commit message 用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat: 新增 XXX
fix: 修复 XXX
docs: 补充 XXX
test: 新增 XXX 用例
refactor: 重构 XXX
chore: 清理 XXX
```

一行标题控制在 72 字符内，正文另起一行说明**为什么**这么改（不是改了什么，
那个看 diff 就知道）。

## PR 流程

1. 从 `main` 切分支：`fix/xxx` / `feat/xxx` / `docs/xxx`
2. 改动 + 用例，确保两个测试脚本全绿
3. 开 PR，模板会自动带上检查清单
4. CI（3 个 OS × 3 个 Python 版本 = 9 个 job）必须全绿才合并

PR 里请说明：**改了什么判定、影响哪些既有结论**。如果改动会让历史扫描结果变化，
请在描述里明确指出，这关系到使用者对已有报告的信任。

## 一个提醒

许可证兼容性判断基于常见实践，**不构成法律意见**。
涉及商业分发的结论请在 PR 里标注清楚依据，不要给出超出证据范围的确定性表述。

## 行为准则

参与本项目即视为同意[行为准则](.github/CODE_OF_CONDUCT.md)。
