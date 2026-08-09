from collections.abc import Iterator
from pathlib import Path

import pytest

from guarded_agent.memory import MemoryStore
from guarded_agent.storage import Database


@pytest.fixture
def memory(tmp_path: Path) -> Iterator[MemoryStore]:
    database = Database.open(tmp_path / "guarded-agent.sqlite3")
    store = MemoryStore(database)
    yield store
    database.close()


def test_memory_search_is_workspace_scoped(memory: MemoryStore) -> None:
    """Catch retrieval that leaks conventions from another workspace."""
    memory.add("w1", "convention", "Use Ruff", "user", "confirmed")
    memory.add("w2", "convention", "Use Black", "user", "confirmed")

    assert [entry.content for entry in memory.search("w1", "Ruff")] == ["Use Ruff"]


def test_memory_search_matches_all_query_tokens_and_ranks_newer_entries_first(
    memory: MemoryStore,
) -> None:
    """Catch broad token matching or unstable ordering of equally relevant memories."""
    first = memory.add("w1", "convention", "Use Ruff for formatting", "user", "confirmed")
    second = memory.add("w1", "convention", "Use Ruff for linting", "user", "confirmed")
    memory.add("w1", "convention", "Use pytest", "user", "confirmed")

    assert [entry.id for entry in memory.search("w1", "Use Ruff")] == [second.id, first.id]


def test_model_content_cannot_be_recorded_as_confirmed_memory(memory: MemoryStore) -> None:
    """Catch an unverified model guess being promoted to trusted workspace memory."""
    with pytest.raises(ValueError, match="model"):
        memory.add("w1", "convention", "Use an imaginary linter", "model", "confirmed")
