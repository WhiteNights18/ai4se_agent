"""Workspace-scoped, deterministic retrieval for trusted memories."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
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
    source: str
    trust: str
    keywords: list[str]
    created_at: datetime


def _tokens(value: str) -> list[str]:
    return _TOKEN.findall(value.casefold())


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class MemoryStore:
    def __init__(self, database: Database) -> None:
        self._connection: sqlite3.Connection = database.connection

    def add(
        self,
        workspace_id: str,
        category: str,
        content: str,
        source: str,
        trust: str,
    ) -> MemoryEntry:
        if source.casefold() in {"model", "llm"} and trust.casefold() == "confirmed":
            raise ValueError("model content cannot be recorded as confirmed memory")
        entry = MemoryEntry(
            str(uuid4()),
            workspace_id,
            category,
            content,
            source,
            trust,
            _tokens(content),
            _now(),
        )
        self._connection.execute(
            "INSERT INTO memory_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.id,
                entry.workspace_id,
                entry.category,
                entry.content,
                entry.source,
                entry.trust,
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
        rows = self._connection.execute(
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
                    cast(str, row["source"]),
                    cast(str, row["trust"]),
                    keywords,
                    created_at,
                )
                matches.append((sum(counts), cast(str, row["created_at"]), cast(int, row["rowid"]), entry))
        matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in matches[:limit]]
