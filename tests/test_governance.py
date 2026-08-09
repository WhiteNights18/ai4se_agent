from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from guarded_agent.domain import Action, GovernanceOutcome, Settings, TaskStatus, ToolName
from guarded_agent.governance import GovernanceEngine, Policy, action_digest
from guarded_agent.paths import PolicyDenied
from guarded_agent.storage import ApprovalStatus, Database


def action(tool: ToolName, **arguments: object) -> Action:
    return Action.model_validate({"tool": tool, "arguments": arguments})


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[GovernanceEngine]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = Database.open(tmp_path / "governance.sqlite3")
    stored_workspace = database.tasks.create_workspace(str(workspace.resolve()), "workspace")
    database.tasks.create_task(
        task_id="t1",
        workspace_id=stored_workspace.id,
        goal="govern safely",
        acceptance_commands=[],
        limits={},
    )
    governance = GovernanceEngine(
        workspace=workspace,
        settings=Settings(validation_commands=[["pytest", "-q"]]),
        database=database,
        task_id="t1",
    )
    yield governance
    database.close()


@pytest.mark.parametrize(
    "argv",
    [
        ["sudo", "id"],
        ["doas", "id"],
        ["shutdown", "-h", "now"],
        ["git", "reset", "--hard"],
        ["/usr/bin/git", "reset", "--hard"],
        ["./git", "clean", "-fd"],
        ["git", "-C", "src", "reset", "--hard"],
        ["git", "clean", "-fd"],
        ["git", "push", "--force", "origin", "main"],
        ["git", "checkout", "--", "src"],
        ["git", "diff", "--output", "report.patch"],
        ["/usr/bin/git", "diff", "--output=report.patch"],
        ["git", "diff", "--output=report.patch"],
        ["rg", "--pre", "./filter", "TODO", "src"],
        ["rg", "--pre=./filter", "TODO", "src"],
        ["rg", "--pre-glob=*.py", "TODO", "src"],
        ["rg", "-f", "patterns.txt", "src"],
        ["rg", "-fpatterns.txt", "src"],
        ["rg", "--file", "patterns.txt", "src"],
        ["rg", "--file=patterns.txt", "src"],
        ["env", "git", "status"],
        ["bash", "-c", "git status"],
        ["sh", "-c", "rm -rf /"],
        ["fish", "-c", "rm -rf /"],
        ["csh", "-c", "rm -rf /"],
        ["tcsh", "-c", "rm -rf /"],
        ["ksh", "-c", "rm -rf /"],
        ["ash", "-c", "rm -rf /"],
        ["rm", "-rf", "/"],
    ],
)
def test_hard_denials_have_no_approval(argv: list[str], engine: GovernanceEngine) -> None:
    """Catch non-approvable commands falling through to the approval tier."""
    decision = engine.evaluate(action(ToolName.RUN_COMMAND, argv=argv))

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.rule_id == "hard_denied_command"
    assert decision.action_digest is None
    assert decision.approval_id is None


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (ToolName.READ_FILE, {"path": ".git/config"}),
        (ToolName.WRITE_FILE, {"path": ".env.local", "content": "x"}),
        (ToolName.DELETE_FILE, {"path": "keys/id_rsa"}),
        (
            ToolName.MOVE_FILE,
            {"source": "safe.txt", "destination": ".guarded-agent/credentials.db"},
        ),
    ],
)
def test_sensitive_names_are_hard_denied_for_every_file_operation(
    tool: ToolName, arguments: dict[str, object], engine: GovernanceEngine
) -> None:
    """Catch a mutating file tool bypassing the same credential fence used for reads."""
    decision = engine.evaluate(action(tool, **arguments))

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.rule_id == "sensitive_path"


