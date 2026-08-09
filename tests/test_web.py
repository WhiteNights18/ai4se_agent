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
from guarded_agent.web import create_web_app

# Both Starlette TestClient and HTTPX ASGITransport hang for even a trivial app
# under the repository's Python 3.14 runtime. CI's supported Python 3.12 path
# executes these in-process ASGI tests normally.
pytestmark = pytest.mark.skipif(sys.version_info >= (3, 14), reason="ASGI transport hangs on Python 3.14")


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


def test_settings_page_never_accepts_master_password(configured_app) -> None:
    async def scenario() -> None:
        transport = httpx.ASGITransport(app=configured_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/settings", data={"master_password": "secret"})
            assert response.status_code == 422

    asyncio.run(scenario())


def test_web_cancel_memory_status_and_csrf_controls(configured_app) -> None:
    async def scenario() -> None:
        transport = httpx.ASGITransport(app=configured_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/")
            response = await client.post(
                f"/approvals/{approval_id}/approve", data={"_csrf": client.cookies["csrf_token"]}
            )
            assert response.status_code == 200
        assert resumed == [(task.id, approval_id)]
        assert database.approvals.get(approval_id).status.value == "APPROVED"
        database.close()

    asyncio.run(scenario())


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
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/")
            response = await client.post(
                f"/approvals/{approval_id}/reject", data={"_csrf": client.cookies["csrf_token"]}
            )
            assert response.status_code == 200
        assert database.tasks.get(task.id).status.value == "RUNNING"
        assert database.tasks.list_turns(task.id)[-1].feedback_json["message"] == "approval was rejected by the user"
        database.close()

    asyncio.run(scenario())
