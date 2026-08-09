"""Strict, JSON-serializable contracts shared between application modules."""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, StringConstraints


class ToolName(str, Enum):
    """The complete fixed set of v1 tool identifiers."""

    LIST_DIRECTORY = "list_directory"
    READ_FILE = "read_file"
    SEARCH_TEXT = "search_text"
    WRITE_FILE = "write_file"
    DELETE_FILE = "delete_file"
    MOVE_FILE = "move_file"
    GIT_STATUS = "git_status"
    GIT_DIFF = "git_diff"
    RUN_COMMAND = "run_command"
    RUN_VALIDATOR = "run_validator"
    SAVE_MEMORY = "save_memory"
    RETRIEVE_MEMORY = "retrieve_memory"
    COMPLETE = "complete"
    CANNOT_CONTINUE = "cannot_continue"


class FeedbackKind(str, Enum):
    PASS = "PASS"
    TEST_FAILURE = "TEST_FAILURE"
    TOOL_FAILURE = "TOOL_FAILURE"
    TIMEOUT = "TIMEOUT"
    INVALID_ACTION = "INVALID_ACTION"
    POLICY_VIOLATION = "POLICY_VIOLATION"


class GovernanceOutcome(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StrictDTO(BaseModel):
    """Base class that prevents DTO consumers from smuggling undocumented data."""

    model_config = ConfigDict(extra="forbid")


class Action(StrictDTO):
    tool: ToolName
    arguments: dict[str, JsonValue]


class ToolResult(StrictDTO):
    tool: ToolName
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int
    changes: list[str]


class Feedback(StrictDTO):
    kind: FeedbackKind
    message: str
    command_result: ToolResult | None
    can_continue: bool


class GovernanceDecision(StrictDTO):
    outcome: GovernanceOutcome
    rule_id: str
    reason: str
    action_digest: str | None
    approval_id: str | None


NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
NonEmptyArgv = Annotated[list[NonEmptyString], Field(min_length=1)]


class Settings(StrictDTO):
    max_turns: StrictInt = Field(default=20, ge=1, le=20)
    max_consecutive_failures: StrictInt = Field(default=4, ge=1, le=4)
    total_timeout_seconds: StrictInt = Field(default=1800, ge=1, le=1800)
    command_timeout_seconds: StrictInt = Field(default=120, ge=1, le=120)
    max_output_bytes: StrictInt = Field(default=65536, ge=1, le=65536)
    validation_commands: list[NonEmptyArgv] = Field(default_factory=list)