def test_file_tool_matrix_distinguishes_reads_bounded_writes_and_destructive_changes(
    engine: GovernanceEngine,
) -> None:
    """Catch a tool being assigned a risk tier different from the compiled matrix."""
    assert engine.evaluate(action(ToolName.READ_FILE, path="README.md")).rule_id == "read_only"
    assert engine.evaluate(
        action(ToolName.WRITE_FILE, path="result.txt", content="ok")
    ).rule_id == "bounded_write"

    deletion = engine.evaluate(action(ToolName.DELETE_FILE, path="old.py"))
    movement = engine.evaluate(
        action(ToolName.MOVE_FILE, source="old.py", destination="archive/old.py")
    )
    assert deletion.outcome is GovernanceOutcome.REQUIRE_APPROVAL
    assert movement.outcome is GovernanceOutcome.REQUIRE_APPROVAL
    assert deletion.rule_id == movement.rule_id == "destructive_file_change"


def test_write_limit_is_measured_in_utf8_bytes_and_fails_closed(engine: GovernanceEngine) -> None:
    """Catch a character-count limit that admits content beyond the configured byte budget."""
    engine.settings = Settings(max_output_bytes=3, validation_commands=[["pytest", "-q"]])

    allowed = engine.evaluate(action(ToolName.WRITE_FILE, path="small.txt", content="abc"))
    denied = engine.evaluate(action(ToolName.WRITE_FILE, path="large.txt", content="你好"))

    assert allowed.outcome is GovernanceOutcome.ALLOW
    assert denied.outcome is GovernanceOutcome.DENY
    assert denied.rule_id == "resource_limit_exceeded"


def test_configured_validator_requires_an_exact_argv_match(engine: GovernanceEngine) -> None:
    """Catch an LLM adding arguments to a pre-authorized validator command."""
    exact = engine.evaluate(action(ToolName.RUN_VALIDATOR, argv=["pytest", "-q"]))
    changed = engine.evaluate(action(ToolName.RUN_VALIDATOR, argv=["pytest", "-q", "--pdb"]))

    assert exact.outcome is GovernanceOutcome.ALLOW
    assert exact.rule_id == "configured_validator"
    assert changed.outcome is GovernanceOutcome.DENY
    assert changed.rule_id == "validator_not_configured"


@pytest.mark.parametrize(
    ("argv", "rule_id"),
    [
        (["sudo", "id"], "hard_denied_command"),
        (["cat", ".env"], "sensitive_path"),
        (["pytest", "../outside"], "workspace_boundary"),
    ],
)
def test_configured_validator_cannot_override_compiled_boundaries(
    argv: list[str], rule_id: str, engine: GovernanceEngine
) -> None:
    """Catch repository validators bypassing hard, sensitive, or workspace denials."""
    governance = GovernanceEngine(
        workspace=engine.workspace,
        settings=Settings(validation_commands=[argv]),
        database=engine.database,
        task_id="t1",
    )

    decision = governance.evaluate(action(ToolName.RUN_VALIDATOR, argv=argv))

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.rule_id == rule_id


def test_validator_allowlist_is_an_immutable_startup_snapshot(engine: GovernanceEngine) -> None:
    """Catch later settings mutation adding a command that was not authorized at startup."""
    engine.settings.validation_commands.append(["python", "-m", "pytest"])

    decision = engine.evaluate(
        action(ToolName.RUN_VALIDATOR, argv=["python", "-m", "pytest"])
    )

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.rule_id == "validator_not_configured"


@pytest.mark.parametrize(
    "argv",
    [
        ["rg", "TODO", "first.py"],
        ["rg", "-n", "-i", "TODO", "first.py", "second.py"],
        ["rg", "TODO", "--line-number", "first.py"],
        ["rg", "--fixed-strings", "bash", "first.py"],
        ["git", "status"],
        ["git", "status", "--short", "--branch"],
        ["git", "status", "--porcelain=v2", "--", "src"],
        ["git", "diff", "--", "first.py"],
        ["git", "diff", "--stat", "--cached", "--", "first.py", "second.py"],
    ],
)
def test_exact_read_commands_are_allowed(argv: list[str], engine: GovernanceEngine) -> None:
    """Catch the narrow read-only argv allowlist being lost to the approval fallback."""
    (engine.workspace / "first.py").write_text("first", encoding="utf-8")
    (engine.workspace / "second.py").write_text("second", encoding="utf-8")
    decision = engine.evaluate(action(ToolName.RUN_COMMAND, argv=argv))

    assert decision.outcome is GovernanceOutcome.ALLOW
    assert decision.rule_id == "approved_read_command"


