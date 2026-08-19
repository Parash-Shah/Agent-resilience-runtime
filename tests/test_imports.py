from __future__ import annotations

import os
import subprocess
import sys


def test_api_factory_import_does_not_require_openai_key(tmp_path):
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("AGENT_MODE", None)
    result = subprocess.run(
        [sys.executable, "-c", "from agent_resilience.api import create_app"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
