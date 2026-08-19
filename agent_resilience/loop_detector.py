from __future__ import annotations

from collections.abc import Sequence


class LoopDetector:
    def __init__(self, repeat_threshold: int = 4, max_tool_calls: int = 20):
        if repeat_threshold < 2:
            raise ValueError("repeat_threshold must be at least 2")
        self.repeat_threshold = repeat_threshold
        self.max_tool_calls = max_tool_calls

    def reason(self, tools: Sequence[str], progress: Sequence[str]) -> str | None:
        if len(tools) >= self.max_tool_calls:
            return f"tool-call budget exceeded ({self.max_tool_calls})"
        if self._repeated_suffix(tools):
            return "repeated tool sequence detected"
        if len(progress) >= self.repeat_threshold and len(set(progress[-self.repeat_threshold:])) == 1:
            return "agent repeated actions without changing workflow evidence"
        return None

    def _repeated_suffix(self, values: Sequence[str]) -> bool:
        for length in range(1, len(values) // self.repeat_threshold + 1):
            tail = values[-length * self.repeat_threshold:]
            pattern = tail[:length]
            if list(tail) == list(pattern) * self.repeat_threshold:
                return True
        return False
