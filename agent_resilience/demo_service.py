from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import AsyncIterator

import boto3
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse


logger = logging.getLogger("checkout-demo")


class CheckoutDemo:
    """Small observable workload whose process-local failure is cleared by an ECS restart."""

    def __init__(
        self,
        service: str = "checkout-service",
        environment: str = "production",
        fail_after_seconds: float = 180.0,
        metric_interval_seconds: float = 10.0,
        health_reports_failure: bool = True,
    ):
        self.service = service
        self.environment = environment
        self.fail_after_seconds = fail_after_seconds
        self.metric_interval_seconds = max(1.0, metric_interval_seconds)
        self.health_reports_failure = health_reports_failure
        self.started_at = time.monotonic()

    @property
    def unhealthy(self) -> bool:
        return self.fail_after_seconds > 0 and time.monotonic() - self.started_at >= self.fail_after_seconds

    def sample(self) -> dict[str, float | str | bool]:
        unhealthy = self.unhealthy
        return {
            "service": self.service,
            "environment": self.environment,
            "unhealthy": unhealthy,
            "error_rate_percent": 12.5 if unhealthy else 0.2,
            "p95_latency_ms": 1_800.0 if unhealthy else 120.0,
        }


def create_demo_app(demo: CheckoutDemo | None = None) -> FastAPI:
    workload = demo or CheckoutDemo(
        service=os.getenv("DEMO_SERVICE_NAME", "checkout-service"),
        environment=os.getenv("DEMO_ENVIRONMENT", "production"),
        fail_after_seconds=float(os.getenv("DEMO_FAIL_AFTER_SECONDS", "180")),
        metric_interval_seconds=float(os.getenv("DEMO_METRIC_INTERVAL_SECONDS", "10")),
        health_reports_failure=os.getenv("DEMO_HEALTH_REPORTS_FAILURE", "true").lower() == "true",
    )
    publisher_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal publisher_task
        publisher_task = asyncio.create_task(_publish_samples(workload))
        yield
        publisher_task.cancel()
        with suppress(asyncio.CancelledError):
            await publisher_task

    app = FastAPI(title="AgentResilience checkout demo", version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> JSONResponse:
        sample = workload.sample()
        status = 503 if sample["unhealthy"] and workload.health_reports_failure else 200
        return JSONResponse(sample, status_code=status)

    @app.get("/checkout")
    async def checkout() -> JSONResponse:
        sample = workload.sample()
        if sample["unhealthy"]:
            return JSONResponse(
                {"error": "database connection pool exhausted", "retryable": True},
                status_code=503,
            )
        return JSONResponse({"checkout_id": "demo-order", "status": "accepted"})

    app.state.demo = workload
    return app


async def _publish_samples(workload: CheckoutDemo) -> None:
    cloudwatch = _cloudwatch_client()
    namespace = os.getenv("SERVICE_METRIC_NAMESPACE", "AgentResilience/Services")
    while True:
        sample = workload.sample()
        level = logging.ERROR if sample["unhealthy"] else logging.INFO
        message = (
            "database connection pool exhausted; checkout requests returning 503"
            if sample["unhealthy"]
            else "checkout service healthy"
        )
        logger.log(level, json.dumps({"level": logging.getLevelName(level), "message": message, **sample}, sort_keys=True))
        if cloudwatch is not None:
            try:
                dimensions = [
                    {"Name": "ServiceName", "Value": workload.service},
                    {"Name": "Environment", "Value": workload.environment},
                ]
                cloudwatch.put_metric_data(
                    Namespace=namespace,
                    MetricData=[
                        {
                            "MetricName": "ErrorRate",
                            "Value": sample["error_rate_percent"],
                            "Unit": "Percent",
                            "Timestamp": datetime.now(UTC),
                            "StorageResolution": 1,
                            "Dimensions": dimensions,
                        },
                        {
                            "MetricName": "Latency",
                            "Value": sample["p95_latency_ms"],
                            "Unit": "Milliseconds",
                            "Timestamp": datetime.now(UTC),
                            "StorageResolution": 1,
                            "Dimensions": dimensions,
                        },
                    ],
                )
            except Exception as error:
                logger.warning("CloudWatch metric publication failed: %s", type(error).__name__)
        await asyncio.sleep(workload.metric_interval_seconds)


def _cloudwatch_client():
    if os.getenv("CLOUDWATCH_METRICS_ENABLED", "false").lower() != "true":
        return None
    options = {}
    endpoint = os.getenv("AWS_ENDPOINT_URL")
    if endpoint:
        options["endpoint_url"] = endpoint
    return boto3.client("cloudwatch", region_name=os.getenv("AWS_REGION", "us-east-1"), **options)


def run() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
    uvicorn.run(
        "agent_resilience.demo_service:create_demo_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
    )
