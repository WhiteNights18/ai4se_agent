# Guarded Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a governance-first local Coding Agent Harness whose own loop, tools, feedback, memory, approvals, credentials, CLI, and WebUI remain deterministically testable without a real LLM.

**Architecture:** A Python 3.12 modular monolith exposes one application service to CLI and local FastAPI WebUI. A hand-written agent loop coordinates provider, governance, tools, feedback, SQLite stores, and an encrypted credential vault; PyInstaller produces one Linux x86_64 binary.

**Tech Stack:** Python 3.12, Pydantic 2, FastAPI/Starlette, Jinja2, HTTPX, SQLite, Cryptography, Typer, Pytest, Ruff, mypy, PyInstaller.

## Global Constraints

- Do not use LangChain AgentExecutor, AutoGen, CrewAI, LlamaIndex Agent, or any SDK agent runner.
- Every production behavior follows RED → GREEN → REFACTOR; preserve the failing-test output in `AGENT_LOG.md`.
- Core tests must run without network, API keys, or a real LLM.
- WebUI binds only to `127.0.0.1`; it never accepts a master password or arbitrary shell text.
- Commands are argument arrays and never use `shell=True`.
- Hard security rules cannot be disabled by repository configuration.
- Only one task may run at a time.
- Distribution target is an unsigned Linux x86_64 PyInstaller single-file binary.
- No Docker, package-registry release, public deployment, or online URL is in scope.
- `REFLECTION.md` personal prose must be written by the student; automation may provide only a fact index/template.

## Dependency and parallelism map

```text
Task 0 → Task 1 → ┬→ Task 2 (storage/memory) ─┐
                  ├→ Task 3 (credentials)    │
                  └→ Task 4 (governance) ────┤
Task 1 ─────────────→ Task 5 (tools/feedback)┤
Task 1 ─────────────→ Task 6 (providers) ────┤
                                             ▼
                                          Task 7
                                             ▼
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                           Task 8         Task 9          Task 10
                              └──────────────┼──────────────┘
                                             ▼
                                          Task 11
```

Tasks 2–6 may be implemented in isolated worktrees after Task 1. Tasks 8–10 depend on the integrated loop in Task 7. Task 11 is the final documentation, packaging, CI, and verification gate.

---

### Task 0: Cold-start specification audit

**Status:** Complete — commits `585d0bb`, `7e49e98`; task review clean.

**Files:**
- Modify: `SPEC_PROCESS.md`
- Modify because defects were found: `SPEC.md`, `PLAN.md`

**Interfaces:**
- Consumes: approved `SPEC.md`, `PLAN.md`, cold-start audit and confirmed resolutions
- Produces: a recorded cold-start audit and corrected unambiguous specifications

- [x] **Step 1: Request an isolated, different agent type**

Requested a no-history `gpt-5.6-terra` dispatch with only `SPEC.md` and `PLAN.md` and the instruction “遇到不确定之处即暂停询问，而非凭猜测继续；不要写实现代码。” The auditor could not independently verify that it was a different agent type; record this limitation and describe the result only as a no-history best-effort audit.

- [x] **Step 2: Record every question and divergent interpretation**

Append the agent type, isolated context, paused questions, expected interpretation, actual interpretation, and whether the defect belongs to SPEC or PLAN to `SPEC_PROCESS.md`.

- [x] **Step 3: Patch specification defects**

For each defect, include a concise before/after excerpt in `SPEC_PROCESS.md`; remove ambiguity in the normative document.

- [x] **Step 4: Verify documentation consistency**

Run: `rg -n 'TBD|TODO|待定|implement later|fill in details' SPEC.md PLAN.md SPEC_PROCESS.md`

Expected: no unresolved placeholder in normative sections; historical quoted text must be explicitly labelled.

- [x] **Step 5: Commit**

```bash
git add SPEC.md PLAN.md SPEC_PROCESS.md
git commit -m "docs: validate specification with cold-start audit"
```

---

