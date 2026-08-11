# Guarded Agent 产品与系统规约

**版本：** 1.0-audit-validated
**日期：** 2026-08-09  
**状态：** 已完成冷启动审计修订；实现前规范基线

## 1. 问题陈述

LLM 可以提出代码修改和命令执行方案，但仅靠提示词无法保证它始终在授权边界内行动，也无法证明其“任务已完成”的判断可靠。Guarded Agent 面向需要在本地代码仓库中执行小型修复和功能任务的个人开发者，提供一个自研 Coding Agent Harness：它允许模型提出动作，却由确定性代码负责动作验证、风险治理、工具执行、客观反馈、记忆检索和停机判断。

项目的核心价值不是再次包装一个聊天接口，而是回答：如何让不确定的 LLM 决策在可审计、可测试、需要时由人介入的工程边界内作用于真实代码库。

### 1.1 目标用户

- 希望让 Coding Agent 在本地仓库中完成小型、可验证任务的个人开发者。
- 希望观察治理、反馈闭环和 mock-LLM 测试机制的 AI4SE 学习者。
- 需要检查 Agent 每一步动作、测试结果和审批记录的项目维护者。

### 1.2 目标

- 自研完整 Agent 主循环，不依赖任何现成 Agent 编排框架。
- 让文件、命令、审批、反馈、记忆和停机机制在移除真实 LLM 后仍可确定性测试。
- 深入实现治理维度：工作区围栏、命令风险分类、单次审批绑定、审计与资源限制。
- 提供 CLI 和仅绑定本机的 WebUI，共享同一 Harness 内核。
- 以 Linux x86_64 单文件可执行程序完成最小分发。

### 1.3 非目标

- 不提供多人账号、远程协作或插件市场。
- 不提供容器、虚拟机或操作系统级强隔离。
- 不安全执行恶意仓库中的任意程序。
- 不支持并行运行多个任务。
- 不提供公网部署、在线 WebUI URL、Docker 镜像或包管理器发布。
- 不保证进程退出后从正在执行的子进程中间恢复。

## 2. 用户故事

1. 作为开发者，我希望向指定本地仓库提交自然语言编码任务，以便 Agent 在明确工作区内读取、修改并验证代码。
2. 作为安全敏感的开发者，我希望危险动作在执行前被确定性拦截，以便模型是否遵守提示词不会成为安全边界。
3. 作为任务审批者，我希望看到动作、参数、风险理由和相关 diff 后只批准一次具体操作，以便批准不能被替换参数或重放。
4. 作为开发者，我希望测试或 lint 失败能自动反馈给下一轮，以便 Agent 根据客观结果修正，而不是自行宣称成功。
5. 作为审阅者，我希望在 WebUI 中查看轮次、工具调用、验证结果和审计事件，以便重建任务执行过程。
6. 作为离线评测者，我希望用 Mock LLM 运行完整主循环和机制演示，以便无 API Key、无网络也能复现实验结果。
7. 作为真实模型用户，我希望 API Key 经主密码加密后保存且日志不泄露明文，以便降低凭据进入仓库或审计记录的风险。
8. 作为本地 Agent 用户，我希望在同一个 WebUI 进程中连续发送多条消息，而不必为每条消息重新输入 vault 密码，以便自然地推进一个受治理任务。
8. 作为项目维护者，我希望保存经过确认的项目约定并按需检索，以便相关知识进入上下文而非加载全部历史。

这些故事可独立实现和验证；每个故事有明确价值，范围足以在单一任务中交付，并通过下文验收标准测试。

## 3. 领域与机制设计

### 3.1 动作与工具

LLM 只能返回经模式验证的结构化动作，不能直接访问文件系统或 shell。v1 工具名固定为 `list_directory`、`read_file`、`search_text`、`write_file`、`delete_file`、`move_file`、`git_status`、`git_diff`、`run_command`、`run_validator`、`save_memory`、`retrieve_memory`、`complete` 和 `cannot_continue`。

