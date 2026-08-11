# Local Conversational Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a local-only WebUI conversation MVP that unlocks the real-provider vault once per server process and advances one governed task across successive user messages.

**Architecture:** Add a small persisted conversation-message repository, inject an optional provider-backed `ApplicationService` into the existing WebUI, and expose CSRF-protected JSON chat endpoints. Reuse AgentLoop, governance, approvals, audit, redaction, and the existing workbench shell; do not add streaming or remote authentication.

**Tech Stack:** Python 3.12+, FastAPI, SQLite, Jinja2, vanilla JavaScript, existing OpenAI-compatible provider.

## Global Constraints

- WebUI binds only to `127.0.0.1`.
- API keys and vault passwords never enter logs, HTML, database messages, or subprocess environments.
- User input remains length-limited, workspace-bound, CSRF-protected, and rendered as text.
- Existing Mock provider default and deterministic governance behavior remain unchanged.
- No new runtime dependency is added.

---

### Task 1: Persist conversation messages

**Files:**
- Modify: `src/guarded_agent/storage.py`
- Modify: `tests/test_storage.py`

**Interfaces:**
- Produce `ConversationMessage` data object and `ConversationStore.add/list_for_task` methods.
- Store role, task id, bounded content, and UTC timestamp; reject unknown roles and oversized content.

- [ ] Add failing tests for message ordering, persistence after reopen, and role/content validation.
- [ ] Run focused storage tests and observe the expected failures.
- [ ] Add the schema and repository with parameterized SQL and bounded values.
- [ ] Run focused and full tests.
- [ ] Commit `feat: persist conversational messages`.

### Task 2: Provider-backed WebUI service flow

**Files:**
- Modify: `src/guarded_agent/service.py`
- Modify: `src/guarded_agent/web.py`
- Modify: `src/guarded_agent/cli.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- `create_web_app(..., provider: LLMProvider | None = None)` preserves the current Mock-compatible default.
- Add a CSRF-protected `POST /api/chat/messages` and read-only `GET /api/chat/messages`.
- A message advances the current task through the existing governed service and returns status, latest feedback, and safe transcript data.

- [ ] Add failing endpoint tests for CSRF, empty/oversized messages, workspace isolation, and provider-backed completion.
- [ ] Implement startup provider injection and a single active task/session guard.
- [ ] Persist the user message before the governed step and append only safe feedback summaries.
- [ ] Preserve the existing task creation and approval routes.
- [ ] Run web tests and full tests.
- [ ] Commit `feat: expose governed chat session endpoints`.

### Task 3: Workbench conversation UI and documentation

**Files:**
- Modify: `src/guarded_agent/templates/base.html`
- Modify: `src/guarded_agent/templates/tasks.html`
- Modify: `src/guarded_agent/static/app.js`
- Modify: `src/guarded_agent/static/app.css`
- Modify: `tests/test_web.py`
- Modify: `README.md`
- Modify: `AGENT_LOG.md`

**Interfaces:**
- Add a transcript panel, composer, busy/error states, and safe JSON rendering.
- Reuse theme, responsive, focus-visible, and reduced-motion contracts.

- [ ] Add failing source/render contracts for transcript, composer, endpoint, and text-only updates.
- [ ] Implement the compact Chinese conversation panel and same-origin fetch flow.
- [ ] Keep approval and task detail links visible when status is `WAITING_APPROVAL`.
- [ ] Update local-only usage and credential lifecycle documentation.
- [ ] Run full tests, quality, binary build, version, demo, and diff-check.
- [ ] Commit `feat: add local conversational workbench`.

### Task 4: Review, verify, and deliver

- [ ] Review the branch against the design and quality/security gates.
- [ ] Fix all Critical and Important findings through a fresh fix pass.
- [ ] Push `feature/conversational-agent` and open a PR to `main`.
- [ ] Confirm GitHub CI status and report any browser-evidence limitation without fabricating screenshots.

