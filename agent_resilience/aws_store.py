from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .config import Settings
from .errors import ConcurrentUpdateError, PermanentWorkflowError, RetryableWorkflowError
from .models import DeadLetterRecord, EventRecord, QueueDelivery, WorkflowState, WorkflowStatus, utc_now


class AWSDurableStore:
    """DynamoDB checkpoints/event ledger plus an at-least-once SQS task queue."""

    def __init__(self, config: Settings):
        if not config.sqs_queue_url:
            raise ValueError("SQS_QUEUE_URL is required for the AWS runtime backend")
        session = boto3.session.Session(region_name=config.aws_region)
        client_options = {"endpoint_url": config.aws_endpoint_url} if config.aws_endpoint_url else {}
        self.table = session.resource("dynamodb", **client_options).Table(config.dynamodb_table_name)
        self.sqs = session.client("sqs", **client_options)
        self.queue_url = config.sqs_queue_url
        self.dlq_url = config.sqs_dlq_url

    @staticmethod
    def _workflow_pk(task_id: str) -> str:
        return f"WORKFLOW#{task_id}"

    @staticmethod
    def arguments_hash(arguments: dict[str, Any]) -> str:
        canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def create_workflow(self, state: WorkflowState, max_attempts: int) -> WorkflowState:
        try:
            self.table.put_item(
                Item={
                    "pk": self._workflow_pk(state.task_id),
                    "sk": "STATE",
                    "entity_type": "WORKFLOW",
                    "version": state.version,
                    "state_json": state.model_dump_json(),
                    "updated_at": state.updated_at.isoformat(),
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            self.record_event(state.task_id, "WORKFLOW_CREATED", {"goal": state.goal})
            self.enqueue(state.task_id, max_attempts)
            return state
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise PermanentWorkflowError(f"workflow already exists: {state.task_id}") from error
            raise self._aws_error("create workflow", error) from error

    def get_workflow(self, task_id: str) -> WorkflowState | None:
        try:
            response = self.table.get_item(
                Key={"pk": self._workflow_pk(task_id), "sk": "STATE"},
                ConsistentRead=True,
            )
        except ClientError as error:
            raise self._aws_error("read checkpoint", error) from error
        item = response.get("Item")
        return WorkflowState.model_validate_json(item["state_json"]) if item else None

    def list_workflows(self, limit: int = 100, status: WorkflowStatus | None = None) -> list[WorkflowState]:
        requested_limit = max(1, min(limit, 500))
        try:
            response = self.table.query(
                IndexName="entity_type-updated_at-index",
                KeyConditionExpression=Key("entity_type").eq("WORKFLOW"),
                ScanIndexForward=False,
                Limit=500 if status is not None else requested_limit,
            )
        except ClientError as error:
            raise self._aws_error("list workflows", error) from error
        states = [WorkflowState.model_validate_json(item["state_json"]) for item in response.get("Items", [])]
        return [state for state in states if status is None or state.status == status][:requested_limit]

    def save_workflow(self, state: WorkflowState, expected_version: int) -> WorkflowState:
        state.version = expected_version + 1
        state.updated_at = utc_now()
        try:
            self.table.update_item(
                Key={"pk": self._workflow_pk(state.task_id), "sk": "STATE"},
                UpdateExpression="SET state_json=:state, version=:next, updated_at=:updated",
                ConditionExpression="version=:expected",
                ExpressionAttributeValues={
                    ":state": state.model_dump_json(),
                    ":next": state.version,
                    ":updated": state.updated_at.isoformat(),
                    ":expected": expected_version,
                },
            )
            return state
        except ClientError as error:
            state.version = expected_version
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConcurrentUpdateError(f"stale checkpoint for {state.task_id}") from error
            raise self._aws_error("save checkpoint", error) from error

    def enqueue(self, task_id: str, max_attempts: int, payload: dict[str, Any] | None = None) -> str:
        body = json.dumps({"task_id": task_id, "max_attempts": max_attempts, "payload": payload or {}})
        try:
            return self.sqs.send_message(QueueUrl=self.queue_url, MessageBody=body)["MessageId"]
        except ClientError as error:
            raise self._aws_error("enqueue task", error) from error

    def claim(self, worker_id: str, lease_seconds: int) -> QueueDelivery | None:
        del worker_id
        try:
            response = self.sqs.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=1,
                VisibilityTimeout=lease_seconds,
                MessageSystemAttributeNames=["ApproximateReceiveCount"],
            )
        except ClientError as error:
            raise self._aws_error("claim task", error) from error
        messages = response.get("Messages", [])
        if not messages:
            return None
        message = messages[0]
        body = json.loads(message["Body"])
        receipt = message["ReceiptHandle"]
        return QueueDelivery(
            id=receipt,
            receipt_handle=receipt,
            task_id=body["task_id"],
            attempts=int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1")),
            max_attempts=int(body.get("max_attempts", 5)),
            payload=body.get("payload", {}),
        )

    def extend_lease(self, delivery: QueueDelivery, lease_seconds: int) -> None:
        receipt = delivery.receipt_handle or str(delivery.id)
        try:
            self.sqs.change_message_visibility(
                QueueUrl=self.queue_url,
                ReceiptHandle=receipt,
                VisibilityTimeout=lease_seconds,
            )
        except ClientError as error:
            raise self._aws_error("extend queue visibility", error) from error

    def acknowledge(self, delivery_id: int | str) -> None:
        try:
            self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=str(delivery_id))
        except ClientError as error:
            raise self._aws_error("acknowledge task", error) from error

    def retry_or_dead_letter(self, delivery: QueueDelivery, error: str, base_delay_seconds: float = 1.0) -> bool:
        receipt = delivery.receipt_handle or str(delivery.id)
        dead = delivery.attempts >= delivery.max_attempts
        try:
            if dead:
                if not self.dlq_url:
                    raise PermanentWorkflowError("SQS_DLQ_URL is required to dead-letter tasks explicitly")
                self.sqs.send_message(
                    QueueUrl=self.dlq_url,
                    MessageBody=json.dumps({
                        "task_id": delivery.task_id,
                        "payload": delivery.payload,
                        "attempts": delivery.attempts,
                        "max_attempts": delivery.max_attempts,
                        "error": error[:2_000],
                    }),
                )
                self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt)
            else:
                delay = int(min(900, base_delay_seconds * (2 ** max(0, delivery.attempts - 1))))
                self.sqs.change_message_visibility(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=receipt,
                    VisibilityTimeout=delay,
                )
            return dead
        except ClientError as aws_error:
            raise self._aws_error("retry or dead-letter task", aws_error) from aws_error

    def record_event(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        created = utc_now()
        event_id = f"{created.isoformat()}#{uuid.uuid4().hex}"
        try:
            self.table.put_item(Item={
                "pk": self._workflow_pk(task_id),
                "sk": f"EVENT#{event_id}",
                "entity_type": "EVENT",
                "event_id": event_id,
                "task_id": task_id,
                "event_type": event_type,
                "payload_json": json.dumps(payload or {}, default=str),
                "created_at": created.isoformat(),
            })
        except ClientError as error:
            raise self._aws_error("record audit event", error) from error

    def list_events(self, task_id: str) -> list[EventRecord]:
        try:
            response = self.table.query(
                KeyConditionExpression=Key("pk").eq(self._workflow_pk(task_id)) & Key("sk").begins_with("EVENT#"),
                ConsistentRead=True,
            )
        except ClientError as error:
            raise self._aws_error("list audit events", error) from error
        return [
            EventRecord(
                id=item["event_id"],
                task_id=task_id,
                event_type=item["event_type"],
                payload=json.loads(item["payload_json"]),
                created_at=datetime.fromisoformat(item["created_at"]),
            )
            for item in response.get("Items", [])
        ]

    def get_tool_result(self, idempotency_key: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        key = {"pk": f"IDEMPOTENCY#{idempotency_key}", "sk": "RESULT"}
        try:
            item = self.table.get_item(Key=key, ConsistentRead=True).get("Item")
        except ClientError as error:
            raise self._aws_error("read idempotency result", error) from error
        if not item:
            return None
        if item["arguments_hash"] != self.arguments_hash(arguments):
            raise ValueError("idempotency key reused with different arguments")
        return json.loads(item["result_json"])

    def save_tool_result(self, key: str, task_id: str, tool: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        try:
            self.table.put_item(
                Item={
                    "pk": f"IDEMPOTENCY#{key}",
                    "sk": "RESULT",
                    "entity_type": "TOOL_RESULT",
                    "task_id": task_id,
                    "tool_name": tool,
                    "arguments_hash": self.arguments_hash(arguments),
                    "result_json": json.dumps(result, default=str),
                    "created_at": utc_now().isoformat(),
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise self._aws_error("save idempotency result", error) from error

    def create_approval(self, task_id: str, action_id: str) -> None:
        try:
            self.table.put_item(
                Item={
                    "pk": self._workflow_pk(task_id),
                    "sk": f"APPROVAL#{action_id}",
                    "entity_type": "APPROVAL",
                    "status": "PENDING",
                    "created_at": utc_now().isoformat(),
                },
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise self._aws_error("create approval", error) from error

    def resolve_approval(self, task_id: str, action_id: str, approved: bool, actor: str, reason: str | None) -> None:
        try:
            self.table.update_item(
                Key={"pk": self._workflow_pk(task_id), "sk": f"APPROVAL#{action_id}"},
                UpdateExpression="SET #status=:status, actor=:actor, reason=:reason, resolved_at=:resolved",
                ConditionExpression="#status=:pending",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":status": "APPROVED" if approved else "REJECTED",
                    ":pending": "PENDING",
                    ":actor": actor,
                    ":reason": reason or "",
                    ":resolved": utc_now().isoformat(),
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError("approval is not pending") from error
            raise self._aws_error("resolve approval", error) from error

    def approval_status(self, task_id: str, action_id: str) -> str | None:
        try:
            item = self.table.get_item(
                Key={"pk": self._workflow_pk(task_id), "sk": f"APPROVAL#{action_id}"},
                ConsistentRead=True,
            ).get("Item")
        except ClientError as error:
            raise self._aws_error("read approval", error) from error
        return str(item["status"]) if item else None

    def queue_counts(self) -> dict[str, int]:
        try:
            main = self.sqs.get_queue_attributes(
                QueueUrl=self.queue_url,
                AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
            )["Attributes"]
            counts = {
                "PENDING": int(main.get("ApproximateNumberOfMessages", 0)),
                "PROCESSING": int(main.get("ApproximateNumberOfMessagesNotVisible", 0)),
            }
            if self.dlq_url:
                dlq = self.sqs.get_queue_attributes(
                    QueueUrl=self.dlq_url,
                    AttributeNames=["ApproximateNumberOfMessages"],
                )["Attributes"]
                counts["DEAD"] = int(dlq.get("ApproximateNumberOfMessages", 0))
            return counts
        except ClientError as error:
            raise self._aws_error("read queue depth", error) from error

    def list_dead_letters(self, limit: int = 25) -> list[DeadLetterRecord]:
        if not self.dlq_url:
            return []
        try:
            response = self.sqs.receive_message(
                QueueUrl=self.dlq_url,
                MaxNumberOfMessages=max(1, min(limit, 10)),
                VisibilityTimeout=0,
                WaitTimeSeconds=0,
                MessageSystemAttributeNames=["ApproximateReceiveCount", "SentTimestamp"],
            )
        except ClientError as error:
            raise self._aws_error("list dead letters", error) from error
        records = []
        for message in response.get("Messages", []):
            body = json.loads(message["Body"])
            attributes = message.get("Attributes", {})
            sent_at = datetime.fromtimestamp(int(attributes.get("SentTimestamp", "0")) / 1_000, tz=UTC)
            records.append(DeadLetterRecord(
                id=message["ReceiptHandle"],
                task_id=body["task_id"],
                attempts=int(body.get("attempts", attributes.get("ApproximateReceiveCount", 1))),
                max_attempts=int(body.get("max_attempts", 5)),
                last_error=body.get("error"),
                payload=body.get("payload", {}),
                updated_at=sent_at,
            ))
        return records

    def replay_dead_letter(
        self, delivery_id: int | str, task_id: str, max_attempts: int, actor: str, reason: str
    ) -> WorkflowState | None:
        if not self.dlq_url:
            return None
        receipt = str(delivery_id)
        state = self.get_workflow(task_id)
        if state is None:
            return None
        state.status = WorkflowStatus.QUEUED
        state.last_error = None
        state = self.save_workflow(state, state.version)
        try:
            self.sqs.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps({"task_id": task_id, "max_attempts": max_attempts, "payload": {}}),
            )
            self.sqs.delete_message(QueueUrl=self.dlq_url, ReceiptHandle=receipt)
        except ClientError as error:
            raise self._aws_error("replay dead letter", error) from error
        self.record_event(task_id, "DLQ_REPLAYED", {"actor": actor, "reason": reason})
        return state

    @staticmethod
    def _aws_error(operation: str, error: ClientError) -> RetryableWorkflowError | PermanentWorkflowError:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        retryable = {
            "InternalError", "InternalServerError", "ProvisionedThroughputExceededException",
            "RequestLimitExceeded", "RequestThrottled", "ServiceUnavailable", "ThrottlingException",
        }
        error_type = RetryableWorkflowError if code in retryable or code.startswith("Throttl") else PermanentWorkflowError
        return error_type(f"AWS {operation} failed ({code})")