Task 1 定义严格 `Action` 外壳：它只含 `tool: ToolName` 和 `arguments: object` 两个字段，顶层 `extra="forbid"`；`ToolName` 仅能取上述 14 个值，未知工具在解析时拒绝。Task 5 把 `arguments` 完成为按 `tool` 判别的严格 Pydantic 联合；每个参数模型均 `extra="forbid"`，并在执行前验证类型、路径、长度及数量上限。Task 1 之前不实现工具执行，因此不得把宽松参数对象当作可执行动作。

工具由 `ToolRegistry` 注册并分发。每个工具公开名称、严格参数模型、风险元数据和执行接口。未知工具、额外字段、类型错误及超限参数在执行前被拒绝。

### 3.2 客观反馈信号

反馈来自用户或项目配置指定的测试、lint、类型检查及验收命令。`FeedbackEngine` 将结果分类为通过、测试失败、工具失败、超时、无效动作或策略违规，并产生有界、脱敏的结构化反馈。代码修改后自动运行验证；Agent 声明完成时必须再次运行验收命令，只有退出码为零且无策略违规才能完成。

### 3.3 危险动作

确定性治理策略依次执行动作校验、工具权限检查、路径解析、工作区围栏、敏感路径检查、命令分析和资源限制。`GovernanceOutcome` 是字符串枚举，值恰为 `ALLOW`、`REQUIRE_APPROVAL`、`DENY`。结果为：

- `ALLOW`：只读或低风险动作可执行。
- `REQUIRE_APPROVAL`：删除、移动、安装依赖、Git commit、项目脚本等动作暂停等待审批。
- `DENY`：提权、系统破坏、危险 Git 操作、工作区逃逸和敏感凭据访问不可审批。

所有文件工具的 `path` 参数都是相对工作区的 POSIX 路径字符串。空串、绝对路径、含 NUL、任一 `.` 段或 `..` 段一律拒绝。纯路径检查通过后，已有目标以 `resolve(strict=True)` 解析；新建目标解析其最近存在父目录再附加剩余段。最终 realpath 必须严格位于工作区 realpath 之下；内部符号链接仅在最终 realpath 仍在工作区内时允许。

普通文件工具对以下路径一律 `DENY`（读取、写入、删除和移动均相同）：任一路径段 `.git`；基名 `.env` 或以 `.env.` 开头；私钥基名 `id_rsa`、`id_dsa`、`id_ecdsa`、`id_ed25519`，或后缀 `.pem`、`.key`、`.p12`、`.pfx`；以及 `.guarded-agent/credentials*`。匹配大小写敏感。Git 数据仅可经专用只读 `git_status`、`git_diff` 工具取得，不能借普通文件工具访问。

工具矩阵如下；路径型工具先适用前两段硬拒绝，资源上限超出时拒绝而非截断后继续执行。

| 工具 | 条件 | 结果 / rule_id |
|---|---|---|
| `list_directory`、`read_file`、`search_text`、`git_status`、`git_diff`、`retrieve_memory`、`complete`、`cannot_continue` | 严格参数验证通过 | `ALLOW` / `read_only`；`complete` 仍须通过最终验收 |
| `write_file` | 目标非敏感、在工作区内，且单次内容不超过 65,536 bytes | `ALLOW` / `bounded_write` |
| `delete_file`、`move_file` | 目标通过路径及敏感规则 | `REQUIRE_APPROVAL` / `destructive_file_change` |
| `save_memory` | 内容和类别通过严格模型；审批者确认其为可信来源 | `REQUIRE_APPROVAL` / `memory_persistence` |
| `run_validator` | argv 与启动时从项目配置载入的某一精确验证命令完全相同 | `ALLOW` / `configured_validator` |
| `run_command` | 先按下列 argv 规则分类 | 按下列 rule_id |
| 其他或未来工具 | 任意参数 | `DENY` / `unknown_tool` |

`run_command` 按顺序匹配：

