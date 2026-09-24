# 安全政策

## 支持版本

本项目为单文件形态的命令行工具，仅维护 `main` 分支的最新版本。

## 报告漏洞

**请不要在公开 Issue 中报告安全漏洞。**

请通过 GitHub 的私密渠道提交：
https://github.com/ank678code/open-source-license-audit/security/advisories/new

我们会：

- 在 **3 个工作日内**确认收到
- 在 **7 个工作日内**给出初步评估与修复计划
- 修复合并后在 Release Notes 与 CHANGELOG 中公开致谢（除非你要求匿名）

## 本项目的攻击面

工具会处理不信任的输入，以下几处是我们重点关注的：

- **依赖清单解析**：`requirements.txt` / `pyproject.toml` / `package.json`。
  畸形或超大输入不应导致崩溃或资源耗尽。
- **上游元数据**：PyPI / npm registry 返回的 JSON 由第三方控制，
  其中的 `license` 字段可能长达数万字符，已被截断处理。
- **路径处理**：`semantic_audit.py` 会遍历用户指定的项目目录；
  Web 界面（`web/server.py`）接受用户上传的 zip 包。

## 已知的非安全问题

以下行为是**设计使然**，不是漏洞：

- 工具会向 PyPI / npm registry 发起网络请求（这是它的工作方式）。
  离线环境请用 `--offline` 或复用快照。
- Web 界面默认只监听 `127.0.0.1:8770`，不建议直接暴露到公网。
- 语义判定启用 `--backend openai` 时会把**依赖名、许可证与源码证据片段**
  发送给模型服务。涉及闭源项目时请使用 `--backend ollama` 本地模型。

## 关于结论的可信度

工具的许可证判定基于上游元数据，**不构成法律意见**。
如果上游包作者填写了错误的许可证字段，工具会忠实地输出错误结论——
这是数据问题，不是安全问题。遇到这种情况请提交 Issue 补充正确的判定规则。
