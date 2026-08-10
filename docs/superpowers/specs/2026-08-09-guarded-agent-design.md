# Guarded Agent Design

本设计的规范性内容以仓库根目录的 [`SPEC.md`](../../../SPEC.md) 为准。本文件记录 Superpowers brainstorming 的设计结论，供后续实现计划引用。

## Design summary

Guarded Agent 是治理优先的本地 Coding Agent Harness。它使用自研主循环连接可替换 LLM、结构化工具、确定性治理、反馈验证、项目记忆和停机条件。产品提供 CLI 与仅监听本机的 WebUI，以 Linux x86_64 PyInstaller 单文件二进制分发。

## Architectural decision

采用模块化单体而非双进程 Worker 或完整事件溯源。CLI 和 WebUI 通过同一个应用服务调用 Harness 内核；SQLite 保存任务当前状态、记忆和追加式审计事件。该方案保留清晰的可测试组件边界，同时把进程管理和基础设施工作控制在课程项目范围内。

## Main contribution

治理模块是主要贡献。每个动作都经过严格模式、路径真实化、工作区与敏感路径围栏、命令风险分类、资源限制及审批策略。审批绑定规范化动作摘要、任务、工作区和策略版本，只能消费一次。机制不依赖真实 LLM，可由 Mock LLM 和直接单元测试确定性验证。

## Feedback and completion

代码修改后自动运行配置的测试、lint 或类型检查；结果被分类、截断和脱敏后进入下一轮。模型声明完成不会直接终止任务，Harness 会再次运行验收命令，只有客观通过才进入 `COMPLETED`。

## Security boundary

该系统是应用级护栏，不是强沙箱。命令采用参数数组且不启用 shell；路径限制在规范化工作区；API Key 使用主密码派生密钥进行认证加密；WebUI 不接触主密码且只监听 `127.0.0.1`。用户只能对可信仓库运行项目脚本。

## Scope decisions

- 同一时间运行一个任务。
- 不做多人、插件、远程 Worker、Docker 或公网部署。
- 分发产物仅为 Linux x86_64 单文件二进制。
- Mock 模式开箱可用，真实模型使用 OpenAI-compatible 单次 HTTP API。
- 公网 URL 条款的偏离必须在交付材料中保留，不得隐去。

## Acceptance focus

离线测试必须证明危险动作拦截、审批防篡改和防重放、失败反馈驱动下一轮改变动作、最终验收门禁、工作区围栏、敏感信息不泄漏，以及打包二进制能够运行机制演示。完整要求见 `SPEC.md`。