1. `DENY / hard_denied_command`：`sudo`、`doas`、`su`、`pkexec`、`shutdown`、`reboot`、`halt`、`poweroff`；含 `git reset --hard`、`git clean`、`git push --force` 或 `git checkout --` 的 argv；以及显式破坏系统根目录的 `rm` argv。此结果不可创建审批。
2. `ALLOW / approved_read_command`：以 `rg`、`git status` 或 `git diff` 开头的 LLM 请求；其所有路径操作数必须是通过上述相对路径规则的工作区路径，且不能包含 shell 解释器、重定向或命令替换语法。`git` 仅允许这两个精确子命令。
3. `REQUIRE_APPROVAL / command_requires_approval`：安装依赖、`git commit`、任何项目脚本（包括 `make`、`npm`、`pnpm`、`yarn`、`poetry`、`tox`、`nox`、`python -m`）以及所有未匹配命令。参数数组原样绑定，绝不以 `shell=True` 执行。

策略是编译入程序、仓库不可覆盖的 `Policy(version="1.0")`。若内置策略构造或完整评估发生不可恢复错误，当前动作 `DENY / policy_evaluation_failed`，且不创建审批；策略版本的任何变化都会使未消费审批无效。

动作摘要为小写十六进制 `SHA-256(UTF-8(canonical_json))`。`canonical_json` 是对象 `{"task_id": ..., "tool": ..., "arguments": ..., "workspace": canonical_absolute_path, "policy_version": "1.0"}` 的 JSON：键按 Unicode code point 升序、无空白、禁止 NaN/Infinity；路径参数先规范化为相对 POSIX 字符串，argv 保持数组顺序，`workspace` 使用 realpath。审批绑定该摘要、任务、工具、规范化参数、工作区、策略版本和过期时间；任何变化都必须重新审批。

### 3.4 记忆

长期记忆只接收用户确认的约定、确认的架构决策、验证命令，以及验收通过后的任务摘要。临时模型猜测不直接持久化。检索按工作区、类别、关键词和近期使用排序，只向模型提供有界数量的相关条目。

### 3.5 重点维度

治理是主要贡献，因为它能以确定性代码把模型建议与真实副作用隔开，并能在 Mock LLM 下验证。深入行为包括路径与符号链接围栏、命令风险分类、不可审批的硬禁令、动作摘要审批、单次消费、防重放、策略版本绑定、资源限制和审计脱敏。

## 4. 功能规约

### 4.1 Agent 主循环

**输入：** 任务目标、规范化工作区、验收命令、运行限制、LLM provider。  
**行为：** 加载相关上下文；调用 LLM；解析单个结构化动作；治理；执行或暂停；记录结果；运行反馈；判断继续或停机。  
**输出：** 任务状态、轮次历史、最终摘要和验收结果。  
**边界：** 单任务最多 20 轮、连续失败最多 4 次、总时限最多 1,800 秒；默认值即这些硬上限，可由配置收紧但不得放宽。
**错误处理：** 无效动作形成下一轮反馈；连续无效或不可恢复内部错误令任务失败；用户取消令任务进入 `CANCELLED`。

状态转换为：`CREATED → RUNNING ↔ WAITING_APPROVAL → COMPLETED | FAILED | CANCELLED`。`COMPLETED` 只能从最终验收通过的 `RUNNING` 进入。

### 4.2 LLM Provider

**输入：** 结构化消息和动作模式。  
**行为：** `MockLLMProvider` 按脚本返回动作；`OpenAICompatibleProvider` 通过单次 HTTP 对话补全 API 请求模型。  
**输出：** 一个候选结构化动作。  
**边界：** 不包含 Agent runner、自动工具调用循环或供应商 memory。  
**错误处理：** 网络错误、超时、HTTP 错误和无效响应转为分类错误；重试次数有界且不会重复副作用动作。

### 4.3 工具执行

**输入：** 已验证且获准的工具动作。  
**行为：** 在指定工作区执行；写文件使用临时文件和原子替换；命令使用参数数组且禁止 `shell=True`。  
**输出：** 退出码、标准输出/错误摘要、耗时、文件变更信息。  
**边界：** 命令有超时和输出上限；子进程只继承白名单环境变量；API Key 不进入子进程。  
**错误处理：** 工具异常被捕获并结构化，任务数据库仍记录失败事件。

### 4.4 治理与审批