### Task 1: Project skeleton, configuration, and domain contracts

**Status:** Complete — commits `344c561`, `ec0a5fb`; 16 tests passing; task review clean.

**Files:**
- Create: `pyproject.toml`, `Makefile`
- Modify: `.gitignore` (created before Task 1 by setup commit `308434d`)
- Create: `src/guarded_agent/__init__.py`, `src/guarded_agent/domain.py`, `src/guarded_agent/config.py`
- Create: `tests/test_config.py`, `tests/test_domain.py`

**Interfaces:**
- Consumes: none
- Produces: strict `Action` envelope and `ToolName`; `ToolResult`, `Feedback`, `GovernanceOutcome`, `GovernanceDecision`, `TaskStatus`, `Settings`, `ConfigError`; and `load_settings(workspace: Path) -> Settings`. Task 5 completes `Action.arguments` as strict per-tool discriminated models.

- [x] **Step 1: Write failing contract tests**

```python
def test_action_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Action.model_validate({"tool": "read_file", "arguments": {}, "surprise": 1})

def test_repository_config_cannot_disable_hard_boundaries(tmp_path: Path) -> None:
    (tmp_path / "guarded-agent.toml").write_text('[governance]\nallow_workspace_escape=true\n')
    with pytest.raises(ConfigError, match="invalid configuration:"):
        load_settings(tmp_path)
```

- [x] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_domain.py tests/test_config.py -q`

Expected: collection/import failure because `guarded_agent.domain` and `guarded_agent.config` do not exist.

- [x] **Step 3: Implement minimal typed contracts and config loader**

Use strict Pydantic models (`extra="forbid"`) and the full SPEC §7.1 DTO field contracts; define `GovernanceOutcome` at `guarded_agent.domain.GovernanceOutcome`. Parse only the SPEC §4.6 TOML schema through `tomllib`: unknown keys and every `[governance]` key fail with `ConfigError("invalid configuration: ...")`; validate all hard maxima. Resolve the workspace with `Path.resolve(strict=True)`, require a directory, and use defaults when the config file is absent.

- [x] **Step 4: Verify GREEN and static quality**

Run: `pytest tests/test_domain.py tests/test_config.py -q && ruff check src tests && mypy src`

Expected: all commands exit 0.

- [x] **Step 5: Commit**

```bash
git add pyproject.toml Makefile .gitignore src tests
git commit -m "feat: define harness domain contracts"
```

---

### Task 2: SQLite task, audit, and memory stores

**Status:** Complete — commits `1bab46b`, `9306644`, `5b88ca8`, `b81574e`; 39 tests passing; task review approved with three deferred minors.

**Files:**
- Create: `src/guarded_agent/storage.py`, `src/guarded_agent/memory.py`
- Create: `tests/test_storage.py`, `tests/test_memory.py`

**Interfaces:**
- Consumes: domain models from Task 1
- Produces: `Database.open(path)`, `TaskStore`, `AuditStore.append()`, `MemoryStore.add()` / `search(workspace_id, query, limit=10)`, and an approval repository with `create_pending(...)`, `approve(id, now)`, `reject(id, now)`, and `consume_if_authorized(id, expected_digest, now) -> bool`

- [x] **Step 1: Write failing persistence tests**

```python
def test_approval_can_be_consumed_only_once(db: Database) -> None:
    approval = db.approvals.create_pending(task_id="t1", action_digest="abc", policy_version="1.0", summary="delete old.py")
    db.approvals.approve(approval.id, now)
    assert db.approvals.consume_if_authorized(approval.id, "abc", now) is True
    assert db.approvals.consume_if_authorized(approval.id, "abc", now) is False

def test_memory_search_is_workspace_scoped(memory: MemoryStore) -> None:
    memory.add("w1", "convention", "Use Ruff", "user", "confirmed")
    memory.add("w2", "convention", "Use Black", "user", "confirmed")
    assert [m.content for m in memory.search("w1", "Ruff")] == ["Use Ruff"]
