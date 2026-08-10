"""Small, server-rendered control surface for one fixed local workspace."""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, cast

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from guarded_agent.config import load_settings
from guarded_agent.credentials import CredentialVault
from guarded_agent.domain import TaskStatus
from guarded_agent.memory import MemoryEntry, MemorySource, MemoryTrust
from guarded_agent.providers.mock import ScriptedMockProvider
from guarded_agent.redaction import Redactor
from guarded_agent.service import ApplicationService
from guarded_agent.storage import Approval, Database, Task

_ACTIVE = {TaskStatus.CREATED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL}
_PACKAGE = Path(__file__).parent
_templates = Jinja2Templates(directory=str(_PACKAGE / "templates"))
_PAGE_METADATA = {
    "tasks.html": ("tasks", "任务工作台"),
    "task_detail.html": ("tasks", "任务详情"),
    "approvals.html": ("approvals", "审批中心"),
    "memories.html": ("memories", "记忆库"),
    "settings.html": ("settings", "设置"),
}
_STATUS_LABELS = {
    "CREATED": "已创建",
    "RUNNING": "执行中",
    "WAITING_APPROVAL": "等待审批",
    "COMPLETED": "已完成",
    "FAILED": "失败",
    "CANCELLED": "已取消",
    "PENDING": "待审批",
    "APPROVED": "已批准",
    "REJECTED": "已拒绝",
    "EXPIRED": "已过期",
    "CONSUMED": "已执行",
}
_EVENT_LABELS = {
    "task_created": "任务已创建",
    "task_started": "任务已开始",
    "task_cancelled": "任务已取消",
    "approval_rejected": "审批已拒绝",
    "approval_mismatch": "审批校验不匹配",
}
_MEMORY_SOURCE_LABELS = {
    MemorySource.USER: "用户确认",
    MemorySource.TASK_SUMMARY: "任务摘要",
    MemorySource.MODEL: "模型生成",
}
_MEMORY_TRUST_LABELS = {
    MemoryTrust.CONFIRMED: "已确认",
    MemoryTrust.UNCONFIRMED: "未确认",
}


def _short_id(identifier: str) -> str:
    """Provide a compact reference while retaining the full value in the rendered title."""
    return identifier[:8]


def _format_timestamp(value: datetime) -> str:
    """Present a timezone-aware audit timestamp without changing its raw API value."""
    return value.astimezone(UTC).strftime("%Y年%m月%d日 %H:%M UTC")


def _task_display(task: Task) -> dict[str, str]:
    return {
        "short_id": _short_id(task.id),
        "status_label": _STATUS_LABELS.get(task.status.value, "未知"),
        "created_at": _format_timestamp(task.created_at),
        "created_at_raw": task.created_at.isoformat(),
        "updated_at": _format_timestamp(task.updated_at),
        "updated_at_raw": task.updated_at.isoformat(),
    }


def _approval_display(approval: Approval) -> dict[str, str]:
    return {
        "short_id": _short_id(approval.id),
        "status_label": _STATUS_LABELS.get(approval.status.value, "未知"),
    }


def _memory_display(memory: MemoryEntry) -> dict[str, str]:
    return {
        "short_id": _short_id(memory.id),
        "source_label": _MEMORY_SOURCE_LABELS[memory.source],
        "trust_label": _MEMORY_TRUST_LABELS[memory.trust],
        "created_at": _format_timestamp(memory.created_at),
        "created_at_raw": memory.created_at.isoformat(),
    }