**输入：** 任务、工作区、工具、规范化参数和当前策略。  
**行为：** 返回允许、需审批或禁止的决定及命中规则。需审批时创建 `PENDING` 记录，默认 TTL 为 10 分钟；状态只能为 `PENDING → APPROVED | REJECTED | EXPIRED`，且仅未过期的 `APPROVED → CONSUMED` 可执行一次。`approve(id)` 仅改变待处理记录；`consume_if_authorized(id, expected_digest, now) -> bool` 必须以一条条件 SQLite 更新或单个事务同时检查 `APPROVED`、未过期、未消费、摘要相等并写入 `CONSUMED`/`consumed_at`。
**输出：** 治理决定或待审批记录。  
**边界：** 配置不能关闭工作区围栏、敏感路径保护和硬禁令。  
**错误处理：** 过期、已消费或摘要不匹配的审批不能执行动作。恢复时从持久化的规范化动作读取，不重新请求 LLM；完整重新 `evaluate` 并重算摘要。只有结果仍为 `REQUIRE_APPROVAL`，且摘要、工作区和策略版本均相等时才能原子消费并执行一次。任一不匹配写入 `approval_mismatch` 审计事件，不执行动作，任务回到 `RUNNING` 并将结构化反馈交给下一轮。

### 4.5 反馈闭环

**输入：** 工具结果、验证命令和任务目标。  
**行为：** 运行确定性校验器，分类结果，截断并脱敏输出，将修正所需信息送入下一轮。  
**输出：** 结构化反馈和最终验收判定。  
**边界：** 完整日志保存在本地数据库；模型上下文只含有界摘要。  
**错误处理：** 校验器无法启动属于工具失败而非测试失败；超时单独分类。

### 4.6 记忆与配置

**输入：** 用户记忆操作、已通过任务摘要、工作区根目录的 `guarded-agent.toml`。
**行为：** `load_settings(workspace)` 接受绝对或相对路径，先执行 `Path.resolve(strict=True)`，结果必须是目录；允许调用者给出符号链接根目录，但该 realpath 是唯一工作区身份。它只读取该目录的 `guarded-agent.toml`；文件缺失时返回全部默认设置。校验配置；保存可信记忆；检索相关条目。
**输出：** 有界记忆集合或明确的配置错误。  
**边界：** 配置文件禁止包含凭据；模型推测不自动保存。  
**错误处理：** 无效配置阻止任务启动并抛出前缀固定为 `invalid configuration:` 的 `ConfigError`；数据库写入失败不会被报告为任务完成。

`guarded-agent.toml` 只允许以下表和键，所有未知根键、表键或嵌套键均为配置错误，且禁止出现 `[governance]` 表：

| 表 / 键 | 类型 | 默认值与范围 |
|---|---|---|
| `[limits].max_turns` | integer | `20`，1–20 |
| `[limits].max_consecutive_failures` | integer | `4`，1–4 |
| `[limits].total_timeout_seconds` | integer | `1800`，1–1800 |
| `[limits].command_timeout_seconds` | integer | `120`，1–120 |
| `[limits].max_output_bytes` | integer | `65536`，1–65536，适用于每个输出流 |
| `[validation].commands` | array of non-empty string arrays | 默认 `[]`；每个 argv 在启动时原样载入，作为唯一可预授权的系统验证器 |

配置不得包含凭据或改变策略、路径围栏、敏感路径、命令分类或硬上限的键。CLI 和 WebUI 都只能从上述已载入的验证命令中选择验收命令；WebUI 不接受任意验收命令文本。

### 4.7 CLI

提供 `run`、`web`、`demo`、`credential`、`memory` 和 `version` 子命令。默认使用 Mock LLM。真实模型模式必须显式选择 provider 并从终端解锁凭据。缺少工作区、无效配置或缺少验收命令时返回非零退出码和可操作的错误信息。

### 4.8 WebUI

WebUI 仅监听 `127.0.0.1`，使用服务端 HTML 和少量原生 JavaScript，通过轮询展示任务状态，并提供 CSRF 保护的同源聊天 JSON 接口。默认使用 Mock provider；显式选择 `openai-compatible` 时，服务启动阶段解锁一次 vault，聊天面板的每条消息推进一次现有 AgentLoop。页面覆盖任务创建、持续对话、执行时间线、工具输出和 diff、审批、取消、记忆管理及凭据状态。WebUI 不接收主密码，不允许浏览服务器任意目录，不允许修改硬安全规则，不提供任意 shell 输入。

