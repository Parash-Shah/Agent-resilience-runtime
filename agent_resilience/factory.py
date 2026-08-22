from __future__ import annotations

from .aws_store import AWSDurableStore
from .aws_tools import AWSOperationsBackend
from .config import Settings
from .contracts import RuntimeStore
from .store import SQLiteStore
from .tools import ScenarioBackend


def build_store(config: Settings) -> RuntimeStore:
    if config.runtime_backend == "aws":
        return AWSDurableStore(config)
    if config.runtime_backend == "sqlite":
        return SQLiteStore(config.database_path)
    raise ValueError(f"unsupported RUNTIME_BACKEND: {config.runtime_backend}")


def build_tool_backend(config: Settings):
    if config.tool_backend == "aws":
        return AWSOperationsBackend(config)
    if config.tool_backend == "scenario":
        return ScenarioBackend()
    raise ValueError(f"unsupported TOOL_BACKEND: {config.tool_backend}")