```

- [x] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_storage.py tests/test_memory.py -q`

Expected: missing store imports.

- [x] **Step 3: Implement schema and repositories**

Enable SQLite foreign keys, create normalized tables from SPEC §7, wrap status plus audit writes in transactions, enforce unique turn number and the specified approval state machine. Implement `consume_if_authorized` as one conditional update or one transaction that checks approval, expiry, digest and prior consumption. Use deterministic token matching plus recency for memory search. Do not store model guesses as confirmed memory.

- [x] **Step 4: Verify GREEN**

Run: `pytest tests/test_storage.py tests/test_memory.py -q`

Expected: all pass with a temporary on-disk SQLite database.

- [x] **Step 5: Commit**

```bash
git add src/guarded_agent/storage.py src/guarded_agent/memory.py tests/test_storage.py tests/test_memory.py
git commit -m "feat: persist tasks audit events and memory"
```

---

### Task 3: Encrypted credential vault and redaction

**Status:** Complete — commits `6eaec9b`, `82e943b`, `4e1c9ef`; 75 tests passing; security review approved.

**Files:**
- Create: `src/guarded_agent/credentials.py`, `src/guarded_agent/redaction.py`
- Create: `tests/test_credentials.py`, `tests/test_redaction.py`

**Interfaces:**
- Consumes: filesystem paths only
- Produces: `CredentialVault.set/get/status/clear`, `CredentialStatus`, and `Redactor.redact(value)`

- [x] **Step 1: Write failing security tests**

```python
def test_vault_never_writes_plaintext(tmp_path: Path) -> None:
    vault = CredentialVault(tmp_path / "vault.bin")
    vault.set("openai-compatible", "sk-secret-value", "master-pass")
    assert b"sk-secret-value" not in (tmp_path / "vault.bin").read_bytes()
    assert vault.get("master-pass").api_key == "sk-secret-value"

def test_redactor_removes_registered_secret() -> None:
    redactor = Redactor(["sk-secret-value"])
    assert "sk-secret-value" not in redactor.redact("failed with sk-secret-value")
```

- [x] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_credentials.py tests/test_redaction.py -q`

Expected: missing module imports.

- [x] **Step 3: Implement minimal secure vault**

Use random salt, Scrypt, and AES-GCM (or Fernet backed by authenticated encryption); encode a versioned envelope; atomically replace the vault with mode `0600`; return generic unlock errors; keep secrets out of `repr`, logs, status, and audit payloads.

- [x] **Step 4: Verify GREEN plus wrong-password behavior**

Run: `pytest tests/test_credentials.py tests/test_redaction.py -q`

Expected: plaintext absence, successful round trip, wrong password rejection, status masking, and clear behavior all pass.

- [x] **Step 5: Commit**

```bash
git add src/guarded_agent/credentials.py src/guarded_agent/redaction.py tests/test_credentials.py tests/test_redaction.py
git commit -m "feat: add encrypted credential vault"
```

---

### Task 4: Governance engine and approval binding

**Status:** Complete — commits `9727080`, `dbf9ad7`, `ba5ce93`, `673b7f1`; 225 tests passing; deep governance review approved.

**Files:**
- Create: `src/guarded_agent/governance.py`, `src/guarded_agent/paths.py`
- Create: `tests/test_governance.py`, `tests/test_paths.py`

**Interfaces:**
- Consumes: `Action`, `Settings`, canonical workspace, approval repository
- Produces: `GovernanceEngine.evaluate(action, context) -> GovernanceDecision`, `canonicalize_inside(workspace, candidate) -> Path`, `action_digest(...) -> str`, `create_pending_approval(...)`, and resume authorization through the Task 2 atomic repository API

- [x] **Step 1: Write failing path and command policy tests**

```python
def test_symlink_escape_is_denied(tmp_path: Path) -> None:
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("secret")
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(PolicyDenied, match="workspace"):
        canonicalize_inside(tmp_path, Path("link"))