### 4.9 凭据管理

`credential set` 隐藏输入主密码和 API Key；Scrypt 使用随机盐派生密钥，认证加密保存秘密。`status` 只显示 provider 和是否配置；`clear` 删除加密凭据。忘记主密码时不可恢复，只能清除重设。

### 4.10 机制演示

`guarded-agent demo` 在临时工作区和 Mock LLM 下确定性演示：危险删除被治理；首次错误修改收到失败反馈后改正；篡改已审批参数导致审批失效。命令不需要网络或 API Key，成功返回零退出码。

## 5. 非功能性需求

### 5.1 性能

- 除 LLM 和外部验证命令外，单次治理决策在普通开发机上目标低于 50 ms。
- 单次模型上下文最多包含最近 8 个轮次摘要和 10 条相关记忆。
- 单项工具输出提供可配置上限，默认每个流 64 KiB；超出部分保留头尾并标记截断。

### 5.2 安全与凭据威胁模型

威胁包括：秘密被提交 Git、进入日志、传给子进程或浏览器；模型构造路径逃逸或危险命令；审批被替换或重放；恶意仓库通过测试命令执行任意代码。对策包括加密凭据库、隐藏输入、日志脱敏、最小环境继承、真实路径围栏、敏感路径硬禁令、无 shell 命令执行、审批摘要和单次消费。

剩余风险是项目脚本本身可包含恶意代码。本项目是应用级护栏而非强沙箱，因此只允许对可信仓库运行构建和测试，并禁止 WebUI 监听公网地址。

### 5.3 可用性

- 默认 Mock 模式不需配置即可运行演示。
- 错误消息说明失败阶段、原因和可采取的下一步。
- WebUI 的允许、等待审批、拒绝、失败和通过状态具有文字标签，不只依靠颜色。

### 5.4 可观测性

- 每个任务、轮次、工具执行、治理决定和审批都有稳定 ID 与时间戳。
- 审计载荷写入前脱敏；不得保存主密码或 API Key。
- CLI 可输出人类可读结果，内部组件使用结构化事件。

### 5.5 可靠性

- SQLite 启用外键和事务；状态变更与关键审计事件在同一事务边界内提交。
- 写文件采用同目录临时文件和原子替换。
- 每个副作用动作只在治理允许且审批有效时执行一次。

## 6. 系统架构

```text
CLI / Local WebUI
        │
        ▼
ApplicationService
        │
        ▼
AgentLoop ───────► LLMProvider
   │
   ├─────────────► GovernanceEngine ─► ApprovalService
   ├─────────────► ToolRegistry ─────► Workspace / subprocess
   ├─────────────► FeedbackEngine
   ├─────────────► MemoryStore
   ├─────────────► TaskStore / ConversationStore / AuditStore / SQLite
   └─────────────► same-origin chat endpoints / transcript UI

CredentialVault ─► OpenAICompatibleProvider（仅真实模型模式）
```

采用模块化单体。CLI 和 WebUI 只调用应用服务，不复制 Agent 逻辑。任务在后台执行器中运行，同一时间最多一个。SQLite 保存当前状态及追加式审计事件，但不实现完整事件溯源。

## 7. 数据模型

### 7.1 v1 跨模块 DTO 契约

所有 DTO 都是 `extra="forbid"` 的 Pydantic 模型、字段可 JSON 序列化且不含自由扩展字段。枚举以其字符串值序列化。

| DTO | 固定字段 |
|---|---|
| `Action` | `tool: ToolName`、`arguments: dict[str, JSONValue]`；Task 5 将后者替换为按工具判别的严格联合 |
| `ToolResult` | `tool: ToolName`、`exit_code: int | None`、`stdout: str`、`stderr: str`、`stdout_truncated: bool`、`stderr_truncated: bool`、`duration_ms: int`、`changes: list[str]` |
| `Feedback` | `kind: FeedbackKind`（`PASS`、`TEST_FAILURE`、`TOOL_FAILURE`、`TIMEOUT`、`INVALID_ACTION`、`POLICY_VIOLATION`）、`message: str`、`command_result: ToolResult | None`、`can_continue: bool` |
| `GovernanceDecision` | `outcome: GovernanceOutcome`、`rule_id: str`、`reason: str`、`action_digest: str | None`、`approval_id: str | None` |
| `TaskStatus` | `CREATED`、`RUNNING`、`WAITING_APPROVAL`、`COMPLETED`、`FAILED`、`CANCELLED` |
| `Settings` | `max_turns: int`、`max_consecutive_failures: int`、`total_timeout_seconds: int`、`command_timeout_seconds: int`、`max_output_bytes: int`、`validation_commands: list[list[str]]` |

