"""Workspace-scoped, deterministic retrieval for trusted memories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, cast
from uuid import uuid4

if TYPE_CHECKING:
    from guarded_agent.storage import Database


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    id: str
    workspace_id: str
    category: str
    content: str
    source: MemorySource
    trust: MemoryTrust
    keywords: list[str]
    created_at: datetime


def _tokens(value: str) -> list[str]:
    return _TOKEN.findall(value.casefold())


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class MemorySource(str, Enum):
    USER = "user"
    TASK_SUMMARY = "task_summary"
    MODEL = "model"


class MemoryTrust(str, Enum):
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"


class MemoryStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add(
        self,
        workspace_id: str,
        category: str,
        content: str,
        source: MemorySource | str,
        trust: MemoryTrust | str,
    ) -> MemoryEntry:
        trust_value = _parse_trust(trust)
        if trust_value is MemoryTrust.CONFIRMED:
            source_value = _confirmed_source(source)
        else:
            source_value = _parse_source(source)
        entry = MemoryEntry(
            str(uuid4()),
            workspace_id,
            category,
            content,
            source_value,
            trust_value,
            _tokens(content),
            _now(),
        )
        with self._database.operation() as connection:
            connection.execute(
                "INSERT INTO memory_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.id,
                    entry.workspace_id,
                    entry.category,
                    entry.content,
                    entry.source.value,
                    entry.trust.value,
                    " ".join(entry.keywords),
                    _timestamp(entry.created_at),
                ),
            )
        return entry

    def search(self, workspace_id: str, query: str, limit: int = 10) -> list[MemoryEntry]:
        if limit < 1:
            return []
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        with self._database.operation() as connection:
            rows = connection.execute(
                """SELECT id, workspace_id, category, content, source, trust, keywords, created_at, rowid
                   FROM memory_entries WHERE workspace_id = ?""",
                (workspace_id,),
            ).fetchall()
        matches: list[tuple[int, str, int, MemoryEntry]] = []
        for row in rows:
            keywords = cast(str, row["keywords"]).split()
            counts = [keywords.count(token) for token in query_tokens]
            if all(counts):
                created_at = datetime.fromisoformat(cast(str, row["created_at"]))
                entry = MemoryEntry(
                    cast(str, row["id"]),
                    cast(str, row["workspace_id"]),
                    cast(str, row["category"]),
                    cast(str, row["content"]),
                    MemorySource(cast(str, row["source"])),
                    MemoryTrust(cast(str, row["trust"])),
                    keywords,
                    created_at,
                )
                matches.append((sum(counts), cast(str, row["created_at"]), cast(int, row["rowid"]), entry))
        matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in matches[:limit]]


def _parse_source(source: MemorySource | str) -> MemorySource:
    try:
        return MemorySource(source)
    except ValueError as error:
        raise ValueError("invalid memory source") from error


def _parse_trust(trust: MemoryTrust | str) -> MemoryTrust:
    try:
        return MemoryTrust(trust)
    except ValueError as error:
        raise ValueError("invalid memory trust") from error


def _confirmed_source(source: MemorySource | str) -> MemorySource:
    try:
        source_value = MemorySource(source)
    except ValueError as error:
        raise ValueError("confirmed memory requires user or task_summary source; model content is not permitted") from error
    if source_value not in {MemorySource.USER, MemorySource.TASK_SUMMARY}:
        raise ValueError("confirmed memory requires user or task_summary source; model content is not permitted")
    return source_value
