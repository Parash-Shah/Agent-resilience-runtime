from __future__ import annotations

from pathlib import Path

import pytest

from agent_resilience.config import Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "runtime.db",
        agent_mode="deterministic",
        max_queue_attempts=3,
        queue_retry_base_seconds=0,
        run_worker=False,
        admin_api_token="test-admin-token",
        viewer_api_token="test-viewer-token",
    )