@pytest.mark.parametrize("command", [["sudo", "id"], ["git", "reset", "--hard"], ["shutdown", "-h", "now"]])
def test_hard_denials_have_no_approval(command: list[str], engine: GovernanceEngine) -> None:
    decision = engine.evaluate(run_command(command))
    assert decision.outcome is GovernanceOutcome.DENY
```

- [x] **Step 2: Run targeted tests and verify RED**

Run: `pytest tests/test_paths.py tests/test_governance.py -q`

Expected: missing governance implementation.

- [x] **Step 3: Implement policy pipeline**

Implement the exact SPEC §3.3 path, sensitive-name, tool matrix, command argv ordering and `Policy(version="1.0")`; do not use semantic guesses such as “safe test command”. `canonicalize_inside` accepts only validated relative POSIX paths, resolves existing targets or the nearest existing parent, and permits internal symlinks only when their final realpath remains inside. Hash exactly the specified canonical JSON using SHA-256/UTF-8. Import `GovernanceOutcome` from `guarded_agent.domain`.

- [x] **Step 4: Add failing approval tampering test**

```python
def test_approval_does_not_authorize_changed_arguments(engine: GovernanceEngine) -> None:
    original = delete_action("old.py")
    approval = engine.create_pending_approval("t1", original)
    engine.approvals.approve(approval.id, now)
    assert engine.authorize_persisted("t1", delete_action("other.py"), approval.id, now) is False
```

Run: `pytest tests/test_governance.py::test_approval_does_not_authorize_changed_arguments -q`

Expected: FAIL until approval verification exists.

- [x] **Step 5: Implement pending/approved/single-use expiry verification and verify GREEN**

Run: `pytest tests/test_paths.py tests/test_governance.py -q`

Expected: traversal, internal/external symlink, sensitive paths, exact command tiers, configured-validator bypass prevention, expiry, tampering, resume mismatch audit/feedback and replay tests all pass.

- [x] **Step 6: Commit**

```bash
git add src/guarded_agent/governance.py src/guarded_agent/paths.py tests/test_governance.py tests/test_paths.py
git commit -m "feat: enforce workspace and approval governance"
```

---

### Task 5: Tool registry and deterministic feedback

**Status:** Complete — commits `ac0a4e0`, `4cc6140`, `95bc46e`, `2d1f77c`, `e56743d`; 290 tests passing; review approved.

**Files:**
- Create: `src/guarded_agent/tools.py`, `src/guarded_agent/subprocesses.py`, `src/guarded_agent/feedback.py`
- Create: `tests/test_tools.py`, `tests/test_feedback.py`

**Interfaces:**
- Consumes: Task 5 strict per-tool `Action`, canonical workspace, `Settings`, `Redactor`
- Produces: `ToolRegistry.execute(action) -> ToolResult`, `CommandRunner.run(argv, cwd, timeout)`, and `FeedbackEngine.verify(commands) -> Feedback`

- [x] **Step 1: Write failing real-behavior tests**

```python
def test_command_runner_does_not_expand_shell_syntax(runner: CommandRunner, tmp_path: Path) -> None:
    result = runner.run(["printf", "%s", "$(touch owned)"], tmp_path, 2)
    assert not (tmp_path / "owned").exists()
    assert "$(touch owned)" in result.stdout

def test_feedback_classifies_test_failure(tmp_path: Path, feedback: FeedbackEngine) -> None:
    result = feedback.verify([[sys.executable, "-c", "import sys; print('assert x'); sys.exit(1)"]], tmp_path)
    assert result.kind is FeedbackKind.TEST_FAILURE
```

- [x] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_tools.py tests/test_feedback.py -q`

Expected: missing runner/registry/feedback imports.

- [x] **Step 3: Implement tools and validation**

Complete the strict discriminated argument models promised by SPEC §3.1 before dispatch. Implement bounded file reads/searches, atomic writes, governed delete/move, Git status/diff, and command execution with `subprocess.Popen` argument arrays, process-group timeout termination, environment allowlist, 64 KiB stream limits and redaction. `run_validator` can execute only an exact startup-loaded `Settings.validation_commands` argv, never an LLM substitute. Classify zero/non-zero/timeout/start failure distinctly.

