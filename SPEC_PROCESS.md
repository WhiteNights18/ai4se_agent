# SPEC 与 PLAN 协作过程

**日期：** 2026-08-09  
**当前阶段：** Task 0–12、本地最终评审与验证已完成；Task 12 的 PR #4 已合并且 hosted CI 已通过。个人反思已补写，并明确标注 AI 仅用于事实整理与文字辅助。

## 1. 使用的方法

本项目通过 Codex 中的 Superpowers `brainstorming` 技能，从原始课程要求推进到可验证设计。开发前先阅读《AI4SE Final Project A: Coding Agent Harness》和通用要求，检查空仓库状态，再逐项确认产品方向、WebUI、分发、凭据、架构、主循环、治理、记忆、测试和验收。

## 2. Brainstorming 关键节点

### 迭代一：从宽泛 Harness 收敛到治理优先

**智能体问题：** 是否接受“治理优先的本地 Coding Agent Harness”，重点做命令风险分级、工作区围栏、HITL 审批状态机、审计，以及测试/lint 反馈闭环？

**用户决策：** 先要求解释目标和 WebUI 使用方式，随后确认采用该方向。

**设计变化：** 把“Agent 会写代码”的宽泛目标改为“LLM 只提议动作，确定性代码治理副作用并以客观反馈闭环”。WebUI 被定义为任务控制台而非在线 IDE。

### 迭代二：删除公网部署并简化分发

**初始建议：** 本地完整模式使用 Docker，公网演示模式使用 Mock LLM 沙箱。

**用户修正：** 明确不要公网部署，并追问是否可以只选择原生可执行二进制。

**最终决策：** 只交付 Linux x86_64 PyInstaller 单文件二进制和本地 WebUI，不制作 Docker、不使用包管理器、不部署公网。智能体指出这虽满足 §3.2 三选一的分发要求，但仍偏离最终清单中的线上 URL 条款；用户接受继续。

### 迭代三：从系统钥匙串改为自包含加密凭据库

**智能体建议：** 为避免 Linux 桌面 Secret Service 依赖，使用主密码加密的本地凭据库，并通过 OpenAI-compatible HTTP API 接入真实模型。

**用户决策：** 接受。

**设计变化：** 凭据方案明确为 Scrypt 派生密钥、认证加密、终端隐藏输入和启动时解锁；WebUI 不输入主密码。该选择更适合单文件二进制和无桌面环境。

### 迭代四：架构复杂度取舍

智能体比较了模块化单体、Web/Worker 双进程和完整事件溯源三种方案。用户接受推荐的模块化单体，同时保留追加式审计事件，不实现完整事件重放。该选择把主要工程投入保留给治理与反馈机制。

### 迭代五：从单次任务扩展到持续对话

**用户需求：** 询问当前 Agent 是否可以像持续对话式 Agent 一样工作，并要求按既有文档自行完成。

**智能体判断：** 不重写 `AgentLoop`，新增有界的 `ConversationMessage` 存储、每条消息推进一次已有治理循环的 WebUI JSON 接口，以及启动时一次性解锁真实 provider 的生命周期。

**最终决策：** 采用本地 WebUI MVP；不加入公网部署、流式输出、多用户或主密码持久化。该决策写入 `docs/superpowers/specs/2026-08-11-local-conversational-agent.md` 和对应计划，并由 PR #4 交付。

**人工取舍：** 保留默认 Mock WebUI 兼容性；只有显式选择 `--provider openai-compatible` 才把真实 provider 注入 WebUI。这样离线 Mock 机制演示不依赖网络，同时提供真实 DeepSeek 的本地持续对话入口。

## 3. AI 建议的采纳与推翻

### 已采纳

- 治理作为主角维度，因为其代码机制可脱离真实 LLM 测试。
- CLI 与 WebUI 复用同一应用服务和 Harness 内核。
- Mock LLM 默认可用，真实模型仅为可替换 provider。
- 审批绑定规范化动作摘要和策略版本，单次消费。
- SQLite 同时保存当前状态、记忆和审计事件。
- WebUI 使用服务端渲染与轮询，减少前端工具链。

### 被用户推翻或收缩

- 推翻 Docker + 公网演示模式，改为原生单文件二进制。
- 删除公网部署及线上 URL，接受并公开记录对应评分风险。
- 优先最小可交付范围，不实现多任务并发、强沙箱和复杂部署。

## 4. 对 brainstorming 的阶段反思

