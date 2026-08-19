from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_resilience.errors import ConcurrentUpdateError
from agent_resilience.models import WorkflowState
from agent_resilience.store import SQLiteStore


def test_checkpoint_uses_optimistic_version(test_settings):
    store = SQLiteStore(test_settings.database_path)
    state = WorkflowState(task_id="incident-checkpoint", goal="Investigate checkout production errors")
    store.create_workflow(state, 3)
    first = store.get_workflow(state.task_id)
    stale = store.get_workflow(state.task_id)
    assert first and stale
    first.completed_steps.append("read_alert")
    saved = store.save_workflow(first, 0)
    assert saved.version == 1
    with pytest.raises(ConcurrentUpdateError):
        store.save_workflow(stale, 0)


def test_queue_reclaims_expired_lease_and_dead_letters(test_settings):
    store = SQLiteStore(test_settings.database_path)
    state = WorkflowState(task_id="incident-queue", goal="Investigate checkout production errors")
    store.create_workflow(state, 2)
    first = store.claim("worker-a", lease_seconds=-1)
    assert first and first.attempts == 1
    reclaimed = store.claim("worker-b", lease_seconds=30)
    assert reclaimed and reclaimed.id == first.id and reclaimed.attempts == 2
    assert store.retry_or_dead_letter(reclaimed, "still failing", base_delay_seconds=0)
    assert store.queue_counts()["DEAD"] == 1


def test_idempotency_rejects_argument_reuse(test_settings):
    store = SQLiteStore(test_settings.database_path)
    store.save_tool_result("key", "task", "restart", {"service": "a"}, {"ok": True})
    assert store.get_tool_result("key", {"service": "a"}) == {"ok": True}
    with pytest.raises(ValueError, match="different arguments"):
        store.get_tool_result("key", {"service": "b"})


def test_store_close_releases_database_file(test_settings):
    store = SQLiteStore(test_settings.database_path)
    store.close()
    test_settings.database_path.unlink()
    assert not test_settings.database_path.exists()