- [x] **Step 4: Verify GREEN**

Run: `pytest tests/test_tools.py tests/test_feedback.py -q`

Expected: execution, timeout, truncation, redaction, atomic write and classification tests pass.

- [x] **Step 5: Commit**

```bash
git add src/guarded_agent/tools.py src/guarded_agent/subprocesses.py src/guarded_agent/feedback.py tests/test_tools.py tests/test_feedback.py
git commit -m "feat: execute governed tools and feedback checks"
```

---

### Task 6: Mock and OpenAI-compatible providers

**Status:** Complete — commit `a70b2a6`; 295 tests passing; review approved.

**Files:**
- Create: `src/guarded_agent/providers/base.py`, `src/guarded_agent/providers/mock.py`, `src/guarded_agent/providers/openai_compatible.py`
- Create: `tests/providers/test_mock.py`, `tests/providers/test_openai_compatible.py`

**Interfaces:**
- Consumes: list of structured context messages and strict action schema
- Produces: `LLMProvider.next_action(messages) -> Action`

- [x] **Step 1: Write failing provider tests**

```python
def test_mock_provider_can_branch_on_feedback() -> None:
    provider = ScriptedMockProvider(on_feedback={"TEST_FAILURE": write_action("fixed.py", "ok")})
    action = provider.next_action([{"role": "tool", "feedback_kind": "TEST_FAILURE"}])
    assert action.arguments["path"] == "fixed.py"

def test_http_provider_rejects_non_action_json(httpx_mock) -> None:
    httpx_mock.add_response(json={"choices": [{"message": {"content": "hello"}}]})
    with pytest.raises(ProviderResponseError):
        provider(httpx_mock).next_action([])
```

- [x] **Step 2: Run tests and verify RED**

Run: `pytest tests/providers -q`

Expected: provider modules absent.

- [x] **Step 3: Implement the narrow provider abstraction**

Mock provider records received messages and returns scripted actions. HTTP provider performs one bounded HTTP request, parses one action, has explicit connect/read timeout and bounded retry only before a valid action exists. Never implement a provider-owned loop or tool execution.

- [x] **Step 4: Verify GREEN**

Run: `pytest tests/providers -q`

Expected: branching, request shape, timeout, HTTP failure and invalid action tests pass without network.

- [x] **Step 5: Commit**

```bash
git add src/guarded_agent/providers tests/providers
git commit -m "feat: add injectable llm providers"
```

---

### Task 7: Agent loop, context builder, and completion gate

**Status:** Complete — commits `10019f3`, `e47494d`; 305 tests passing; review approved.

**Files:**
- Create: `src/guarded_agent/context.py`, `src/guarded_agent/agent.py`, `src/guarded_agent/service.py`
- Create: `tests/test_agent_loop.py`, `tests/test_service.py`

**Interfaces:**
- Consumes: provider, stores, governance, tools, feedback, memory, settings
- Produces: `AgentLoop.step(task_id) -> TaskStatus`, `ApplicationService.create/run/resume/cancel`

- [x] **Step 1: Write failing end-to-end loop test**

```python
def test_failed_validation_changes_next_mock_action(harness, sample_workspace) -> None:
    provider = FeedbackAwareMock(first=write_bug(), after_failure=write_fix(), final=complete_action())
    task = harness.create_task(sample_workspace, "fix add", [["pytest", "-q"]], provider)
    harness.run(task.id)
    assert provider.received_feedback("TEST_FAILURE")
    assert harness.tasks.get(task.id).status is TaskStatus.COMPLETED
```

- [x] **Step 2: Run test and verify RED**

Run: `pytest tests/test_agent_loop.py::test_failed_validation_changes_next_mock_action -q`

Expected: missing `AgentLoop`/service.