做得好的部分：它迫使设计把“安全”从提示词要求转为路径围栏、风险分类和审批绑定等可测试代码，并在实现前暴露了分发条款和线上 URL 条款并不相同。

不满意或成本较高的部分：逐节签字使设计阶段较长；对一个课程项目而言，部分架构备选不会真正进入实现。但这些确认也留下了用户主动取舍范围的过程证据。

## 5. 书面规约复核记录

用户在 2026-08-09 明确回复“批准”。复核版本为 commit `5228011`；该版本保留了无公网 URL 的偏离、客观验收门禁和个人反思学术归属说明，未要求进一步修改。

## 6. 冷启动规格审计（Task 0）

### 6.1 隔离边界与真实性说明

- 请求的审计分派：无项目历史的 `gpt-5.6-terra`，只提供 `SPEC.md`、`PLAN.md`，并明确要求“遇到不确定之处即暂停询问，而非凭猜测继续；不要写实现代码”。
- 记录的审计身份：Codex（GPT-5）；审计材料只读上述两份规范，未读取实现、测试、Git 状态、过程文档或既有对话/记忆，且未写实现代码。
- 限制：审计者不能独立验证该分派确为不同 agent 类型。因此本记录是无历史的 best-effort 审计，**不是**“已满足不同 agent 类型”这一条件的证据。
- 输入与确认：`.superpowers/sdd/PLAN/cold-start-audit.md` 记录了暂停问题；用户于 2026-08-09 在 `task-0-resolutions.md` 确认以下所有选择为规范性决定。

### 6.2 问题、分歧与已确认修订

| ID | 审计问题及分歧解释 | 归属 | 已确认选择 | 简明前后变化 |
|---|---|---|---|---|
| T1-1 | `Action` 是开放参数字典、立即判别联合，还是留给 Task 5？ | SPEC / PLAN | Task 1 提供只含已知 `ToolName` 和 `arguments` 的严格外壳；Task 5 完成逐工具判别参数模型；未知工具始终执行前拒绝。 | 从“工具有参数模型”的散文改为 §3.1 的 14 个工具名、严格外壳及 Task 5 边界。 |
| T1-2 | `ToolResult`、`Feedback`、`GovernanceDecision`、`TaskStatus`、`Settings` 是自由 DTO 还是稳定 API？ | SPEC / PLAN | 固定所有跨模块字段、枚举和 JSON 序列化边界，禁止自由扩展字段。 | §7 新增 DTO 契约表；Task 1 明列产物与断言。 |
| T1-3 | `Outcome` 的来源是漏写 enum、嵌套 enum 还是裸字符串？ | PLAN | 使用 `GovernanceOutcome(str, Enum)`，值精确为 `ALLOW`、`REQUIRE_APPROVAL`、`DENY`。 | 从 `Outcome.DENY` 改为 `GovernanceOutcome.DENY`，并规定导入模块。 |
| T1-4 | 未知 TOML 键忽略、仅拒安全键，还是可放宽资源限制？ | SPEC / PLAN | 未知键均错误；最大值为 20 轮、4 连续失败、1800 总秒、120 命令秒、每流 65536 bytes。 | §4.6 增加唯一 TOML schema、范围及 `[governance]` 禁止规则。 |
| T1-5 | 配置加载谁规范化工作区、缺配置是否错误、符号链接根是否允许？ | SPEC / PLAN | `load_settings` strict-resolve 到绝对目录；符号链接根允许但 realpath 是身份；缺配置使用默认；无效配置以 `invalid configuration:` 报错。 | §4.6 与 Task 1 写出顺序、返回值和错误契约。 |
| T4-1 | 是否允许 `sub/../file`、绝对路径和内部符号链接？ | SPEC / PLAN | 文件路径仅相对 POSIX；拒空、绝对、NUL、`.`、`..`；内部链接仅最终 realpath 留在工作区时允许。 | §3.3 从泛称围栏改为纯路径检查及现有/新建目标解析算法。 |
| T4-2 | 敏感名称只含 `.env` 还是需保护 Git、私钥、凭据；读写删是否一致？ | SPEC / PLAN | `.git`、`.env`、`.env.*`、列明的私钥及 `.guarded-agent/credentials*` 均对普通文件工具硬拒绝。 | §3.3 新增大小写和操作一致的敏感路径表；Git 仅经只读工具。 |
| T4-3 | “安全测试命令”是语义判断、白名单还是项目脚本也可允许？ | SPEC / PLAN | LLM 请求的 `rg`、`git status`、`git diff` 允许；安装、commit、项目脚本和未匹配命令需审批；提权、系统破坏和危险 Git 拒绝。 | §3.3 改为有序 argv 规则与稳定 rule_id，不再使用“safe test”猜测。 |
| T4-4 | 写入、搜索、Git 只读、验证器、记忆和未知工具如何治理？ | SPEC / PLAN | 读/搜索/有界写允许；删除/移动/安装/commit/项目脚本需审批；未知工具拒绝；配置中的精确验证器为系统预授权。 | §3.3 新增 v1 工具×条件×结果矩阵，并规定 validator 不接受 LLM 替换参数。 |
| T4-5 | 摘要是任意 JSON、RFC 8785 还是 Pydantic 输出？ | SPEC / PLAN | 小写 SHA-256 hex，UTF-8、键排序、无空白、无 NaN/Infinity；绑定任务、工具、规范化参数、realpath 和策略版本。 | §3.3 新增固定 JSON 对象与字节级规则。 |
| T4-6 | 审批创建即授权还是 PENDING；何时过期，如何原子消费？ | SPEC / PLAN | `PENDING → APPROVED | REJECTED | EXPIRED`，仅批准后可消费；默认 10 分钟；SQLite 条件更新/事务原子消费。 | §4.4、§7 Approval 和 Task 2/4 改为完整状态机与 API。 |
| T4-7 | 策略来自代码、TOML 或数据库；版本变化如何处理？ | SPEC / PLAN | 版本 `1.0` 编译入程序、仓库不可覆盖；评估失败拒绝；版本变化使审批失效。 | §3.3 明定 `Policy(version="1.0")` 与失败闭合行为。 |
| T4-8 | 恢复时重问 LLM、执行旧动作还是仅比较 digest？不匹配后怎样？ | SPEC / PLAN | 从持久化规范化动作恢复，完整重新评估并重算摘要；不匹配不执行、写 `approval_mismatch` 审计并反馈下一轮。 | §4.4 与 Task 7 加入精确恢复顺序和测试结果。 |