| 实体 | 关键字段 | 约束与关系 |
|---|---|---|
| Workspace | id, canonical_path, name, created_at | canonical_path 唯一 |
| Task | id, workspace_id, goal, status, acceptance_commands, limits, timestamps | 属于一个 Workspace |
| AgentTurn | id, task_id, turn_no, action_json, feedback_json | `(task_id, turn_no)` 唯一 |
| ToolExecution | id, turn_id, tool, normalized_args, result, duration | 属于一个 AgentTurn |
| Approval | id, task_id, action_digest, policy_version, summary, status, expires_at, approved_at, consumed_at | `PENDING → APPROVED | REJECTED | EXPIRED`；仅原子 `APPROVED → CONSUMED` 一次 |
| MemoryEntry | id, workspace_id, category, content, source, trust, keywords | 只存可信来源 |
| AuditEvent | id, task_id?, event_type, redacted_payload, previous_digest, created_at | 追加写入 |
| ConversationMessage | id, task_id, role (`user`/`agent`), bounded content, created_at | 属于一个 Task；按时间排序；不保存主密码、API Key 或原始模型响应 |
| ProjectConfig | workspace_id, config_digest, parsed_values | 不含凭据 |

## 8. 凭据与分发设计

### 8.1 凭据流程

- 录入：终端隐藏读取主密码及 API Key，加密后原子保存。
- 查看：仅显示 provider、endpoint 和“已配置”，不回显 Key。
- 更新：成功解锁或明确覆盖后生成新的盐和密文。
- 清除：删除凭据文件并记录不含秘密的审计事件。
- 使用：CLI `run` 每次进程启动时解锁；真实 provider WebUI 在服务启动时解锁一次，明文只保留在服务进程内，不写日志、不传浏览器、不传工具子进程；服务重启后重新解锁。

### 8.2 分发

唯一分发形态为 PyInstaller 构建的 Linux x86_64 单文件可执行程序 `guarded-agent`。CI 构建产物作为 artifact 提供。二进制未签名；README 说明目标平台、CPU 架构、执行权限、首次运行拦截和已知限制。

启动示例：

```bash
./guarded-agent web --workspace /absolute/path/to/project
./guarded-agent demo
```

不提供 Docker、PyPI 或公网服务。此决定满足“分发形态任选其一”，但偏离最终清单中的线上部署 URL 要求；该偏离由用户明确选择，并记录在过程文档和 `AGENT_LOG.md`。

## 9. 技术选型与理由

- Python 3.12：开发和测试生态成熟，便于实现 CLI、SQLite 和单文件打包。
- FastAPI/Starlette：用少量代码提供本地路由和测试客户端，不承担 Agent 编排。
- Jinja2 + 原生 JavaScript：避免独立前端构建链。
- Pydantic：严格校验 LLM 动作和配置边界。
- HTTPX：直接调用供应商单次对话补全 API。
- SQLite 标准库：无需外部服务，适合单用户本地状态。
- Cryptography：提供 Scrypt 和认证加密原语。
- Pytest：支持确定性单元与集成测试。
- PyInstaller：生成 Linux x86_64 单文件二进制。

WebUI 采用最小的 Jinja2 服务端渲染、原生 CSS/JavaScript、同源 JSON 聊天接口和 localhost
轮询，没有使用 Open Design 设计系统或对应 skill。原因是用户明确选择最简单的
本地控制台，界面只承担任务创建、持续对话、时间线、diff 与审批，不建设通用组件库或品牌化
前端；这是有意识的流程偏离，并在 `AGENT_LOG.md` 中披露。

