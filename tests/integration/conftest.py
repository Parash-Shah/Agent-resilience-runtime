from __future__ import annotations

import os
import time
import uuid

import boto3
import pytest

from agent_resilience.config import Settings


pytestmark = pytest.mark.integration


@pytest.fixture
def aws_settings():
    if os.getenv("RUN_LOCALSTACK_TESTS") != "1":
        pytest.skip("set RUN_LOCALSTACK_TESTS=1 to run LocalStack tests")
    endpoint = os.getenv("LOCALSTACK_ENDPOINT", "http://localhost:4566")
    region = "us-east-1"
    options = {"endpoint_url": endpoint, "region_name": region}
    dynamodb = boto3.client("dynamodb", **options)
    sqs = boto3.client("sqs", **options)
    suffix = uuid.uuid4().hex[:10]
    table_name = f"agent-resilience-test-{suffix}"
    dynamodb.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "entity_type", "AttributeType": "S"},
            {"AttributeName": "updated_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "entity_type-updated_at-index",
            "KeySchema": [
                {"AttributeName": "entity_type", "KeyType": "HASH"},
                {"AttributeName": "updated_at", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )
    waiter = dynamodb.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    dlq_url = sqs.create_queue(QueueName=f"agent-resilience-dlq-{suffix}")["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    queue_url = sqs.create_queue(
        QueueName=f"agent-resilience-tasks-{suffix}",
        Attributes={"VisibilityTimeout": "2", "RedrivePolicy": f'{{"deadLetterTargetArn":"{dlq_arn}","maxReceiveCount":"3"}}'},
    )["QueueUrl"]
    config = Settings(
        runtime_backend="aws",
        tool_backend="scenario",
        agent_mode="deterministic",
        aws_region=region,
        aws_endpoint_url=endpoint,
        dynamodb_table_name=table_name,
        sqs_queue_url=queue_url,
        sqs_dlq_url=dlq_url,
        max_queue_attempts=3,
        queue_lease_seconds=2,
        queue_heartbeat_seconds=0.5,
        queue_retry_base_seconds=0,
        worker_poll_seconds=0.05,
    )
    yield config
    sqs.delete_queue(QueueUrl=queue_url)
    sqs.delete_queue(QueueUrl=dlq_url)
    dynamodb.delete_table(TableName=table_name)
