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
    runtime_backend: str = "sqlite"
    tool_backend: str = "scenario"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-terra"
    agent_mode: str = "live"
    admin_api_token: str | None = None
    viewer_api_token: str | None = None
    max_agent_turns: int = 12
    max_queue_attempts: int = 5
    queue_lease_seconds: int = 60
    queue_heartbeat_seconds: float = 20.0
    queue_retry_base_seconds: float = 1.0
    worker_poll_seconds: float = 0.5
    chaos_pause_tool: str | None = None
    chaos_pause_after_steps: int | None = None
    chaos_pause_seconds: float = 0.0
    run_worker: bool = False
    max_evidence_chars: int = 12_000
    otel_exporter_otlp_endpoint: str | None = None
    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    dynamodb_table_name: str = "agent-resilience-dev-workflows"
    sqs_queue_url: str | None = None
    sqs_dlq_url: str | None = None
    ecs_cluster: str = "agent-resilience-dev"
    ecs_service_prefix: str = ""
    cloudwatch_log_group_prefix: str = "/ecs/"
    cloudwatch_metric_namespace: str = "AgentResilience"
    service_metric_namespace: str = "AgentResilience/Services"
    cloudwatch_metrics_enabled: bool = False


settings = Settings()