@pytest.mark.parametrize(
    "argv",
    [
        ["rg", "TODO", "../outside"],
        ["git", "diff", "--", "../outside"],
    ],
)
def test_read_command_path_operands_cannot_escape(argv: list[str], engine: GovernanceEngine) -> None:
    """Catch the command allowlist bypassing the workspace fence for path operands."""
    decision = engine.evaluate(action(ToolName.RUN_COMMAND, argv=argv))

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.rule_id == "workspace_boundary"


@pytest.mark.parametrize(
    "argv",
    [
        ["rg", "TODO", ";", "shutdown"],
        ["rg", "--json", "TODO", "src"],
        ["rg", "-g", "*.py", "TODO", "src"],
        ["git", "status", "--ignored"],
        ["git", "status", "-unsafe"],
        ["git", "diff", "--ext-diff", "--", "src"],
        ["git", "status", ">", "report"],
        ["pytest", "-q"],
        ["git", "commit", "-m", "message"],
    ],
)
def test_non_allowlisted_commands_require_approval(argv: list[str], engine: GovernanceEngine) -> None:
    """Catch shell syntax or unclassified commands gaining read-only authorization."""
    decision = engine.evaluate(action(ToolName.RUN_COMMAND, argv=argv))

    assert decision.outcome is GovernanceOutcome.REQUIRE_APPROVAL
    assert decision.rule_id == "command_requires_approval"


@pytest.mark.parametrize(
    "argv",
    [
        ["/usr/bin/rg", "TODO", "src"],
        ["./rg", "TODO", "src"],
        ["tools/rg", "TODO", "src"],
        ["/usr/bin/git", "status"],
        ["./git", "diff", "--", "src"],
        ["printf", "git", "reset", "--hard"],
    ],
)
def test_path_qualified_read_lookalikes_require_approval(
    argv: list[str], engine: GovernanceEngine
) -> None:
    """Catch basename matching that auto-allows an attacker-controlled executable."""
    decision = engine.evaluate(action(ToolName.RUN_COMMAND, argv=argv))

    assert decision.outcome is GovernanceOutcome.REQUIRE_APPROVAL
    assert decision.rule_id == "command_requires_approval"


@pytest.mark.parametrize(
    "argv",
    [
        ["rg", "TODO", "../outside"],
        ["rg", "TODO", ".env"],
        ["rg", "--files", ".env"],
        ["git", "status", "--", "../outside"],
        ["git", "diff", "--stat", "--", ".git/config"],
    ],
)
def test_safe_read_grammar_still_hard_denies_unsafe_pathspecs(
    argv: list[str], engine: GovernanceEngine
) -> None:
    """Catch a syntactically safe read command skipping path and sensitive-name fences."""
    decision = engine.evaluate(action(ToolName.RUN_COMMAND, argv=argv))

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.rule_id in {"workspace_boundary", "sensitive_path"}


def test_rg_auto_allow_requires_explicit_existing_regular_file_targets(
    engine: GovernanceEngine,
) -> None:
    """Catch recursive or semantic expansion entering the automatic read tier."""
    source = engine.workspace / "src"
    source.mkdir()
    (source / "app.py").write_text("print('ok')", encoding="utf-8")
    (source / "id_rsa").write_text("private", encoding="utf-8")

    commands = [
        ["rg", "TODO"],
        ["rg", "--files"],
        ["rg", "--files", "src/app.py"],
        ["rg", "TODO", "src"],
        ["rg", "TODO", "missing.py"],
        ["rg", "TODO", "*.py"],
    ]

    for argv in commands:
        decision = engine.evaluate(action(ToolName.RUN_COMMAND, argv=argv))
        assert decision.outcome is GovernanceOutcome.REQUIRE_APPROVAL, argv
        assert decision.rule_id == "command_requires_approval"