def create_web_app(
    workspace: Path, *, host: str = "127.0.0.1", redactor: Redactor | None = None
) -> FastAPI:
    """Build an app whose workspace and acceptance-command allowlist never change."""
    if host != "127.0.0.1":
        raise ValueError("WebUI may only bind to 127.0.0.1")
    fixed_workspace = workspace.resolve(strict=True)
    settings = load_settings(fixed_workspace)
    validators = [list(command) for command in settings.validation_commands]
    state_directory = fixed_workspace / ".guarded-agent"
    state_directory.mkdir(mode=0o700, exist_ok=True)
    database = Database.open(state_directory / "state.sqlite3")
    payload_redactor = redactor or Redactor([])
    service = ApplicationService(
        database, configured_validation_commands=validators, redactor=payload_redactor
    )
    registered = database.tasks.get_workspace(str(fixed_workspace))
    if registered is None:
        registered = database.tasks.create_workspace(str(fixed_workspace), fixed_workspace.name)
    workspace_id = registered.id
    vault = CredentialVault(fixed_workspace / ".guarded-agent" / "credentials.vault")
    create_lock = Lock()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(_PACKAGE / "static")), name="static")

    @app.middleware("http")
    async def csrf_cookie(request: Request, call_next: Any) -> Response:
        request.state.csrf_token = request.cookies.get("csrf_token") or secrets.token_urlsafe(32)
        response = cast(Response, await call_next(request))
        if request.cookies.get("csrf_token") is None:
            response.set_cookie("csrf_token", request.state.csrf_token, httponly=True, samesite="strict")
        return response

    def page(request: Request, name: str, **context: Any) -> HTMLResponse:
        active_page, page_title = _PAGE_METADATA.get(name, ("", "受控 Agent"))
        return _templates.TemplateResponse(
            request,
            name,
            {
                "csrf_token": request.state.csrf_token,
                "active_page": active_page,
                "page_title": page_title,
                "workspace_name": fixed_workspace.name,
                **context,
            },
        )

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
                "time_display": _format_timestamp(event.created_at),
                "event": event.event_type,
                "event_label": _EVENT_LABELS.get(event.event_type, "受控事件"),
                "payload": payload_redactor.redact(
                    json.dumps(event.redacted_payload, ensure_ascii=False)
                ),
            }
            for event in events
        ]

    @app.get("/", response_class=HTMLResponse)
    def tasks(request: Request) -> HTMLResponse:
        listed_tasks = database.tasks.list_for_workspace(workspace_id)
        active_task = next((task for task in listed_tasks if task.status in _ACTIVE), None)
        return page(
            request,
            "tasks.html",
            tasks=listed_tasks,
            task_display={task.id: _task_display(task) for task in listed_tasks},
            task_count=len(listed_tasks),
            active_task=active_task,
            validators=validators,
        )

    @app.post("/tasks")
    def create_task(
        request: Request,
        goal: Annotated[str, Form(min_length=1, max_length=4096)],
        validation_id: Annotated[str, Form()],
        csrf: Annotated[str, Form(alias="_csrf")],
    ) -> RedirectResponse:
        check_csrf(request, csrf)
        match = re.fullmatch(r"validator-(0|[1-9][0-9]*)", validation_id)
        if match is None:
            raise HTTPException(status_code=422, detail="validation_id must select a configured validator")
        try:
            selected = validators[int(match.group(1))]
        except (IndexError, ValueError):
            raise HTTPException(status_code=422, detail="validation_id must select a configured validator") from None
        with create_lock:
            if any(task.status in _ACTIVE for task in database.tasks.list_for_workspace(workspace_id)):
                raise HTTPException(status_code=409, detail="only one task may be active")
            task = service.create(fixed_workspace, goal, [selected], ScriptedMockProvider())
        return RedirectResponse(f"/tasks/{task.id}", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    def task_detail(request: Request, task_id: str) -> HTMLResponse:
        task = get_task(task_id)
        return page(
            request,
            "task_detail.html",
            task=task,
            task_display=_task_display(task),
            timeline=timeline(task.id),
        )

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
        return page(
            request,
            "approvals.html",
            approvals=pending,
            approval_display={approval.id: _approval_display(approval) for approval in pending},
        )

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
            service.resume(approval.task_id, approval_id)
        else:
            try:
                service.reject_approval(approval.task_id, approval_id)
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
        return RedirectResponse("/approvals", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/memories", response_class=HTMLResponse)
    def memories(request: Request) -> HTMLResponse:
        entries = database.memory.list_for_workspace(workspace_id)
        return page(
            request,
            "memories.html",
            memories=entries,
            memory_display={memory.id: _memory_display(memory) for memory in entries},
        )

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
