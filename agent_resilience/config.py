from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_path: Path = Path("data/agent-resilience.db")
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-terra"
    agent_mode: str = "live"
    admin_api_token: str | None = None
    max_agent_turns: int = 12
    max_queue_attempts: int = 5
    queue_lease_seconds: int = 60
    queue_retry_base_seconds: float = 1.0
    worker_poll_seconds: float = 0.5
    run_worker: bool = False
    max_evidence_chars: int = 12_000
    otel_exporter_otlp_endpoint: str | None = None


settings = Settings()
