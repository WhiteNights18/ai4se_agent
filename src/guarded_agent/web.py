"""Small, server-rendered control surface for one fixed local workspace."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from guarded_agent.config import load_settings
from guarded_agent.credentials import CredentialVault
from guarded_agent.domain import TaskStatus
from guarded_agent.memory import MemorySource, MemoryTrust
from guarded_agent.providers.mock import ScriptedMockProvider
from guarded_agent.redaction import Redactor
from guarded_agent.service import ApplicationService
from guarded_agent.storage import Database, Task

_ACTIVE = {TaskStatus.CREATED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL}
_PACKAGE = Path(__file__).parent
_templates = Jinja2Templates(directory=str(_PACKAGE / "templates"))


def create_web_app(workspace: Path, *, host: str = "127.0.0.1") -> FastAPI:
    """Build an app whose workspace and acceptance-command allowlist never change."""
    if host != "127.0.0.1":
        raise ValueError("WebUI may only bind to 127.0.0.1")
    fixed_workspace = workspace.resolve(strict=True)
    settings = load_settings(fixed_workspace)
    validators = [list(command) for command in settings.validation_commands]
    state_directory = fixed_workspace / ".guarded-agent"
    state_directory.mkdir(mode=0o700, exist_ok=True)
    database = Database.open(state_directory / "state.sqlite3")
    service = ApplicationService(database, configured_validation_commands=validators)
    registered = database.tasks.get_workspace(str(fixed_workspace))
    if registered is None:
        registered = database.tasks.create_workspace(str(fixed_workspace), fixed_workspace.name)
    workspace_id = registered.id
    vault = CredentialVault(fixed_workspace / ".guarded-agent" / "credentials.vault")
    redactor = Redactor([])
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(_PACKAGE / "static")), name="static")

    @app.middleware("http")
    async def csrf_cookie(request: Request, call_next: Any) -> Response:
        response = cast(Response, await call_next(request))
        if request.cookies.get("csrf_token") is None:
            response.set_cookie("csrf_token", secrets.token_urlsafe(32), httponly=True, samesite="strict")
        return response

    def page(request: Request, name: str, **context: Any) -> HTMLResponse:
        return _templates.TemplateResponse(request, name, {"csrf_token": request.cookies.get("csrf_token", ""), **context})

    def check_csrf(request: Request, token: str) -> None:
        expected = request.cookies.get("csrf_token")
        if not expected or not secrets.compare_digest(expected, token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")

    def get_task(task_id: str) -> Task:
        try:
            task = database.tasks.get(task_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="task not found") from error
        if task.workspace_id != workspace_id:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    def timeline(task_id: str) -> list[dict[str, str]]:
        events = database.audit.list_for_task(task_id)
        return [
            {
                "time": event.created_at.isoformat(),
                "event": event.event_type,
                "payload": redactor.redact(json.dumps(event.redacted_payload, ensure_ascii=False)),
            }
            for event in events
        ]

    @app.get("/", response_class=HTMLResponse)
    def tasks(request: Request) -> HTMLResponse:
        return page(request, "tasks.html", tasks=database.tasks.list_for_workspace(workspace_id), validators=validators)

    @app.post("/tasks")
    def create_task(
        request: Request,
        goal: Annotated[str, Form(min_length=1, max_length=4096)],
        validation_id: Annotated[str, Form()],
        csrf: Annotated[str, Form(alias="_csrf")],
    ) -> RedirectResponse:
        check_csrf(request, csrf)
        if not validation_id.startswith("validator-"):
            raise HTTPException(status_code=422, detail="validation_id must select a configured validator")
        try:
            selected = validators[int(validation_id.removeprefix("validator-"))]
        except (IndexError, ValueError):
            raise HTTPException(status_code=422, detail="validation_id must select a configured validator") from None
        if any(task.status in _ACTIVE for task in database.tasks.list_for_workspace(workspace_id)):
            raise HTTPException(status_code=409, detail="only one task may be active")
        task = service.create(fixed_workspace, goal, [selected], ScriptedMockProvider())
        return RedirectResponse(f"/tasks/{task.id}", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    def task_detail(request: Request, task_id: str) -> HTMLResponse:
        task = get_task(task_id)
        return page(request, "task_detail.html", task=task, timeline=timeline(task.id))

    @app.get("/api/tasks/{task_id}/status")
    def task_status(task_id: str) -> JSONResponse:
        task = get_task(task_id)
        return JSONResponse({"id": task.id, "status": task.status.value, "timeline": timeline(task.id)})

    @app.post("/tasks/{task_id}/cancel")
    def cancel_task(request: Request, task_id: str, csrf: Annotated[str, Form(alias="_csrf")]) -> RedirectResponse:
        check_csrf(request, csrf)
        get_task(task_id)
        service.cancel(task_id)
        return RedirectResponse(f"/tasks/{task_id}", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(request: Request) -> HTMLResponse:
        pending = []
        for task in database.tasks.list_for_workspace(workspace_id):
            if task.status is TaskStatus.WAITING_APPROVAL:
                try:
                    approval = database.approvals.get(service.pending_approval_id(task.id))
                except KeyError:
                    continue
                pending.append(approval)
        return page(request, "approvals.html", approvals=pending)

    @app.post("/approvals/{approval_id}/{decision}")
    def decide_approval(
        request: Request, approval_id: str, decision: str, csrf: Annotated[str, Form(alias="_csrf")]
    ) -> RedirectResponse:
        check_csrf(request, csrf)
        if decision not in {"approve", "reject"}:
            raise HTTPException(status_code=404, detail="unknown approval decision")
        try:
            approval = database.approvals.get(approval_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="approval not found") from error
        get_task(approval.task_id)
        if decision == "approve":
            database.approvals.approve(approval_id, datetime.now(UTC))
        else:
            database.approvals.reject(approval_id, datetime.now(UTC))
        return RedirectResponse("/approvals", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/memories", response_class=HTMLResponse)
    def memories(request: Request) -> HTMLResponse:
        return page(request, "memories.html", memories=database.memory.list_for_workspace(workspace_id))

    @app.post("/memories")
    def add_memory(
        request: Request,
        category: Annotated[str, Form(min_length=1, max_length=128)],
        content: Annotated[str, Form(min_length=1, max_length=4096)],
        csrf: Annotated[str, Form(alias="_csrf")],
    ) -> RedirectResponse:
        check_csrf(request, csrf)
        database.memory.add(workspace_id, category, content, MemorySource.USER, MemoryTrust.CONFIRMED)
        return RedirectResponse("/memories", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/memories/{memory_id}/delete")
    def delete_memory(request: Request, memory_id: str, csrf: Annotated[str, Form(alias="_csrf")]) -> RedirectResponse:
        check_csrf(request, csrf)
        if not database.memory.delete(workspace_id, memory_id):
            raise HTTPException(status_code=404, detail="memory not found")
        return RedirectResponse("/memories", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> HTMLResponse:
        return page(request, "settings.html", credential=vault.status(), workspace=fixed_workspace.name)

    @app.post("/settings")
    async def settings_post(request: Request) -> None:
        # This intentionally has no state-changing settings: the UI cannot receive secrets or weaken policy.
        await request.form()
        raise HTTPException(status_code=422, detail="settings are read-only; use the CLI for credential changes")

    return app
