"""In-process coverage for the local-only WebUI boundary."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from guarded_agent.providers.mock import ScriptedMockProvider
from guarded_agent.service import ApplicationService
from guarded_agent.storage import Database
from guarded_agent.web import _templates, create_web_app

# Both Starlette TestClient and HTTPX ASGITransport hang for even a trivial app
# under the repository's Python 3.14 runtime. CI's supported Python 3.12 path
# executes these in-process ASGI tests normally.  Template contracts remain
# runnable on Python 3.14, so keep this marker off their source-level tests.
requires_asgi_transport = pytest.mark.skipif(
    sys.version_info >= (3, 14), reason="ASGI transport hangs on Python 3.14"
)


def test_shared_workbench_shell_exposes_accessible_chinese_navigation() -> None:
    """Catch a regression back to the old English, unstructured page shell."""
    template = _templates.get_template("base.html").render(
        active_page="tasks", page_title="任务工作台", workspace_name="演示工作区"
    )
    badge = _templates.get_template("_macros.html").module.status_badge("WAITING_APPROVAL")

    assert '<html lang="zh-CN" data-theme="system">' in template
    assert '<nav aria-label="主导航">' in template
    assert 'aria-current="page"' in template
    assert 'aria-label="切换主题"' in template
    assert "受控 Agent" in template
    assert "任务" in template
    assert "审批" in template
    assert "记忆" in template
    assert "设置" in template
    assert "127.0.0.1" in template
    assert "固定工作区" in template
    assert "演示工作区" in template
    assert 'class="app-shell"' in template
    assert 'class="sidebar"' in template
    assert 'class="workspace-main"' in template
    assert 'class="context-panel"' in template
    assert "status-badge" in badge
    assert "等待审批" in badge


def test_shared_design_system_has_theme_responsive_and_focus_contracts() -> None:
    """Catch loss of the offline CSS contracts required by the shared shell."""
    stylesheet = (Path(__file__).parents[1] / "src/guarded_agent/static/app.css").read_text()

    assert ".status-badge" in stylesheet
    assert ".status-badge--completed" in stylesheet
    assert "@media (max-width: 720px)" in stylesheet
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
    assert ":focus-visible" in stylesheet
    assert 'html[data-theme="dark"]' in stylesheet
    assert 'html[data-theme="light"]' in stylesheet


def test_shell_initializes_a_supported_stored_theme_before_paint() -> None:
    """Catch a shell that flashes because it ignores the stored theme mode."""
    template = (Path(__file__).parents[1] / "src/guarded_agent/templates/base.html").read_text()

    assert 'localStorage.getItem("guarded-agent-theme")' in template
    assert '["system", "light", "dark"]' in template


def test_live_workbench_contract_keeps_theme_and_status_updates_safe() -> None:
    """Catch a theme or poller regression that can break the local workbench."""
    root = Path(__file__).parents[1]
    javascript = (root / "src/guarded_agent/static/app.js").read_text()
    template = (root / "src/guarded_agent/templates/task_detail.html").read_text()
    macros = (root / "src/guarded_agent/templates/_macros.html").read_text()
    stylesheet = (root / "src/guarded_agent/static/app.css").read_text()

    assert 'const themes = ["system", "light", "dark"]' in javascript
    assert 'localStorage.setItem("guarded-agent-theme", nextTheme)' in javascript
    assert 'window.matchMedia("(prefers-color-scheme: dark)")' in javascript
    assert 'mediaQuery.addEventListener("change",' in javascript
    assert 'if (document.documentElement.dataset.theme !== "system") return;' in javascript
    assert 'credentials: "same-origin"' in javascript
    assert 'new URL(node.dataset.statusUrl, window.location.origin)' in javascript
    assert 'url.origin !== window.location.origin' in javascript
    assert 'try {' in javascript and 'catch (_)' in javascript
    assert '[data-status-value]' in javascript
    assert 'badge.classList.remove(...statusClasses)' in javascript
    assert 'badge.classList.add(`status-badge--${payload.status.toLowerCase().replaceAll("_", "-")}`)' in javascript
    assert '[data-timeline]' in javascript
    assert 'data-status-value' in macros
    assert '<p class="context-card__value" data-status-label>' in template
    assert 'data-timeline' in template
    assert '@media (max-width: 720px)' in stylesheet
    assert '@media (prefers-reduced-motion: reduce)' in stylesheet
    assert ':focus-visible' in stylesheet
    assert 'aria-label="主导航"' in (root / "src/guarded_agent/templates/base.html").read_text()


def test_theme_button_cycles_modes_persists_the_selection_and_updates_aria_state(
    tmp_path: Path,
) -> None:
    """Catch a toggle that skips a mode, fails to persist, or misreports its state."""
    javascript_shell = shutil.which("js140")
    if javascript_shell is None:
        pytest.skip("SpiderMonkey js140 is unavailable for the browser-script contract")

    script_path = Path(__file__).parents[1] / "src/guarded_agent/static/app.js"
    harness = tmp_path / "theme_button_contract.js"
    harness.write_text(
        f'''\
const documentListeners = {{}};
const buttonListeners = {{}};
const attributes = {{}};
const persisted = [];
const themeButton = {{
  addEventListener(type, handler) {{ buttonListeners[type] = handler; }},
  setAttribute(name, value) {{ attributes[name] = String(value); }},
}};
globalThis.document = {{
  documentElement: {{ dataset: {{ theme: "system" }} }},
  addEventListener(type, handler) {{ documentListeners[type] = handler; }},
  querySelector(selector) {{
    if (selector === "[data-theme-toggle]") return themeButton;
    if (selector === "[data-status-url]") return null;
    throw new Error("unexpected selector: " + selector);
  }},
}};
globalThis.localStorage = {{
  setItem(key, value) {{ persisted.push([key, value]); }},
}};
globalThis.window = {{ setInterval() {{ throw new Error("unexpected poll"); }} }};
load({script_path.as_posix()!r});
documentListeners.DOMContentLoaded();
function expect(actual, expected, message) {{
  if (actual !== expected) throw new Error(message + ": expected " + expected + ", got " + actual);
}}
expect(attributes["aria-pressed"], "false", "system mode must not be pressed");
for (const [mode, pressed] of [["light", "true"], ["dark", "true"], ["system", "false"]]) {{
  buttonListeners.click();
  expect(document.documentElement.dataset.theme, mode, "theme after click");
  expect(attributes["aria-pressed"], pressed, "pressed state after click");
}}
expect(persisted.length, 3, "persisted selection count");
for (const [index, mode] of ["light", "dark", "system"].entries()) {{
  expect(persisted[index][0], "guarded-agent-theme", "storage key");
  expect(persisted[index][1], mode, "persisted mode");
}}
print("theme button contract passed");
'''
    )

    result = subprocess.run(
        [javascript_shell, str(harness)], text=True, capture_output=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "theme button contract passed\n"


def test_control_page_templates_render_workbench_information_and_preserve_form_contracts() -> None:
    """Catch a regression that removes a control page's usable, safe workbench UI."""
    task = SimpleNamespace(
        id="task-12345678",
        short_id="task-123",
        goal="修复测试失败",
        status=SimpleNamespace(value="WAITING_APPROVAL"),
        status_label="等待审批",
        created_at_display="2026年08月10日 09:30 UTC",
        updated_at_display="2026年08月10日 09:31 UTC",
    )
    approval = SimpleNamespace(
        id="approval-12345678",
        short_id="approval-123",
        summary="删除受控文件需要确认",
        status=SimpleNamespace(value="PENDING"),
        status_label="待审批",
    )
    memory = SimpleNamespace(
        id="memory-12345678",
        short_id="memory-123",
        category="项目约定",
        content="先写测试",
        source_label="用户确认",
        trust_label="已确认",
        source=SimpleNamespace(value="user"),
        trust=SimpleNamespace(value="confirmed"),
        created_at_display="2026年08月10日 09:32 UTC",
    )
    common = {
        "active_page": "tasks",
        "page_title": "任务工作台",
        "workspace_name": "演示工作区",
        "csrf_token": "csrf-contract-token",
    }
    task_display = {
        task.id: {
            "short_id": task.short_id,
            "created_at": task.created_at_display,
            "created_at_raw": "2026-08-10T09:30:00+00:00",
            "updated_at": task.updated_at_display,
            "updated_at_raw": "2026-08-10T09:31:00+00:00",
        }
    }
    approval_display = {approval.id: {"short_id": approval.short_id, "status_label": approval.status_label}}
    memory_display = {
        memory.id: {
            "short_id": memory.short_id,
            "source_label": memory.source_label,
            "trust_label": memory.trust_label,
            "created_at": memory.created_at_display,
            "created_at_raw": "2026-08-10T09:32:00+00:00",
        }
    }

    tasks_page = _templates.get_template("tasks.html").render(
        **common,
        tasks=[task],
        task_display=task_display,
        task_count=1,
        active_task=task,
        validators=[["pytest", "-q"]],
    )
    detail_page = _templates.get_template("task_detail.html").render(
        **common,
        task=task,
        task_display=task_display[task.id],
        timeline=[
            {
                "time": "2026-08-10T09:30:00+00:00",
                "time_display": "2026年08月10日 09:30 UTC",
                "event": "task_started",
                "event_label": "任务已开始",
                "payload": '{"reason":"demo"}',
            }
        ],
    )
    approvals_page = _templates.get_template("approvals.html").render(
        **(common | {"active_page": "approvals"}),
        approvals=[approval],
        approval_display=approval_display,
    )
    memories_page = _templates.get_template("memories.html").render(
        **(common | {"active_page": "memories"}), memories=[memory], memory_display=memory_display
    )
    settings_page = _templates.get_template("settings.html").render(
        **(common | {"active_page": "settings"}),
        workspace="演示工作区",
        credential=SimpleNamespace(configured=True),
    )

    assert 'class="task-card"' in tasks_page
    assert "任务数量：1" in tasks_page
    assert "新建任务" in tasks_page
    assert "等待审批" in tasks_page
    assert "WAITING_APPROVAL" in tasks_page
    assert '<form method="post" action="/tasks">' in tasks_page
    assert 'name="goal"' in tasks_page
    assert 'name="validation_id"' in tasks_page
    assert 'value="validator-0"' in tasks_page
    assert 'name="_csrf" value="csrf-contract-token"' in tasks_page

    assert 'id="task-status"' in detail_page
    assert 'data-status-url="/api/tasks/task-12345678/status"' in detail_page
    assert re.search(
        r'id="task-status".*?<strong[^>]*class="status-badge__raw"[^>]*>WAITING_APPROVAL</strong>',
        detail_page,
        re.DOTALL,
    )
    assert "事件时间线" in detail_page
    assert '<details class="event-payload">' in detail_page
    assert "取消任务" in detail_page
    assert 'action="/tasks/task-12345678/cancel"' in detail_page

    assert 'class="approval-card approval-card--risk"' in approvals_page
    assert "高风险操作需人工确认" in approvals_page
    assert "拒绝操作" in approvals_page
    assert "批准并继续" in approvals_page
    assert 'action="/approvals/approval-12345678/reject"' in approvals_page
    assert 'action="/approvals/approval-12345678/approve"' in approvals_page

    assert 'class="memory-card"' in memories_page
    assert "记忆条目" in memories_page
    assert "新增记忆" in memories_page
    assert "仅保存已确认事实" in memories_page
    assert '<form method="post" action="/memories">' in memories_page
    assert 'action="/memories/memory-12345678/delete"' in memories_page
    assert "用户确认" in memories_page
    assert "user" in memories_page
    assert "已确认" in memories_page
    assert "confirmed" in memories_page

    assert 'class="setting-card"' in settings_page
    assert "安全策略" in settings_page
    assert "凭据只能通过 CLI 管理" in settings_page
    assert "guarded-agent credentials" in settings_page
    assert "<form" not in settings_page


