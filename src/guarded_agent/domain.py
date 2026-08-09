"""Strict, JSON-serializable contracts shared between application modules."""

from enum import Enum
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    StrictInt,
    StrictStr,
    StringConstraints,
    TypeAdapter,
)


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


PathArgument = Annotated[StrictStr, Field(min_length=1, max_length=4096)]
TextArgument = Annotated[StrictStr, Field(min_length=1, max_length=65_536)]
CommandArgument = Annotated[StrictStr, Field(min_length=1, max_length=4096)]
CommandArgv = Annotated[list[CommandArgument], Field(min_length=1, max_length=128)]


class ListDirectoryArguments(StrictDTO):
    path: PathArgument


class ReadFileArguments(StrictDTO):
    path: PathArgument


class SearchTextArguments(StrictDTO):
    path: PathArgument
    query: TextArgument
    max_results: StrictInt = Field(default=100, ge=1, le=1000)


class WriteFileArguments(StrictDTO):
    path: PathArgument
    content: StrictStr


class DeleteFileArguments(StrictDTO):
    path: PathArgument


class MoveFileArguments(StrictDTO):
    source: PathArgument
    destination: PathArgument


class GitStatusArguments(StrictDTO):
    pass


class GitDiffArguments(StrictDTO):
    pass


class RunCommandArguments(StrictDTO):
    argv: CommandArgv


class RunValidatorArguments(StrictDTO):
    argv: CommandArgv


class SaveMemoryArguments(StrictDTO):
    category: TextArgument
    content: TextArgument


class RetrieveMemoryArguments(StrictDTO):
    query: TextArgument
    limit: StrictInt = Field(default=10, ge=1, le=100)


class CompleteArguments(StrictDTO):
    summary: Annotated[StrictStr, Field(max_length=65_536)] = ""


class CannotContinueArguments(StrictDTO):
    reason: TextArgument


class ListDirectoryAction(StrictDTO):
    tool: Literal[ToolName.LIST_DIRECTORY]
    arguments: ListDirectoryArguments


class ReadFileAction(StrictDTO):
    tool: Literal[ToolName.READ_FILE]
    arguments: ReadFileArguments


class SearchTextAction(StrictDTO):
    tool: Literal[ToolName.SEARCH_TEXT]
    arguments: SearchTextArguments


class WriteFileAction(StrictDTO):
    tool: Literal[ToolName.WRITE_FILE]
    arguments: WriteFileArguments


class DeleteFileAction(StrictDTO):
    tool: Literal[ToolName.DELETE_FILE]
    arguments: DeleteFileArguments


class MoveFileAction(StrictDTO):
    tool: Literal[ToolName.MOVE_FILE]
    arguments: MoveFileArguments


class GitStatusAction(StrictDTO):
    tool: Literal[ToolName.GIT_STATUS]
    arguments: GitStatusArguments


class GitDiffAction(StrictDTO):
    tool: Literal[ToolName.GIT_DIFF]
    arguments: GitDiffArguments


class RunCommandAction(StrictDTO):
    tool: Literal[ToolName.RUN_COMMAND]
    arguments: RunCommandArguments


class RunValidatorAction(StrictDTO):
    tool: Literal[ToolName.RUN_VALIDATOR]
    arguments: RunValidatorArguments


class SaveMemoryAction(StrictDTO):
    tool: Literal[ToolName.SAVE_MEMORY]
    arguments: SaveMemoryArguments


class RetrieveMemoryAction(StrictDTO):
    tool: Literal[ToolName.RETRIEVE_MEMORY]
    arguments: RetrieveMemoryArguments


class CompleteAction(StrictDTO):
    tool: Literal[ToolName.COMPLETE]
    arguments: CompleteArguments


class CannotContinueAction(StrictDTO):
    tool: Literal[ToolName.CANNOT_CONTINUE]
    arguments: CannotContinueArguments


type ToolAction = Annotated[
    ListDirectoryAction
    | ReadFileAction
    | SearchTextAction
    | WriteFileAction
    | DeleteFileAction
    | MoveFileAction
    | GitStatusAction
    | GitDiffAction
    | RunCommandAction
    | RunValidatorAction
    | SaveMemoryAction
    | RetrieveMemoryAction
    | CompleteAction
    | CannotContinueAction,
    Field(discriminator="tool"),
]

_TOOL_ACTION_ADAPTER: TypeAdapter[ToolAction] = TypeAdapter(ToolAction)


def parse_tool_action(value: object) -> ToolAction:
    """Parse the provider-facing strict discriminated action contract."""
    return _TOOL_ACTION_ADAPTER.validate_python(value)


class Action(RootModel[ToolAction]):
    """Compatibility wrapper around the public strict ToolAction contract."""

    @property
    def tool(self) -> ToolName:
        return self.root.tool

    @property
    def arguments(self) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            self.root.arguments.model_dump(mode="python", warnings=False),
        )


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