def test_git_diff_auto_allow_requires_literal_existing_regular_files(
    engine: GovernanceEngine,
) -> None:
    """Catch repo-wide, directory, missing, or magic pathspec expansion entering auto-read."""
    source = engine.workspace / "src"
    source.mkdir()
    (source / "app.py").write_text("print('ok')", encoding="utf-8")

    commands = [
        ["git", "diff"],
        ["git", "diff", "--stat"],
        ["git", "diff", "--", "src"],
        ["git", "diff", "--", "missing.py"],
        ["git", "diff", "--", "*.py"],
        ["git", "diff", "--", "src:file.py"],
        ["git", "diff", "--", ":(icase).ENV"],
    ]

    for argv in commands:
        decision = engine.evaluate(action(ToolName.RUN_COMMAND, argv=argv))
        assert decision.outcome is GovernanceOutcome.REQUIRE_APPROVAL, argv
        assert decision.rule_id == "command_requires_approval"


@pytest.mark.parametrize(
    "argv",
    [
        ["env", "sudo", "id"],
        ["bash", "-c", "rm -rf /"],
        ["sh", "-c", "rm -rf /"],
        ["zsh", "-c", "rm -rf /"],
        ["dash", "-c", "rm -rf /"],
        ["fish", "-c", "rm -rf /"],
        ["csh", "-c", "rm -rf /"],
        ["tcsh", "-c", "rm -rf /"],
        ["ksh", "-c", "rm -rf /"],
        ["ash", "-c", "rm -rf /"],
        ["rm", "-rf", "/"],
    ],
)
def test_configured_validator_cannot_preapprove_shell_or_environment_wrappers(
    argv: list[str], engine: GovernanceEngine
) -> None:
    """Catch exact validator matching that bypasses hard-denied wrapper executables."""
    governance = GovernanceEngine(
        workspace=engine.workspace,
        settings=Settings(validation_commands=[argv]),
        database=engine.database,
        task_id="t1",
    )

    decision = governance.evaluate(action(ToolName.RUN_VALIDATOR, argv=argv))

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.rule_id == "hard_denied_command"


def test_memory_persistence_requires_approval(engine: GovernanceEngine) -> None:
    """Catch model-authored memory becoming trusted without user confirmation."""
    decision = engine.evaluate(action(ToolName.SAVE_MEMORY, category="decision", content="Use SQLite"))

    assert decision.outcome is GovernanceOutcome.REQUIRE_APPROVAL
    assert decision.rule_id == "memory_persistence"


def test_compiled_policy_rejects_any_repository_selected_version() -> None:
    """Catch callers constructing a locally weakened or approval-compatible policy version."""
    with pytest.raises(ValueError, match="compiled policy"):
        Policy(version="repository-choice")


def test_action_digest_has_the_specified_canonical_utf8_bytes() -> None:
    """Catch digest drift in key order, whitespace, Unicode encoding, or policy binding."""
    digest = action_digest(
        task_id="τ",
        action=action(ToolName.MOVE_FILE, destination="new.py", source="old.py"),
        workspace=Path("/"),
        policy_version="1.0",
    )

    assert digest == "085b9df5890d61928299661e5189e7531e3c7802a412369c790699149884821e"


def test_unserializable_numeric_values_fail_policy_evaluation_closed(
    engine: GovernanceEngine,
) -> None:
    """Catch NaN entering a non-canonical digest and creating an ambiguous approval."""
    unsafe = Action.model_construct(tool=ToolName.SAVE_MEMORY, arguments={"content": float("nan")})

    decision = engine.evaluate(unsafe)

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.rule_id == "policy_evaluation_failed"
    assert decision.approval_id is None


