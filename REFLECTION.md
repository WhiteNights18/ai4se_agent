# 项目反思报告（学生本人填写）

> 学术规范：本文件的个人分析必须由学生本人撰写，正文要求 **1500–2500 个中文字符**。下面只提供结构、可核验事实索引和写作提示，不构成反思正文。若后续仅使用 AI 润色，须按课程要求主动标注。

## 1. Superpowers 技能的实际作用

<!-- 学生填写：哪些技能最有价值，哪些“形式大于实质”，结合具体 task 和决策说明。 -->

事实索引：`SPEC_PROCESS.md` 记录 brainstorming 的四轮取舍；`PLAN.md` 和 `AGENT_LOG.md` 记录 writing-plans、worktree、subagent、TDD 与评审过程。2026-08-10 接入真实 provider 时修复了 HTTP 400（commit `0469074`）：SPEC 定义的自研消息协议在 OpenAI/DeepSeek 端被拒，因为真实 API 只接受标准 system/user/assistant 角色且 content 必须是非空字符串；修复新增 `src/guarded_agent/providers/openai_messages.py` 翻译层，并让 `_extract_json` 容忍 markdown 围栏与前后散文。该案例可作"规约与真实世界的碰撞"引用。

## 2. TDD：阻碍还是放大器

<!-- 学生填写：结合至少一个 RED→GREEN 案例分析，而不是只罗列测试数。 -->

可选案例：Task 3 的凭据 envelope/endpoint/redaction 边界；Task 4 的命令语法与审批绑定；Task 5 的符号链接、输出边界和子进程清理。对应事实见 `.superpowers/sdd/PLAN/task-*-report.md`。HTTP 400 修复（commit `0469074`）也是案例：先在 `tests/providers/test_openai_compatible.py` 增加 markdown 围栏 / 散文包裹 JSON、翻译 schema 的失败测试，再实现翻译层使其变绿。

## 3. subagent 自主性与最佳 task 粒度

<!-- 学生填写：自主运行多久会偏题、哪些 review round 说明 task 太宽或刚好、人工在何处介入。 -->

事实索引：Task 2–5 经多轮修复；Task 10 的打包 demo 在冻结环境暴露 `sys.executable` 差异，最终改用临时可执行 validator。

## 4. SPEC / PLAN 如何影响实现

<!-- 学生填写：至少给一个规约不清导致暂停或偏离的具体案例，并判断责任在 spec 还是解读。 -->

可选事实：`SPEC_PROCESS.md` §6 的 T1/T4 冷启动问题、规范化动作摘要、验证器选择器及审批恢复语义。另一个案例：SPEC 的消息协议未约束真实 HTTP 端（OpenAI/DeepSeek）接受的角色集合，导致接入真实模型时 HTTP 400；修复需把内部协议翻译为标准 chat 消息并改写系统提示（commit `0469074`），可讨论"规约边界应覆盖外部契约还是由实现层适配"。

## 5. 最有效的 prompt / context 策略

<!-- 学生填写：说明具体 prompt/context 结构以及为何有效，避免泛泛而谈。 -->

事实索引：`src/guarded_agent/providers/openai_messages.py` 的 `_ACTION_CONTRACT` 把 14 个工具写成严格 JSON schema，并写死三条路径规则（相对 POSIX、禁 `.`/`..`、根目录不可列出）与循环语义（写/删/移/run_command 后自动跑验收并回灌反馈）；配合 `_extract_json` 容错解析，使真实模型（DeepSeek）一次运行即完成 `trusted-proj` 修复任务（commit `0469074`）。可用作"结构化的动作契约比自由文本更稳定"的实证。
## 6. 凭据与分发带来的工程思考

<!-- 学生填写：讨论加密 vault、隐藏输入、脱敏、Linux x86_64 单文件构建、签名与首次执行权限。 -->

事实索引：凭据 commits `6eaec9b`、`82e943b`、`4e1c9ef`；打包 commit `154ea5c`；README 的“凭据管理”“分发”“已知限制”。另一次真实运行暴露 `cd ~/.local/share/guarded-agent && python -m guarded_agent` 报 `No module named guarded_agent`（venv 只装在仓库 `.venv`），README 已在安装节补充绝对路径与 activate 说明（commit `da594c1`）——可用作"分发文档必须覆盖模块可见性"案例。

## 7. 如果重做会改变什么

<!-- 学生填写：从架构、测试、任务拆分、工具或交付流程中选择具体改进。 -->

## 8. 对 Superpowers 方法论的批判

<!-- 学生填写：它依赖哪些关于任务可分解性、上下文隔离、review 成本和 Git 平台的假设？这些假设在本项目是否成立？ -->

## 写作完成前自检

- [ ] 正文为学生本人撰写，中文字符数在 1500–2500 范围。
- [ ] 回答了课程列出的核心问题，并引用至少三个具体 task/commit/测试案例。
- [ ] 清楚区分个人判断和仓库中可验证的事实。
- [ ] 如使用 AI 辅助润色，已如实标注范围和方式。
- [ ] 删除所有 `<!-- 学生填写 -->` 占位内容后再提交最终版本。