@pytest.fixture
def configured_app(tmp_path: Path):
    """Create an in-process ASGI app with one startup-configured validator.

    FastAPI's TestClient currently hangs under this repository's Python 3.14
    runtime, so tests use HTTPX's equivalent in-process ASGI transport.
    """
    (tmp_path / "guarded-agent.toml").write_text(
        '[validation]\ncommands = [["pytest", "-q"]]\n'
    )
    return create_web_app(tmp_path)


@requires_asgi_transport
def test_web_can_create_mock_task_with_configured_validator(configured_app) -> None:
    async def scenario() -> None:
        transport = httpx.ASGITransport(app=configured_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first_page = await client.get("/")
            token = re.search(r'name="_csrf" value="([^"]+)"', first_page.text)
            assert token is not None
            assert token.group(1) == client.cookies["csrf_token"]
            response = await client.post(
                "/tasks",
                data={
                    "goal": "fix it",
                    "validation_id": "validator-0",
                    "_csrf": token.group(1),
                },
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert "事件时间线" in (await client.get(response.headers["location"])).text

    asyncio.run(scenario())


@requires_asgi_transport
def test_web_rejects_arbitrary_validation_command(configured_app) -> None:
    async def scenario() -> None:
        transport = httpx.ASGITransport(app=configured_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/")
            response = await client.post(
                "/tasks",
                data={
                    "goal": "fix it",
                    "validation_id": "pytest -q",
                    "_csrf": client.cookies["csrf_token"],
                },
            )
            assert response.status_code == 422

    asyncio.run(scenario())


@requires_asgi_transport
def test_web_rejects_signed_or_malformed_validator_ids(configured_app) -> None:
    async def scenario() -> None:
        transport = httpx.ASGITransport(app=configured_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/")
            response = await client.post(
                "/tasks",
                data={
                    "goal": "fix it",
                    "validation_id": "validator--1",
                    "_csrf": client.cookies["csrf_token"],
                },
            )
            assert response.status_code == 422

    asyncio.run(scenario())


@requires_asgi_transport
def test_settings_page_never_accepts_master_password(configured_app) -> None:
    async def scenario() -> None:
        transport = httpx.ASGITransport(app=configured_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/settings", data={"master_password": "secret"})
            assert response.status_code == 422

    asyncio.run(scenario())


@requires_asgi_transport
def test_web_cancel_memory_status_and_csrf_controls(configured_app) -> None:
    async def scenario() -> None:
        transport = httpx.ASGITransport(app=configured_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=True
        ) as client:
            await client.get("/")
            token = client.cookies["csrf_token"]
            created = await client.post(
                "/tasks",
                data={"goal": "fix it", "validation_id": "validator-0", "_csrf": token},
                follow_redirects=False,
            )
            task_url = created.headers["location"]
            task_id = task_url.rsplit("/", 1)[-1]
            assert (
                await client.post(
                    "/tasks",
                    data={"goal": "second", "validation_id": "validator-0", "_csrf": token},
                )
            ).status_code == 409
            assert (await client.get(f"/api/tasks/{task_id}/status")).json()["status"] == "CREATED"
            assert (await client.post(f"/tasks/{task_id}/cancel", data={"_csrf": token})).status_code == 200
            assert (await client.get(f"/api/tasks/{task_id}/status")).json()["status"] == "CANCELLED"
            saved = await client.post(
                "/memories", data={"category": "style", "content": "use tests", "_csrf": token}
            )
            assert saved.status_code == 200
            page = await client.get("/memories")
            memory_id = re.search(r"/memories/([^/]+)/delete", page.text)
            assert memory_id is not None
            assert (await client.post(f"/memories/{memory_id.group(1)}/delete", data={"_csrf": token})).status_code == 200
            assert (await client.post("/memories", data={"category": "x", "content": "y", "_csrf": "bad"})).status_code == 403

    asyncio.run(scenario())


def test_web_binds_only_localhost_and_fixed_workspace(tmp_path: Path) -> None:
    (tmp_path / "guarded-agent.toml").write_text("[validation]\ncommands = []\n")
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_web_app(tmp_path, host="0.0.0.0")


@requires_asgi_transport
def test_web_approve_route_resumes_the_exact_pending_action(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        (tmp_path / "guarded-agent.toml").write_text("[validation]\ncommands = []\n")
        app = create_web_app(tmp_path)
        database = Database.open(tmp_path / ".guarded-agent" / "state.sqlite3")
        service = ApplicationService(database)
        task = service.create(
            tmp_path,
            "remove file",
            [],
            ScriptedMockProvider({"tool": "delete_file", "arguments": {"path": "missing.txt"}}),
        )
        assert service.run(task.id).value == "WAITING_APPROVAL"
        approval_id = service.pending_approval_id(task.id)
        resumed: list[tuple[str, str]] = []

        def resume(self, task_id: str, supplied_approval_id: str):
            resumed.append((task_id, supplied_approval_id))
            return database.tasks.get(task_id).status

        monkeypatch.setattr(ApplicationService, "resume", resume)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=True
        ) as client:
            await client.get("/")
            response = await client.post(
                f"/approvals/{approval_id}/approve", data={"_csrf": client.cookies["csrf_token"]}
            )
            assert response.status_code == 200
        assert resumed == [(task.id, approval_id)]
        assert database.approvals.get(approval_id).status.value == "APPROVED"
        database.close()

    asyncio.run(scenario())


@requires_asgi_transport
def test_web_reject_route_records_feedback_and_returns_to_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "guarded-agent.toml").write_text("[validation]\ncommands = []\n")
        app = create_web_app(tmp_path)
        database = Database.open(tmp_path / ".guarded-agent" / "state.sqlite3")
        service = ApplicationService(database)
        task = service.create(
            tmp_path,
            "remove file",
            [],
            ScriptedMockProvider({"tool": "delete_file", "arguments": {"path": "missing.txt"}}),
        )
        assert service.run(task.id).value == "WAITING_APPROVAL"
        approval_id = service.pending_approval_id(task.id)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=True
        ) as client:
            await client.get("/")
            response = await client.post(
                f"/approvals/{approval_id}/reject", data={"_csrf": client.cookies["csrf_token"]}
            )
            assert response.status_code == 200
        assert database.tasks.get(task.id).status.value == "RUNNING"
        assert database.tasks.list_turns(task.id)[-1].feedback_json["message"] == "approval was rejected by the user"
        database.close()

    asyncio.run(scenario())