- [x] **Step 3: Implement one-step state machine and context bounds**

Load goal, at most 8 recent turn summaries and 10 related memories; validate action; evaluate governance; pause, deny or execute; verify after mutation; persist turn plus audit; enforce configured limits within the hard maxima. A completion action always invokes selected startup-loaded acceptance commands.

- [x] **Step 4: Add failing pause/resume and false-completion tests**

```python
def test_approval_pauses_without_executing(harness) -> None:
    task = harness.task_with_action(delete_action("old.py"))
    assert harness.step(task.id) is TaskStatus.WAITING_APPROVAL
    assert (harness.workspace / "old.py").exists()

def test_completion_is_rejected_when_acceptance_fails(harness) -> None:
    task = harness.task_with_action(complete_action(), acceptance=[["false"]])
    assert harness.step(task.id) is not TaskStatus.COMPLETED
```

- [x] **Step 5: Implement persisted-action approval resume, cancel, and stop conditions; verify GREEN**

Run: `pytest tests/test_agent_loop.py tests/test_service.py -q`

Expected: feedback correction, pause/resume from persisted normalized action, denial, expiry/replay, mismatch audit plus next-turn feedback, completion gate, cancellation and all limits pass.

- [x] **Step 6: Commit**

```bash
git add src/guarded_agent/context.py src/guarded_agent/agent.py src/guarded_agent/service.py tests/test_agent_loop.py tests/test_service.py
git commit -m "feat: run the governed agent feedback loop"
```

---

### Task 8: CLI and deterministic mechanism demo

**Status:** Complete — commit `f09d40d`; 309 tests and demo passing; review approved.

**Files:**
- Create: `src/guarded_agent/cli.py`, `src/guarded_agent/demo.py`, `src/guarded_agent/__main__.py`
- Create: `tests/test_cli.py`, `tests/test_demo.py`

**Interfaces:**
- Consumes: `ApplicationService`, providers, credential vault
- Produces: `guarded-agent run|web|demo|credential|memory|version`

- [x] **Step 1: Write failing CLI and demo tests**

```python
def test_demo_runs_three_offline_scenarios(cli_runner) -> None:
    result = cli_runner.invoke(app, ["demo"])
    assert result.exit_code == 0
    assert "dangerous action blocked" in result.stdout
    assert "feedback correction passed" in result.stdout
    assert "approval tampering blocked" in result.stdout
```

- [x] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_cli.py tests/test_demo.py -q`

Expected: CLI module missing.

- [x] **Step 3: Implement commands and isolated demo fixtures**

Use Typer, hidden terminal input for secrets, explicit provider selection, non-zero exits for invalid workspace/config/verification. Demo creates a temporary repository and uses scripted Mock LLM only; it must not alter the caller's working tree.

- [x] **Step 4: Verify GREEN**

Run: `pytest tests/test_cli.py tests/test_demo.py -q && python -m guarded_agent demo`

Expected: tests and three scenarios pass without network/key.

- [x] **Step 5: Commit**

```bash
git add src/guarded_agent/cli.py src/guarded_agent/demo.py src/guarded_agent/__main__.py tests/test_cli.py tests/test_demo.py
git commit -m "feat: expose cli and offline mechanism demo"
```

---

### Task 9: Local WebUI

**Status:** Complete — commits `2fa8eed`, `0aa75e4`, `3266620`, `be546cf`; 311 tests passing, 8 ASGI tests documented for Python 3.12; review approved.

**Files:**
- Create: `src/guarded_agent/web.py`
- Create: `src/guarded_agent/templates/base.html`, `tasks.html`, `task_detail.html`, `approvals.html`, `memories.html`, `settings.html`
- Create: `src/guarded_agent/static/app.css`, `app.js`
- Create: `tests/test_web.py`

**Interfaces:**
- Consumes: `ApplicationService`, active workspace fixed at server startup
- Produces: local HTML routes and JSON status polling endpoint

- [x] **Step 1: Write failing route/security tests**

```python
@pytest.fixture
def configured_client(tmp_path: Path):
    (tmp_path / "guarded-agent.toml").write_text(
        '[validation]\ncommands = [["pytest", "-q"]]\n'
    )
    return make_web_client(tmp_path)