### 6.3 额外确认的交付约束

- 删除 Task 11 的 README 标题源码匹配测试。README 主题由人工验收并记录；Task 10 仍自动校验 GitLab 精确命名的 `unit-test` job。
- `.gitignore` 已由工作树设置提交 `308434d` 创建；Task 1 只能修改/扩展它，不得声称创建它。
- 公网部署仍明确不在范围内；本次审计不虚构 CI、部署、PR 或个人反思证据。

### 6.4 审计结论

审计前，Task 1 与 Task 4 不能仅凭规范无歧义实现。上述已确认修订关闭了列出的暂停点；实现工作仍必须遵守 PLAN 的 RED→GREEN 证据与后续验证门禁。

## 7. 冷启动修订的实现结果与交付偏离

冷启动提出的严格 DTO、配置上限、路径围栏、命令矩阵、摘要绑定和审批恢复要求均在 Task 1、2、4、5、7 中落地，并由各 task report 的 RED→GREEN 与 review 记录验证。尤其是 validator 从“任意验收文本”收紧为启动时配置的 opaque selector，避免 WebUI 和 LLM 绕过命令治理；审批恢复从模糊的“继续执行”变为加载持久化动作、重新评估、重算摘要并原子消费。

实现过程也验证了审计边界的价值：Task 4 的多轮 review 继续发现 path-qualified Git、ripgrep pattern file、symlink 和跨 task 审批变体，说明安全矩阵必须采用封闭规则并以失败测试维护。Task 10 则暴露了规范之外的冻结运行时差异：PyInstaller 中 `sys.executable` 指向打包后的应用自身，demo 因而改用临时受控可执行 validator，并由真实二进制 smoke test 覆盖。

最终范围仍保留用户明确批准的偏离：只交付未签名 Linux x86_64 单文件二进制和 localhost WebUI，不做公网部署，因此没有可访问的线上 URL。助教后续确认托管平台可任选，用户选择 GitHub；因此仓库新增 GitHub Actions 作为实际 hosted CI，并保留 `.gitlab-ci.yml` 兼容课程原始检查。最新 main 的 hosted workflow `31477957962` 已通过，artifact 仍受 GitHub 保留期限制。`REFLECTION.md` 已由项目负责人根据真实过程事实补写 1500–2500 中文字符，并标注 AI 辅助范围。
