"""In-process coverage for the local-only WebUI boundary."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

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
            assert "Task timeline" in (await client.get(response.headers["location"])).text

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