def test_web_can_create_mock_task_with_configured_validator(configured_client) -> None:
    response = configured_client.post("/tasks", data={
        "goal": "fix it", "validation_id": "validator-0", "_csrf": configured_client.csrf_token
    })
    assert response.status_code == 303
    assert "Task timeline" in configured_client.get(response.headers["location"]).text

def test_web_rejects_arbitrary_validation_command(configured_client) -> None:
    response = configured_client.post("/tasks", data={
        "goal": "fix it", "validation_id": "pytest -q", "_csrf": configured_client.csrf_token
    })
    assert response.status_code == 422

def test_settings_page_never_accepts_master_password(client) -> None:
    response = client.post("/settings", data={"master_password": "secret"})
    assert response.status_code == 422
```

- [x] **Step 2: Run test and verify RED**

Run: `pytest tests/test_web.py -q`

Expected: web app missing.

- [x] **Step 3: Implement server-rendered pages and polling**

Expose task list/create/detail/cancel, approval approve/reject, memory list/add/delete, settings/status, and `/api/tasks/{id}/status`. At startup load project validation commands and render them as the only selectable acceptance choices; reject arbitrary acceptance text. Validate CSRF token for state changes, escape output, fix workspace at startup, and redact all displayed payloads. The CLI must reject any host other than `127.0.0.1`.

- [x] **Step 4: Verify GREEN and accessibility basics**

Run: `pytest tests/test_web.py -q`

Expected: routes, CSRF, local-bind validation, approval flow, cancellation, no secret fields, textual status labels and escaped output pass.

- [x] **Step 5: Commit**

```bash
git add src/guarded_agent/web.py src/guarded_agent/templates src/guarded_agent/static tests/test_web.py
git commit -m "feat: add local task control web ui"
```

---

### Task 10: Packaging and hosted CI

**Status:** Complete — commit `154ea5c`; 316 passed/8 documented Python 3.14 ASGI skips; Ruff, mypy, binary `version` and `demo` passing locally.

**Files:**
- Create: `guarded-agent.spec`, `scripts/build_binary.sh`, `tests/test_packaging.py`
- Create: `.gitlab-ci.yml`（课程原始要求）；后续新增 `.github/workflows/ci.yml` 作为实际托管 CI
- Modify: `pyproject.toml`, `Makefile`

**Interfaces:**
- Consumes: complete CLI package and embedded templates/static files
- Produces: `dist/guarded-agent` Linux x86_64 executable and CI artifact

- [x] **Step 1: Write failing packaging metadata test**

```python
def test_gitlab_has_required_unit_test_job() -> None:
    config = yaml.safe_load(Path(".gitlab-ci.yml").read_text())
    assert "unit-test" in config
    assert "make test" in " ".join(config["unit-test"]["script"])
