from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from .errors import ConcurrentUpdateError
from .models import EventRecord, QueueDelivery, WorkflowState, WorkflowStatus, utc_now


class SQLiteStore:
    """SQLite-backed checkpoints, durable queue, idempotency records, and event store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.initialize()

    def _connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            self._local.connection = connection
        return connection

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield connection
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def initialize(self) -> None:
        connection = self._connection()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                task_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES workflows(task_id),
                status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','COMPLETED','DEAD')),
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL,
                available_at TEXT NOT NULL,
                lease_until TEXT,
                worker_id TEXT,
                payload_json TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_queue_claim
                ON queue(status, available_at, lease_until, id);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);
            CREATE TABLE IF NOT EXISTS tool_results (
                idempotency_key TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_hash TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
                task_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
                actor TEXT,
                reason TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                PRIMARY KEY(task_id, action_id)
            );
            """
        )

    def close(self) -> None:
        """Release this thread's SQLite handle (important for clean shutdown on Windows)."""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            del self._local.connection

    def create_workflow(self, state: WorkflowState, max_attempts: int) -> WorkflowState:
        now = utc_now().isoformat()
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO workflows(task_id,state_json,version,updated_at) VALUES(?,?,?,?)",
                (state.task_id, state.model_dump_json(), state.version, now),
            )
            connection.execute(
                """INSERT INTO queue(task_id,status,attempts,max_attempts,available_at,payload_json,created_at,updated_at)
                   VALUES(?, 'PENDING', 0, ?, ?, '{}', ?, ?)""",
                (state.task_id, max_attempts, now, now, now),
            )
            self._record_event(connection, state.task_id, "WORKFLOW_CREATED", {"goal": state.goal})
        return state

    def get_workflow(self, task_id: str) -> WorkflowState | None:
        row = self._connection().execute(
            "SELECT state_json FROM workflows WHERE task_id=?", (task_id,)
        ).fetchone()
        return WorkflowState.model_validate_json(row["state_json"]) if row else None

    def save_workflow(self, state: WorkflowState, expected_version: int) -> WorkflowState:
        state.version = expected_version + 1
        state.updated_at = utc_now()
        with self.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """UPDATE workflows SET state_json=?,version=?,updated_at=?
                   WHERE task_id=? AND version=?""",
                (state.model_dump_json(), state.version, state.updated_at.isoformat(), state.task_id, expected_version),
            )
            if cursor.rowcount != 1:
                state.version = expected_version
                raise ConcurrentUpdateError(f"stale checkpoint for {state.task_id}")
        return state

    def enqueue(self, task_id: str, max_attempts: int, payload: dict[str, Any] | None = None) -> int:
        now = utc_now().isoformat()
        with self.transaction(immediate=True) as connection:
            active = connection.execute(
                "SELECT id FROM queue WHERE task_id=? AND status IN ('PENDING','PROCESSING') LIMIT 1",
                (task_id,),
            ).fetchone()
            if active:
                return int(active["id"])
            cursor = connection.execute(
                """INSERT INTO queue(task_id,status,attempts,max_attempts,available_at,payload_json,created_at,updated_at)
                   VALUES(?, 'PENDING', 0, ?, ?, ?, ?, ?)""",
                (task_id, max_attempts, now, json.dumps(payload or {}), now, now),
            )
            return int(cursor.lastrowid)

    def claim(self, worker_id: str, lease_seconds: int) -> QueueDelivery | None:
        now = utc_now()
        now_text = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE queue SET status='PENDING',worker_id=NULL,lease_until=NULL,updated_at=?
                   WHERE status='PROCESSING' AND lease_until < ?""",
                (now_text, now_text),
            )
            row = connection.execute(
                """SELECT * FROM queue WHERE status='PENDING' AND available_at <= ?
                   ORDER BY id LIMIT 1""",
                (now_text,),
            ).fetchone()
            if not row:
                return None
            attempts = int(row["attempts"]) + 1
            connection.execute(
                """UPDATE queue SET status='PROCESSING',attempts=?,worker_id=?,lease_until=?,updated_at=?
                   WHERE id=? AND status='PENDING'""",
                (attempts, worker_id, lease_until, now_text, row["id"]),
            )
            return QueueDelivery(
                id=row["id"], task_id=row["task_id"], attempts=attempts,
                max_attempts=row["max_attempts"], payload=json.loads(row["payload_json"]),
            )

    def acknowledge(self, delivery_id: int) -> None:
        now = utc_now().isoformat()
        self._connection().execute(
            "UPDATE queue SET status='COMPLETED',lease_until=NULL,updated_at=? WHERE id=?",
            (now, delivery_id),
        )

    def retry_or_dead_letter(self, delivery: QueueDelivery, error: str, base_delay_seconds: float = 1.0) -> bool:
        now = utc_now()
        dead = delivery.attempts >= delivery.max_attempts
        status = "DEAD" if dead else "PENDING"
        delay = min(60.0, base_delay_seconds * (2 ** max(0, delivery.attempts - 1)))
        available = now if dead else now + timedelta(seconds=delay)
        self._connection().execute(
            """UPDATE queue SET status=?,available_at=?,lease_until=NULL,worker_id=NULL,last_error=?,updated_at=?
               WHERE id=?""",
            (status, available.isoformat(), error[:2_000], now.isoformat(), delivery.id),
        )
        return dead

    def record_event(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        with self.transaction(immediate=True) as connection:
            self._record_event(connection, task_id, event_type, payload or {})

    def _record_event(self, connection: sqlite3.Connection, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO events(task_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (task_id, event_type, json.dumps(payload, default=str), utc_now().isoformat()),
        )

    def list_events(self, task_id: str) -> list[EventRecord]:
        rows = self._connection().execute(
            "SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,)
        ).fetchall()
        return [
            EventRecord(id=row["id"], task_id=row["task_id"], event_type=row["event_type"],
                        payload=json.loads(row["payload_json"]), created_at=datetime.fromisoformat(row["created_at"]))
            for row in rows
        ]

    @staticmethod
    def arguments_hash(arguments: dict[str, Any]) -> str:
        canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def get_tool_result(self, idempotency_key: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        row = self._connection().execute(
            "SELECT arguments_hash,result_json FROM tool_results WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if not row:
            return None
        if row["arguments_hash"] != self.arguments_hash(arguments):
            raise ValueError("idempotency key reused with different arguments")
        return json.loads(row["result_json"])

    def save_tool_result(self, key: str, task_id: str, tool: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        self._connection().execute(
            """INSERT OR IGNORE INTO tool_results(idempotency_key,task_id,tool_name,arguments_hash,result_json,created_at)
               VALUES(?,?,?,?,?,?)""",
            (key, task_id, tool, self.arguments_hash(arguments), json.dumps(result), utc_now().isoformat()),
        )

    def create_approval(self, task_id: str, action_id: str) -> None:
        self._connection().execute(
            """INSERT OR IGNORE INTO approvals(task_id,action_id,status,created_at)
               VALUES(?,?,'PENDING',?)""",
            (task_id, action_id, utc_now().isoformat()),
        )

    def resolve_approval(self, task_id: str, action_id: str, approved: bool, actor: str, reason: str | None) -> None:
        cursor = self._connection().execute(
            """UPDATE approvals SET status=?,actor=?,reason=?,resolved_at=?
               WHERE task_id=? AND action_id=? AND status='PENDING'""",
            ("APPROVED" if approved else "REJECTED", actor, reason, utc_now().isoformat(), task_id, action_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("approval is not pending")

    def approval_status(self, task_id: str, action_id: str) -> str | None:
        row = self._connection().execute(
            "SELECT status FROM approvals WHERE task_id=? AND action_id=?", (task_id, action_id)
        ).fetchone()
        return str(row["status"]) if row else None

    def queue_counts(self) -> dict[str, int]:
        rows = self._connection().execute("SELECT status,COUNT(*) AS count FROM queue GROUP BY status").fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}
