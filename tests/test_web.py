"""In-process coverage for the local-only WebUI boundary."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import httpx
import pytest

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