def test_approval_does_not_authorize_changed_arguments(engine: GovernanceEngine) -> None:
    """Catch an approval digest authorizing a destructive action with different arguments."""
    original = action(ToolName.DELETE_FILE, path="old.py")
    approval = engine.create_pending_approval("t1", original)
    now = datetime.now(UTC)
    engine.approvals.approve(approval.id, now)

    assert (
        engine.authorize_persisted(
            "t1", action(ToolName.DELETE_FILE, path="other.py"), approval.id, now
        )
        is False
    )
    assert engine.approvals.get(approval.id).status is ApprovalStatus.APPROVED


def test_hard_denial_cannot_create_a_pending_approval(engine: GovernanceEngine) -> None:
    """Catch an approval API that bypasses the policy outcome and persists a hard denial."""
    with pytest.raises(PolicyDenied, match="eligible"):
        engine.create_pending_approval(
            "t1", action(ToolName.RUN_COMMAND, argv=["git", "reset", "--hard"])
        )

    count = engine.database.connection.execute("SELECT count(*) FROM approvals").fetchone()[0]
    assert count == 0


def test_approved_action_is_single_use(engine: GovernanceEngine) -> None:
    """Catch a persisted approval replaying the exact same destructive action."""
    deletion = action(ToolName.DELETE_FILE, path="old.py")
    approval = engine.create_pending_approval("t1", deletion)
    now = datetime.now(UTC)
    engine.approvals.approve(approval.id, now)

    assert engine.authorize_persisted("t1", deletion, approval.id, now) is True
    assert engine.authorize_persisted("t1", deletion, approval.id, now) is False
    assert engine.approvals.get(approval.id).status is ApprovalStatus.CONSUMED


def test_expired_approval_cannot_resume_and_records_mismatch(engine: GovernanceEngine) -> None:
    """Catch resume skipping expiry or failing to leave an auditable policy signal."""
    deletion = action(ToolName.DELETE_FILE, path="old.py")
    now = datetime.now(UTC)
    approval = engine.create_pending_approval(
        "t1", deletion, expires_at=now + timedelta(seconds=1)
    )
    engine.approvals.approve(approval.id, now)

    assert (
        engine.authorize_persisted("t1", deletion, approval.id, now + timedelta(seconds=2))
        is False
    )
    assert [event.event_type for event in engine.audit.list_for_task("t1")] == [
        "approval_mismatch"
    ]


def test_resume_re_evaluates_policy_and_returns_waiting_task_to_running(
    engine: GovernanceEngine,
) -> None:
    """Catch resume consuming by old digest without re-running current hard policy and feedback."""
    deletion = action(ToolName.DELETE_FILE, path="old.py")
    approval = engine.create_pending_approval("t1", deletion)
    now = datetime.now(UTC)
    engine.approvals.approve(approval.id, now)
    engine.database.tasks.transition_status(
        "t1", TaskStatus.RUNNING, event_type="task_started", payload={}
    )
    engine.database.tasks.transition_status(
        "t1", TaskStatus.WAITING_APPROVAL, event_type="approval_requested", payload={}
    )

    changed_to_hard_denial = action(ToolName.RUN_COMMAND, argv=["sudo", "id"])
    assert (
        engine.authorize_persisted("t1", changed_to_hard_denial, approval.id, now) is False
    )

    assert engine.database.tasks.get("t1").status is TaskStatus.RUNNING
    mismatch = engine.audit.list_for_task("t1")[-1]
    assert mismatch.event_type == "approval_mismatch"
    assert mismatch.redacted_payload["feedback"] == {
        "kind": "POLICY_VIOLATION",
        "message": "persisted approval no longer authorizes the action",
        "can_continue": True,
    }
    assert engine.approvals.get(approval.id).status is ApprovalStatus.APPROVED


