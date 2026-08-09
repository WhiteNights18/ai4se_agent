# Agent 实施日志

时间均为 Asia/Shanghai。日志依据会话决策、Git 历史和 `.superpowers/sdd/PLAN/task-*-report.md` 回填；无法由仓库验证的 agent 型号、PR 或 hosted CI 不作成功声明。

## 2026-08-09：规约、计划与冷启动（Task 0）

- **15:36–17:16；技能：** `brainstorming`、`writing-plans`、`using-git-worktrees`。
- **关键 prompt/context：** 阅读 A 项目与通用要求；用户逐步确认治理优先 Harness、本地 WebUI、仅 Linux x86_64 原生二进制、加密凭据库和模块化单体，并明确不要公网部署。
- **输出：** `SPEC.md`（`5228011`）、`PLAN.md`（`a131c17`）；冷启动审计修订 `585d0bb`、`7e49e98`，状态提交 `f7049e5`。
- **人工干预：** 用户推翻 Docker/公网方案，选择最小本地交付；批准无公网 URL 的显式偏离。冷启动审计被如实标为 no-history best-effort，不能独立证明“不同 agent 类型”。
- **教训：** 分发三选一与最终清单中的公网 URL 是两个独立约束；审批摘要、恢复与 validator 选择器必须写成字节级/状态级契约。

## 2026-08-09：领域与持久化（Task 1–2）

- **17:26–19:06；技能：** `subagent-driven-development`、`test-driven-development`、`requesting-code-review`。
- **关键 context：** 新鲜 task 上下文只包含 SPEC/PLAN 契约、文件范围、RED 命令和验收门禁。
- **RED→GREEN：** Task 1 先因 `guarded_agent.domain/config` 不存在而 collection error，最终 16 tests；Task 2 先因 `storage/memory` 不存在而 collection error，经状态机、事务和审批并发修复后 39 tests。
- **输出/commits：** Task 1 `344c561`, `ec0a5fb`；Task 2 `1bab46b`, `9306644`, `5b88ca8`, `b81574e`；状态 `d9b2a26`, `239a588`。
- **人工干预与 review：** controller 因 subagent 无权写共享 worktree Git index 而代为提交；三项非阻塞 minor（memory workspace FK、raw connection/文件规模、测试命名）推迟到最终 review。
- **教训：** 数据库不变量需要在 SQL 条件更新/事务中实现，不能只靠调用者顺序。

## 2026-08-09：凭据与治理（Task 3–4）

- **19:11–20:53；技能：** TDD、系统化安全评审、两阶段 code review。
- **RED→GREEN：** Task 3 从缺失模块开始，review 测试继续暴露重复 JSON key、oversize、endpoint 和跨替换边界脱敏问题，最终 75 tests；Task 4 从缺失 paths/governance 开始，多轮命令 grammar、symlink、validator 与跨 task 审批绕过测试，最终 225 tests。
- **输出/commits：** Task 3 `6eaec9b`, `82e943b`, `4e1c9ef`；Task 4 `9727080`, `dbf9ad7`, `ba5ce93`, `673b7f1`；状态 `492b1c1`, `d6dbe8f`。
- **人工干预：** review 发现即增加失败回归测试，再做最小修复；未把应用层护栏描述为 OS 沙箱。
- **教训：** “安全命令”必须是封闭 argv 语法；批准必须绑定 task/workspace/policy/action digest 且单次原子消费。

## 2026-08-09：工具、反馈与 provider（Task 5–6）

- **21:12–22:58；技能：** TDD、systematic debugging、两阶段 review。
- **RED→GREEN：** Task 5 依次出现缺失模块、超时子进程存活、输出无界、FIFO/symlink 和诊断泄漏失败，经四轮修复达到 290 tests；Task 6 provider collection RED 后用 HTTPX MockTransport 离线验证，达到 295 tests。
- **输出/commits：** Task 5 `ac0a4e0`, `4cc6140`, `95bc46e`, `2d1f77c`, `e56743d`；Task 6 `a70b2a6`；状态 `ea0d29b`, `81df093`。
- **人工干预：** 坚持真实文件系统/子进程边界测试；真实 LLM 网络不进入测试。
- **教训：** canonical path 只是策略快照，实际工具仍需 dirfd/no-follow；截断后仍须满足 byte bound 与脱敏后置条件。

## 2026-08-09：主循环、CLI 与 WebUI（Task 7–9）

