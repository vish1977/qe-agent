"""
Event Consumer — receives ProductEvents from Kafka, webhooks, or direct injection.

In production, wire this to your Kafka / GCP Pub/Sub consumer.
For local development and testing, use inject_event() directly.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import AsyncIterator, Callable, Awaitable

from rich.console import Console

from ..models import EventType, ProductEvent

console = Console()

# ── In-process event bus (dev/test) ─────────────────────────────────────────

_event_queue: asyncio.Queue[ProductEvent] = asyncio.Queue()


async def inject_event(event: ProductEvent) -> None:
    """Push an event directly into the queue (for testing and webhooks)."""
    await _event_queue.put(event)


async def stream_events() -> AsyncIterator[ProductEvent]:
    """Yield events as they arrive."""
    while True:
        event = await _event_queue.get()
        yield event


# ── Webhook helpers ──────────────────────────────────────────────────────────

def parse_github_webhook(payload: dict) -> ProductEvent | None:
    """Convert a raw GitHub webhook payload to a ProductEvent."""
    action = payload.get("action")
    pr = payload.get("pull_request")

    if pr:
        event_type_map = {
            "opened": EventType.PR_OPENED,
            "synchronize": EventType.PR_UPDATED,
            "closed": EventType.PR_MERGED if pr.get("merged") else None,
        }
        et = event_type_map.get(action)
        if not et:
            return None

        repo_full = payload.get("repository", {}).get("full_name", "")
        return ProductEvent(
            event_id=str(uuid.uuid4()),
            event_type=et,
            source="github",
            repo=repo_full,
            pr_url=pr.get("html_url"),
            pr_number=pr.get("number"),
            commit_sha=pr.get("head", {}).get("sha"),
            branch=pr.get("head", {}).get("ref"),
            payload=payload,
        )

    # CI status event
    if "check_run" in payload or "workflow_run" in payload:
        conclusion = (
            payload.get("check_run", {}).get("conclusion")
            or payload.get("workflow_run", {}).get("conclusion")
        )
        et = EventType.CI_FAILED if conclusion == "failure" else EventType.CI_PASSED
        repo_full = payload.get("repository", {}).get("full_name", "")
        return ProductEvent(
            event_id=str(uuid.uuid4()),
            event_type=et,
            source="github",
            repo=repo_full,
            payload=payload,
        )

    return None


def parse_jira_webhook(payload: dict) -> ProductEvent | None:
    """Convert a raw Jira webhook payload to a ProductEvent."""
    issue = payload.get("issue", {})
    if not issue:
        return None

    return ProductEvent(
        event_id=str(uuid.uuid4()),
        event_type=EventType.JIRA_UPDATED,
        source="jira",
        jira_ticket=issue.get("key"),
        payload=payload,
    )


# ── Kafka consumer (optional) ────────────────────────────────────────────────

async def start_kafka_consumer(
    bootstrap_servers: str,
    topic: str,
    on_event: Callable[[ProductEvent], Awaitable[None]],
) -> None:
    """
    Start a Kafka consumer loop.
    Requires kafka-python: pip install kafka-python-ng
    """
    try:
        from kafka import KafkaConsumer  # type: ignore
    except ImportError:
        console.print("[yellow]kafka-python-ng not installed. Kafka consumer disabled.[/yellow]")
        return

    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="latest",
        enable_auto_commit=True,
        group_id="qe-agent",
    )

    console.print(f"[green]Kafka consumer started[/green] → topic: {topic}")

    loop = asyncio.get_event_loop()
    for msg in consumer:
        try:
            event = ProductEvent(**msg.value)
            await on_event(event)
        except Exception as e:
            console.print(f"[red]Failed to process Kafka message: {e}[/red]")