不使用 LangChain AgentExecutor、AutoGen、CrewAI、LlamaIndex Agent 或任何编码智能体 SDK 的 Agent runner。

## 10. 验收标准

1. `make test` 在无网络、无 API Key 环境中运行全部测试并返回零。
2. GitHub Actions 包含 `unit-test` job 并在 Pull Request/push 上运行；兼容的 `.gitlab-ci.yml` 也保留精确命名的 `unit-test` job。
3. Mock LLM 能驱动“上下文—动作—治理—工具—反馈—停机”的完整循环。
4. 工作区外路径、符号链接逃逸和敏感文件访问在执行前被拒绝。
5. 删除或移动等动作进入审批；硬禁令没有批准入口。
6. 已批准动作发生参数、工作区或策略版本变化时不能执行；审批只消费一次。
7. 注入一次测试失败后，下一轮 Mock LLM 收到分类反馈并改变动作，最终验收通过。
8. Agent 的完成动作不会绕过最终验收；验收失败时任务继续或按限制失败。
9. API Key 加密落盘，状态输出、日志、数据库、Web 响应和子进程环境均无明文。
10. 本地 WebUI 能创建 Mock 任务、查看时间线、查看 diff、审批/拒绝及取消任务。
11. `guarded-agent demo` 离线复现治理、反馈修正和防审批篡改三个场景。
12. PyInstaller 产物在 Linux x86_64 上成功执行 `version` 与 `demo`。
13. 真实 provider WebUI 启动时只提示一次 vault 密码；连续消息能够持久化并推进一个受治理任务。
14. 聊天端点拒绝无效 CSRF、空/超长消息和跨工作区任务，并且 transcript 使用文本渲染而不插入模型 HTML。

## 11. 测试策略

- 遵守红—绿—重构；每个行为先运行一个因缺少实现而正确失败的测试。
- 单元测试覆盖动作模式、治理规则、工具分发、反馈分类、记忆、停机和凭据。
- 主循环集成测试只使用 Mock LLM 和临时工作区。
- WebUI 使用进程内测试客户端，不依赖浏览器或网络。
- 二进制构建后运行冒烟测试。
- 安全回归覆盖路径穿越、符号链接、敏感值脱敏、审批重放和参数替换。

## 12. 风险与已决问题

| 风险 | 决策或缓解 |
|---|---|
| 测试脚本本身恶意 | 只运行可信仓库；明确不是强沙箱 |
| shell 语法绕过规则 | 禁止 `shell=True`，只接受参数数组；复杂命令默认审批或拒绝 |
| PyInstaller 隐藏依赖遗漏 | CI 构建后运行 `version` 和 `demo` 冒烟测试 |
| 主密码遗忘 | 不提供恢复；清除凭据后重新配置 |
| LLM 输出不稳定 | 严格动作模式、错误反馈和连续失败上限 |
| 持续对话状态增长 | 只保存限长消息，provider 上下文只带最近 12 条对话和最近 8 个 AgentTurn |
| WebUI 进程退出 | 不持久化主密码；重启后重新解锁 vault，任务状态仍保留在 SQLite |
| 输出污染模型上下文 | 分类、脱敏、截断，只提供相关摘要 |
| 公网 WebUI 条款未满足 | 用户选择本地最小实现；在交付文档中透明记录评分风险 |
| REFLECTION 学术归属 | 只创建由学生本人填写的结构和事实索引，不代写个人反思正文 |

## 13. 交付物范围

交付 `SPEC.md`、`PLAN.md`、`SPEC_PROCESS.md`、`AGENT_LOG.md`、学生填写的 `REFLECTION.md`、README、完整源码、Mock LLM 测试、机制演示、构建脚本、PyInstaller 配置、GitHub Actions workflow 和兼容的 `.gitlab-ci.yml`；持续对话扩展的设计与计划另存于 `docs/superpowers/specs/2026-08-11-local-conversational-agent.md` 和 `docs/superpowers/plans/2026-08-11-local-conversational-agent.md`。GitHub 是实际托管平台；最终 CI 通过记录、artifact 保留和学生个人反思仍需仓库所有者确认。