- **23:07–23:55；技能：** TDD、subagent-driven development、code review。
- **RED→GREEN：** Task 7 完成 pause/resume、false completion 与 stop-limit 回归，305 tests；Task 8 CLI/demo 达 309 tests；Task 9 Web 路由、CSRF、审批绑定与轮询达 311 passed，Python 3.14 下 8 个 ASGI 测试按已知兼容问题 skip。
- **输出/commits：** Task 7 `10019f3`, `e47494d`, `baca243`；Task 8 `f09d40d`；Task 9 `2fa8eed`, `0aa75e4`, `3266620`, `be546cf`；状态 `2e31154`, `f9d26ff`。
- **人工干预：** 用户多次要求压缩非必要工作与 token；因此采用服务器渲染+轮询的最小 WebUI，并固定 `127.0.0.1` 和启动工作区。
- **教训：** Web 层不能重新引入任意命令、路径或凭据入口；审批 UI 必须指向持久化 pending action。

## 2026-08-10：README、二进制与 CI 配置（Task 10）

- **00:00–00:52；技能：** systematic debugging、TDD、verification-before-completion。
- **关键 prompt/context：** 用户要求 README 严格按课程文档，分发仅选择最简单的原生二进制，不做公网部署。
- **RED→GREEN：** packaging tests 先约束 spec、脚本、精确 `unit-test` job 和 artifact；冻结二进制的 `demo` 起初失败，根因为 `sys.executable` 在 PyInstaller 中指向自身，改用临时受控可执行 validator 后通过。
- **输出/commits：** README `ca1cc20`；PyInstaller、构建脚本和 GitLab CI `154ea5c`。
- **最新本地证据：** Python 3.14 环境 `316 passed, 8 skipped`；Ruff、mypy、binary build、`version` 和三场景 `demo` 均通过。此证据不等同于 hosted CI pass。
- **教训：** 源码解释器假设在 frozen 运行时会改变；打包 smoke test 必须执行真正产物。

## 2026-08-10：Task 11 文档人工验收

- **技能/动作：** 按 Task 11 人工逐项阅读 README，并以实际 CLI、`pyproject.toml`、`.gitlab-ci.yml` 和 commit 历史交叉核对；未创建脆弱的标题源码测试。
- **验收结果：** README 已覆盖项目简介、源码安装、CLI/WebUI、二进制获取/本地构建、`chmod +x`、Linux x86_64/未签名、凭据 set/status/update/clear、架构、测试、安全边界、可信仓库警告、无公网 URL 和第三方许可证链接；示例子命令与当前 CLI 一致。
- **文档产物：** `THIRD_PARTY_LICENSES.md` 只声称直接依赖；`REFLECTION.md` 仅提供事实索引与学生模板，不代写个人反思。

## SPEC §10 最终逐项核对

| # | 仓库证据与结论 |
|---|---|
| 1–2 | `make test` 本地离线返回零；GitHub Actions 与兼容的 `.gitlab-ci.yml` 均存在 `unit-test` job。 |
| 3 | Mock provider、`AgentLoop` 与 `tests/test_agent_loop.py` 覆盖完整循环。 |
| 4–6 | paths/governance 测试覆盖越界、符号链接、敏感路径、风险分类、摘要变化和单次消费。 |
| 7–8 | feedback/agent-loop 测试及 `demo` 覆盖失败修正和禁止虚假完成。 |
| 9 | credentials/redaction/subprocess 测试覆盖加密落盘及输出、存储、Web、环境脱敏。 |
| 10 | Web 路由与模板具备创建、时间线、diff、审批/拒绝和取消；目标 Python 3.12 CI 执行 ASGI 测试。 |
| 11–12 | 真实 `dist/guarded-agent` 的 `version` 与三场景 `demo` 均返回零。 |

课程仓库内交付物均已提供或如实留模板；远端可见性、PR 页面、hosted CI pass、artifact 下载、学生反思和公网 URL 的状态见下节，未用本地结果冒充。

## 已知偏离与外部责任

- **明确的用户决策：** 不做公网部署，因此没有线上 WebUI URL；这是对最终交付清单 URL 条款的已知偏离，不伪造补齐。
- **前端流程偏离：** 为落实用户要求的最小本地 WebUI，采用 Jinja2 服务端渲染和原生静态资源，未使用 Open Design 或其 skill；`SPEC.md` 已补充理由。
- **最终两阶段审查：** 规约审查与代码质量/安全审查均无 Critical；据审查将 Python 声明收紧为 3.12.x，并记录 Open Design 取舍。冷启动不同 agent 类型、真实 PR 和远端 CI 等历史/平台证据不能事后伪造。
- **托管平台更新（2026-08-10）：** 助教确认平台可任选，用户选择 GitHub。功能分支已推送并创建 PR #1；新增 GitHub Actions 作为实际 hosted CI，保留 GitLab 配置仅作兼容。
- **仍需学生/平台完成：** 确认最后一次 GitHub Actions workflow 为 pass；保留或下载 Linux x86_64 artifact；合并 PR；由学生本人完成 1500–2500 中文字符 `REFLECTION.md`。
