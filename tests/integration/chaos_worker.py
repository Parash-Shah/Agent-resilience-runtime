from __future__ import annotations

import asyncio
import os

from agent_resilience.config import Settings
from agent_resilience.worker import build_worker


def main() -> None:
    config = Settings(
        runtime_backend="aws",
        tool_backend="scenario",
        agent_mode="deterministic",
        aws_region=os.environ["AWS_DEFAULT_REGION"],
        aws_endpoint_url=os.environ["LOCALSTACK_ENDPOINT"],
        dynamodb_table_name=os.environ["DYNAMODB_TABLE_NAME"],
        sqs_queue_url=os.environ["SQS_QUEUE_URL"],
        sqs_dlq_url=os.environ["SQS_DLQ_URL"],
        queue_lease_seconds=2,
        queue_heartbeat_seconds=0.5,
        queue_retry_base_seconds=0,
        worker_poll_seconds=0.05,
        chaos_pause_after_steps=3,
        chaos_pause_seconds=30,
    )
    asyncio.run(build_worker(config).serve())


if __name__ == "__main__":
    main()
