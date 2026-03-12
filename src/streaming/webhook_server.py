"""
Webhook Server — receives GitHub (and optionally Jira) webhook HTTP requests.

Signature verification flow:
  GitHub sends X-Hub-Signature-256: sha256=<hmac>
  We compute HMAC-SHA256(secret, raw_body) and compare in constant time.

Routes:
  POST /webhook/github   — GitHub events (pull_request, check_run, workflow_run, push, release)
  POST /webhook/jira     — Jira issue / project events
  GET  /health           — liveness probe
  GET  /events           — SSE stream of processed event IDs (optional monitoring)

The server immediately returns 202 Accepted and processes the pipeline in the background,
so GitHub's 10-second delivery timeout is never hit.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from aiohttp import web
from rich.console import Console

from ..config import config
from ..models import ProductEvent
from .consumer import inject_event, parse_github_webhook, parse_jira_webhook

console = Console()

# Ring buffer of the last 200 processed event IDs for the /events SSE endpoint
_recent_events: deque[dict[str, Any]] = deque(maxlen=200)
# SSE subscriber queues
_sse_queues: list[asyncio.Queue] = []


# ── Signature helpers ─────────────────────────────────────────────────────────

def _verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """
    Verify X-Hub-Signature-256 in constant time.
    Returns True if valid, False otherwise (including missing secret).
    """
    if not secret:
        # No secret configured — skip verification (not recommended for production)
        console.print("[yellow]⚠ GITHUB_WEBHOOK_SECRET not set — skipping signature check[/yellow]")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _verify_jira_secret(token: str | None, header_token: str | None) -> bool:
    """Jira sends a shared secret in a custom header."""
    if not token:
        return True  # no secret configured
    if not header_token:
        return False
    return hmac.compare_digest(token, header_token)


# ── SSE broadcast ─────────────────────────────────────────────────────────────

async def _broadcast(event_data: dict) -> None:
    _recent_events.append(event_data)
    for q in list(_sse_queues):
        try:
            q.put_nowait(event_data)
        except asyncio.QueueFull:
            pass


# ── Route handlers ────────────────────────────────────────────────────────────

async def handle_github(request: web.Request) -> web.Response:
    body = await request.read()

    # Verify signature
    sig = request.headers.get("X-Hub-Signature-256")
    if not _verify_github_signature(config.GITHUB_WEBHOOK_SECRET, body, sig):
        console.print("[red]GitHub webhook: invalid signature[/red]")
        return web.Response(status=401, text="Invalid signature")

    gh_event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON")

    console.print(
        f"[bold cyan]↓ GitHub webhook[/bold cyan] "
        f"event=[yellow]{gh_event}[/yellow] delivery={delivery_id[:8] if delivery_id else '?'}"
    )

    event = parse_github_webhook(payload)

    if event is None:
        # Unsupported event type — acknowledge but don't process
        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps({"accepted": False, "reason": f"unhandled event type: {gh_event}"}),
        )

    # Enqueue — pipeline runs in the background
    await inject_event(event)

    entry = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "repo": event.repo,
        "delivery_id": delivery_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    await _broadcast(entry)

    return web.Response(
        status=202,
        content_type="application/json",
        text=json.dumps({"accepted": True, "event_id": event.event_id}),
    )


async def handle_jira(request: web.Request) -> web.Response:
    body = await request.read()

    # Jira uses a shared-secret header (configurable)
    secret_header = request.headers.get("X-Jira-Webhook-Secret") or request.headers.get("Authorization")
    if not _verify_jira_secret(config.JIRA_WEBHOOK_SECRET, secret_header):
        return web.Response(status=401, text="Invalid secret")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON")

    event = parse_jira_webhook(payload)
    if event is None:
        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps({"accepted": False, "reason": "unhandled jira event"}),
        )

    console.print(
        f"[bold cyan]↓ Jira webhook[/bold cyan] ticket=[yellow]{event.jira_ticket}[/yellow]"
    )
    await inject_event(event)

    entry = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "jira_ticket": event.jira_ticket,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    await _broadcast(entry)

    return web.Response(
        status=202,
        content_type="application/json",
        text=json.dumps({"accepted": True, "event_id": event.event_id}),
    )


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "recent_events": len(_recent_events),
        }),
    )


async def handle_sse(request: web.Request) -> web.StreamResponse:
    """
    Server-Sent Events stream — each processed webhook is pushed here.
    Useful for monitoring dashboards without polling.
    """
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await response.prepare(request)

    # Send buffered recent events first
    for entry in _recent_events:
        await response.write(f"data: {json.dumps(entry)}\n\n".encode())

    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_queues.append(q)
    try:
        while True:
            try:
                entry = await asyncio.wait_for(q.get(), timeout=25.0)
                await response.write(f"data: {json.dumps(entry)}\n\n".encode())
            except asyncio.TimeoutError:
                # Keepalive comment
                await response.write(b": keepalive\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        _sse_queues.remove(q)

    return response


# ── Server factory ────────────────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/webhook/github", handle_github)
    app.router.add_post("/webhook/jira", handle_jira)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/events", handle_sse)
    return app


async def start_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    pipeline_runner: Callable[[ProductEvent], Awaitable[None]] | None = None,
) -> web.AppRunner:
    """
    Start the webhook HTTP server.

    If pipeline_runner is provided, events are automatically dispatched to it.
    Otherwise events accumulate in the queue for the caller to consume.
    """
    app = create_app()

    if pipeline_runner is not None:
        # Background task: drain the queue and run the pipeline
        async def _drain() -> None:
            from .consumer import stream_events
            async for event in stream_events():
                asyncio.create_task(pipeline_runner(event))

        app.on_startup.append(lambda _: asyncio.ensure_future(_drain()))

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    console.print(
        f"[bold green]Webhook server running[/bold green] → "
        f"http://{host}:{port}\n"
        f"  POST /webhook/github   (X-Hub-Signature-256 verified)\n"
        f"  POST /webhook/jira\n"
        f"  GET  /health\n"
        f"  GET  /events           (SSE stream)\n"
    )
    return runner