```

- [x] **Step 2: Run test and verify RED**

Run: `pytest tests/test_packaging.py -q`

Expected: `.gitlab-ci.yml` absent.

- [x] **Step 3: Implement reproducible build and CI**

`make test` runs all offline tests; `make quality` runs Ruff and mypy; `make binary` invokes a checked-in PyInstaller spec that embeds templates/static. GitLab jobs are `unit-test` and `build-binary`; the latter uploads `dist/guarded-agent` and runs `version` plus `demo` before artifact publication.

- [x] **Step 4: Verify source CI contract and binary smoke tests**

Run: `pytest tests/test_packaging.py -q && make test && make quality && make binary && ./dist/guarded-agent version && ./dist/guarded-agent demo`

Expected: every command exits 0; demo reports all three scenarios.

- [x] **Step 5: Commit**

```bash
git add guarded-agent.spec scripts/build_binary.sh .gitlab-ci.yml pyproject.toml Makefile tests/test_packaging.py
git commit -m "build: package linux binary in gitlab ci"
```

---

### Task 11: User documentation, process evidence, and final verification

**Status:** Complete — commit `50d6235`; final two-stage review found no Critical issues; 316 passed/8 documented out-of-scope Python 3.14 ASGI skips, Ruff, mypy, binary smoke tests and diff check passing.

**Files:**
- Create: `README.md`, `AGENT_LOG.md`, `REFLECTION.md`, `THIRD_PARTY_LICENSES.md`
- Modify: `PLAN.md`, `SPEC_PROCESS.md`

**Interfaces:**
- Consumes: verified commands, actual commits, agent review reports and known limitations
- Produces: complete handoff documentation and truthful process evidence

- [x] **Step 1: Write factual project documentation**

README must include binary acquisition/build, executable permission, localhost WebUI, CLI examples, encrypted credential setup/status/update/clear, unsupported platform/signing, trusted-repository warning, no public URL, architecture, test command and third-party licenses. `REFLECTION.md` contains headings, word-count guidance, factual commit/test references and explicit markers for the student to write personal analysis; do not generate the personal reflection.

Manual documentation acceptance: a reviewer reads README for the listed topics, confirms examples match the implemented CLI, and records the result in `AGENT_LOG.md`. This is deliberately manual; no README-heading source-text test is created.

- [x] **Step 2: Complete process evidence**

Append chronological skill usage, prompts, red/green evidence, subagent output, commit hashes, human decisions and deviations to `AGENT_LOG.md`; update PLAN checkboxes with actual commit hashes; finish cold-start outcomes in `SPEC_PROCESS.md`. Never fabricate PR, CI, public registry or deployment evidence.

- [x] **Step 3: Request two-stage code review and fix findings**

Use `superpowers:requesting-code-review`: first check SPEC compliance, then code quality/security. Resolve every Critical issue and record the review and fixes in `AGENT_LOG.md`.

- [x] **Step 4: Run fresh final verification**

Run: `make test && make quality && make binary && ./dist/guarded-agent version && ./dist/guarded-agent demo && git diff --check`

Expected: all commands exit 0, all tests pass, all three demo scenarios pass, and no whitespace errors are reported.

- [x] **Step 5: Verify requirements line by line**

Compare every SPEC §10 acceptance criterion and course deliverable against repository evidence. Report external-owner actions separately: GitHub remote/public visibility, PR workflow, final hosted CI pass, downloadable artifact retention, and the student's 1500–2500 Chinese-character reflection.

- [x] **Step 6: Commit**

```bash
git add README.md AGENT_LOG.md REFLECTION.md THIRD_PARTY_LICENSES.md PLAN.md SPEC_PROCESS.md
git commit -m "docs: complete guarded agent delivery evidence"
```

### Task 12: Local conversational WebUI extension

**Status:** Complete and merged to `main` by PR #4 (`70f06c8`); design/plan commit `dd951e1`, implementation commits `ebdd89d`, `80c7eb3`, `673b6fa`, `d56e8a8`, `b8129be`, `668ac4e`.

- [x] Persist bounded `ConversationMessage` records and include recent transcript messages in provider context.
- [x] Add provider-injected WebUI service stepping and CSRF-protected `/api/chat/messages` GET/POST endpoints.
- [x] Add the Chinese transcript/composer UI, safe text-only rendering, DeepSeek model option, and local credential lifecycle documentation.
- [x] Verify `331 passed, 8 skipped`, Ruff, mypy, PyInstaller `version`, `demo`, and `git diff --check`.
- [x] Confirm the final GitHub Actions run is pass and merge PR #4 (`31477957962`, `70f06c8`).

## Execution discipline

Each implementation task uses an isolated worktree where the environment permits it, a fresh subagent, and two reviews: specification compliance first, then code quality. Because all agents share the current filesystem in this environment, do not run overlapping worktree edits against the same paths. Before every completion claim, run the task's full verification command and inspect its exit code and output.
