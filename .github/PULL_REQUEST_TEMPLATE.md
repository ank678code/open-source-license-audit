## 这个 PR 改了什么

<!-- 一句话说清改动。 -->

## 为什么改

<!--
最关键的一节。请给出真实依据：你在哪个包、哪个源站字段上遇到了什么问题。
「我觉得」不构成理由；「zope.interface 的 license_expression 是 ZPL-2.1，工具判成 UNKNOWN」才是。
-->

## 改动类型

- [ ] 修复误判（fix）
- [ ] 补充许可证写法 / 知识库（license-db）
- [ ] 新增功能（feat）
- [ ] 重构 / 清理（refactor / chore）
- [ ] 文档（docs）

## 影响范围

<!-- 这个改动会让已有的扫描结论发生变化吗？使用者手上的旧报告会不会失效？ -->

- 是否改变既有判定结论：是 / 否
- 若「是」，哪些许可证或哪些包的结论会变：

## 自检清单

- [ ] `python test_cases.py` 全绿
- [ ] `python test_semantic.py` 全绿
- [ ] `python -m pyflakes *.py` 无告警
- [ ] 新增了对应的测试用例（本项目每条用例都对应一次真实误判，不接受无用例的判定逻辑改动）
- [ ] 若新增了许可证：已同时补齐 `SPDX_PATTERNS`、`LICENSE_DB`、`COMPAT_MATRIX`，
      非 OSI 许可还补了 `NON_OSI_NOTES`
- [ ] 未引入任何第三方依赖
- [ ] Python 3.8 语法兼容（未使用 3.9+ 语法）

## 相关 Issue

<!-- Closes #xxx -->
