"""Typer adapter for the locally operated guarded-agent service."""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from guarded_agent.config import ConfigError, load_settings
from guarded_agent.credentials import CredentialError, CredentialUnlockError, CredentialVault
from guarded_agent.demo import run_demo
from guarded_agent.domain import TaskStatus
from guarded_agent.memory import MemorySource, MemoryTrust
from guarded_agent.providers.base import LLMProvider
from guarded_agent.providers.mock import ScriptedMockProvider
from guarded_agent.providers.openai_compatible import OpenAICompatibleProvider
from guarded_agent.service import ApplicationService
from guarded_agent.storage import Database

app = typer.Typer(no_args_is_help=True, add_completion=False)
credential_app = typer.Typer(no_args_is_help=True)
memory_app = typer.Typer(no_args_is_help=True)
app.add_typer(credential_app, name="credential")
app.add_typer(memory_app, name="memory")


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=1)


def _vault(path: Path | None) -> CredentialVault:
    return CredentialVault(path or (Path.cwd() / ".guarded-agent" / "credentials.vault"))


def _state_database(workspace: Path) -> Database:
    return Database.open(workspace / ".guarded-agent" / "state.sqlite3")


def _workspace_id(database: Database, workspace: Path) -> str:
    canonical = workspace.resolve(strict=True)
    registered = database.tasks.get_workspace(str(canonical))
    if registered is None:
        registered = database.tasks.create_workspace(str(canonical), canonical.name)
    return registered.id


def _selected_acceptance(settings_commands: list[list[str]], selections: list[str]) -> list[list[str]]:
    if not settings_commands:
        raise ValueError("no validation commands are configured in guarded-agent.toml")
    if not selections:
        return settings_commands
    selected: list[list[str]] = []
    for raw in selections:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("--accept must be a JSON argv array") from error
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ValueError("--accept must be a JSON argv array")
        command = list(value)
        if command not in settings_commands:
            raise ValueError("acceptance command is not configured in guarded-agent.toml")
        selected.append(command)
    return selected


@app.command("version")
def version_command() -> None:
    """Print the installed guarded-agent version."""
    typer.echo(f"guarded-agent {version('guarded-agent')}")


@app.command()
def run(
    workspace: Annotated[Path, typer.Option(exists=True, file_okay=False, resolve_path=True)],
    goal: Annotated[str, typer.Option(min=1)],
    provider: Annotated[str, typer.Option()] = "mock",
    accept: Annotated[list[str] | None, typer.Option(help="JSON argv from configured validation")] = None,
    vault: Annotated[Path | None, typer.Option()] = None,
    model: Annotated[str, typer.Option()] = "gpt-4.1-mini",
) -> None:
    """Create and run one governed task in WORKSPACE."""
    try:
        settings = load_settings(workspace)
        acceptance = _selected_acceptance(settings.validation_commands, accept or [])
        if provider == "mock":
            selected_provider: LLMProvider = ScriptedMockProvider(
                {"tool": "cannot_continue", "arguments": {"reason": "mock provider has no task script"}}
            )
        elif provider == "openai-compatible":
            password = typer.prompt("Vault password", hide_input=True)
            credential = _vault(vault).get(password)
            if credential.provider != provider:
                raise ValueError("unlocked credential does not match the selected provider")
            selected_provider = OpenAICompatibleProvider(
                endpoint=credential.endpoint, api_key=credential.api_key, model=model
            )
        else:
            raise ValueError("provider must be 'mock' or 'openai-compatible'")
        with _state_database(workspace) as database:
            service = ApplicationService(database)
            task = service.create(workspace, goal, acceptance, selected_provider)
            status = service.run(task.id)
        typer.echo(f"task {task.id}: {status.value}")
        if status is not TaskStatus.COMPLETED:
            raise typer.Exit(code=1)
    except (ConfigError, CredentialError, CredentialUnlockError, OSError, ValueError) as error:
        _fail(str(error))


@app.command()
def web(
    workspace: Annotated[Path, typer.Option(exists=True, file_okay=False, resolve_path=True)],
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
) -> None:
    """Start the local-only Web UI for one fixed workspace."""
    try:
        if host != "127.0.0.1":
            raise ValueError("WebUI may only bind to 127.0.0.1")
        import uvicorn

        from guarded_agent.web import create_web_app

        uvicorn.run(create_web_app(workspace, host=host), host=host, port=port)
    except (ConfigError, OSError, ValueError) as error:
        _fail(str(error))


@app.command()
def demo() -> None:
    """Run deterministic governance demonstrations without network access."""
    try:
        for line in run_demo():
            typer.echo(line)
    except (AssertionError, OSError, ValueError) as error:
        _fail(f"demo failed: {error}")


@credential_app.command("set")
def credential_set(
    provider: Annotated[str, typer.Option()] = "openai-compatible",
    endpoint: Annotated[str, typer.Option()] = "https://api.openai.com/v1",
    vault: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Encrypt a real-provider credential using hidden terminal input."""
    api_key = typer.prompt("API key", hide_input=True)
    password = typer.prompt("Vault password", hide_input=True, confirmation_prompt=True)
    try:
        _vault(vault).set(provider, api_key, password, endpoint=endpoint)
    except (CredentialError, OSError, ValueError) as error:
        _fail(str(error))
    typer.echo("credential configured")


@credential_app.command("status")
def credential_status(
    vault: Annotated[Path | None, typer.Option()] = None,
    unlock: Annotated[bool, typer.Option()] = False,
) -> None:
    """Report credential configuration without exposing a secret."""
    try:
        password = typer.prompt("Vault password", hide_input=True) if unlock else None
        status = _vault(vault).status(password)
    except CredentialUnlockError as error:
        _fail(str(error))
    if not status.configured:
        typer.echo("credential not configured")
    elif status.provider is None:
        typer.echo("credential configured (locked)")
    else:
        typer.echo(f"credential configured: provider={status.provider} endpoint={status.endpoint}")


@credential_app.command("clear")
def credential_clear(vault: Annotated[Path | None, typer.Option()] = None) -> None:
    """Remove the encrypted credential file."""
    try:
        _vault(vault).clear()
    except OSError as error:
        _fail(str(error))
    typer.echo("credential cleared")


@memory_app.command("add")
def memory_add(
    workspace: Annotated[Path, typer.Option(exists=True, file_okay=False, resolve_path=True)],
    category: Annotated[str, typer.Option(min=1)],
    content: Annotated[str, typer.Option(min=1)],
    source: Annotated[MemorySource, typer.Option()] = MemorySource.USER,
    trust: Annotated[MemoryTrust, typer.Option()] = MemoryTrust.CONFIRMED,
) -> None:
    """Store one workspace-scoped memory entry."""
    try:
        with _state_database(workspace) as database:
            entry = database.memory.add(_workspace_id(database, workspace), category, content, source, trust)
        typer.echo(f"memory saved: {entry.id}")
    except (OSError, ValueError) as error:
        _fail(str(error))


@memory_app.command("search")
def memory_search(
    workspace: Annotated[Path, typer.Option(exists=True, file_okay=False, resolve_path=True)],
    query: Annotated[str, typer.Option(min=1)],
    limit: Annotated[int, typer.Option(min=1, max=100)] = 10,
) -> None:
    """Search trusted workspace memories deterministically."""
    try:
        with _state_database(workspace) as database:
            entries = database.memory.search(_workspace_id(database, workspace), query, limit)
    except (OSError, ValueError) as error:
        _fail(str(error))
    for entry in entries:
        typer.echo(f"{entry.id}\t{entry.category}\t{entry.trust.value}\t{entry.content}")