def test_approval_is_bound_to_task_workspace_and_policy_version(
    engine: GovernanceEngine, tmp_path: Path
) -> None:
    """Catch digest-only authorization that ignores persisted binding columns or context."""
    deletion = action(ToolName.DELETE_FILE, path="old.py")
    now = datetime.now(UTC)
    approval = engine.create_pending_approval("t1", deletion)
    engine.approvals.approve(approval.id, now)

    assert engine.authorize_persisted("other-task", deletion, approval.id, now) is False

    changed_workspace = tmp_path / "different-workspace"
    changed_workspace.mkdir()
    with pytest.raises(ValueError, match="registered workspace"):
        GovernanceEngine(
            workspace=changed_workspace,
            settings=engine.settings,
            database=engine.database,
            task_id="t1",
        )

    engine.database.connection.execute(
        "UPDATE approvals SET policy_version = '0.9' WHERE id = ?", (approval.id,)
    )
    assert engine.authorize_persisted("t1", deletion, approval.id, now) is False


def test_cross_task_approval_mismatch_recovers_only_the_current_task(
    engine: GovernanceEngine,
) -> None:
    """Catch a foreign approval ID moving or auditing the approval owner's task."""
    workspace_id = engine.database.tasks.get("t1").workspace_id
    engine.database.tasks.create_task(
        task_id="t2",
        workspace_id=workspace_id,
        goal="second task",
        acceptance_commands=[],
        limits={},
    )
    second_engine = GovernanceEngine(
        workspace=engine.workspace,
        settings=engine.settings,
        database=engine.database,
        task_id="t2",
    )
    deletion = action(ToolName.DELETE_FILE, path="old.py")
    foreign_approval = second_engine.create_pending_approval("t2", deletion)
    now = datetime.now(UTC)
    second_engine.approvals.approve(foreign_approval.id, now)
    for task_id in ("t1", "t2"):
        engine.database.tasks.transition_status(
            task_id, TaskStatus.RUNNING, event_type="task_started", payload={}
        )
        engine.database.tasks.transition_status(
            task_id,
            TaskStatus.WAITING_APPROVAL,
            event_type="approval_requested",
            payload={},
        )

    assert (
        engine.authorize_persisted("t1", deletion, foreign_approval.id, now) is False
    )

    assert engine.database.tasks.get("t1").status is TaskStatus.RUNNING
    assert engine.database.tasks.get("t2").status is TaskStatus.WAITING_APPROVAL
    assert engine.audit.list_for_task("t1")[-1].event_type == "approval_mismatch"
    assert engine.audit.list_for_task("t2")[-1].event_type == "approval_requested"
    assert engine.approvals.get(foreign_approval.id).status is ApprovalStatus.APPROVED


@pytest.mark.parametrize("tool", [ToolName.READ_FILE, ToolName.RUN_COMMAND])
def test_internal_symlink_cannot_alias_a_sensitive_target(
    tool: ToolName, engine: GovernanceEngine
) -> None:
    """Catch sensitivity checks that inspect only an in-workspace symlink's submitted name."""
    sensitive = engine.workspace / ".env"
    sensitive.write_text("SECRET=x", encoding="utf-8")
    (engine.workspace / "alias").symlink_to(sensitive)
    aliased_action = (
        action(ToolName.READ_FILE, path="alias")
        if tool is ToolName.READ_FILE
        else action(ToolName.RUN_COMMAND, argv=["rg", "SECRET", "alias"])
    )

    decision = engine.evaluate(aliased_action)

    assert decision.outcome is GovernanceOutcome.DENY
    assert decision.rule_id == "sensitive_path"


def test_canonical_target_snapshot_does_not_follow_a_later_symlink_swap(
    engine: GovernanceEngine,
) -> None:
    """Catch governance handing the executor only the mutable submitted symlink spelling."""
    first = engine.workspace / "first.txt"
    second = engine.workspace / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    alias = engine.workspace / "alias"
    alias.symlink_to(first)

    canonical = engine.canonical_targets(action(ToolName.READ_FILE, path="alias"))
    alias.unlink()
    alias.symlink_to(second)

    assert canonical == (first.resolve(strict=True),)
    assert canonical[0].read_text(encoding="utf-8") == "first"
