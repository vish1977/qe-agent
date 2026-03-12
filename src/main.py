#!/usr/bin/env python3
"""
QE Agent — entry point

Usage:
  # Webhook server mode (recommended for production)
  python -m src.main --serve

  # Run the full pipeline on a PR event (demo mode)
  python -m src.main --demo

  # Trigger from a GitHub webhook payload file
  python -m src.main --webhook path/to/payload.json --source github

  # Watch for events from Kafka
  python -m src.main --kafka

  # Process a specific PR
  python -m src.main --pr https://github.com/owner/repo/pull/123
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .config import config
from .models import EventType, ProductEvent
from .orchestrator import QEOrchestrator
from .streaming.consumer import inject_event, parse_github_webhook, parse_jira_webhook, stream_events

console = Console()


def build_pr_event(pr_url: str) -> ProductEvent:
    """Build a ProductEvent from a PR URL."""
    # Parse https://github.com/owner/repo/pull/123
    parts = pr_url.rstrip("/").split("/")
    if "pull" in parts:
        idx = parts.index("pull")
        repo = "/".join(parts[idx - 2: idx])
        pr_number = int(parts[idx + 1])
    else:
        repo = None
        pr_number = None

    return ProductEvent(
        event_id=str(uuid.uuid4()),
        event_type=EventType.PR_OPENED,
        source="github",
        repo=repo,
        pr_url=pr_url,
        pr_number=pr_number,
        timestamp=datetime.utcnow(),
    )


def demo_event() -> ProductEvent:
    """Generate a demo ProductEvent for testing without real integrations."""
    return ProductEvent(
        event_id=str(uuid.uuid4()),
        event_type=EventType.PR_OPENED,
        source="github",
        repo="acme/backend",
        pr_url="https://github.com/acme/backend/pull/42",
        pr_number=42,
        commit_sha="abc1234",
        branch="feat/stripe-checkout",
        jira_ticket="ACME-567",
        timestamp=datetime.utcnow(),
        payload={
            "title": "feat: add Stripe payment integration",
            "author": "dev-alice",
            "base_branch": "main",
            "changed_files": [
                "src/payments/stripe.py",
                "src/payments/__init__.py",
                "tests/payments/test_stripe.py",
            ],
        },
    )


async def run_pipeline(
    event: ProductEvent,
    repo: str | None = None,
    repo_path: str | None = None,
    use_device_farm: bool = False,
) -> None:
    orchestrator = QEOrchestrator(
        repo=repo or event.repo,
        repo_path=repo_path,
        use_device_farm=use_device_farm,
    )
    state = await orchestrator.run(event)

    # Write state to disk for inspection
    output_path = Path(f"qe_run_{state.event.event_id[:8]}.json")
    output_path.write_text(state.model_dump_json(indent=2, default=str))
    console.print(f"\n[dim]Full pipeline state saved to {output_path}[/dim]")


async def serve_webhooks(host: str, port: int, ngrok: bool = False, **kwargs) -> None:
    """Start the webhook HTTP server and process incoming GitHub/Jira events."""
    from .streaming.webhook_server import start_server

    async def handle(event: ProductEvent) -> None:
        await run_pipeline(event, **kwargs)

    runner = await start_server(host=host, port=port, pipeline_runner=handle)

    if ngrok:
        from .streaming.ngrok_tunnel import open_tunnel, close_tunnel
        try:
            open_tunnel(port)
        except Exception:
            await runner.cleanup()
            return

    try:
        await asyncio.Event().wait()  # run forever
    finally:
        if ngrok:
            from .streaming.ngrok_tunnel import close_tunnel
            close_tunnel()
        await runner.cleanup()


async def watch_kafka(bootstrap_servers: str, topic: str, **kwargs) -> None:
    """Consume events from Kafka and run the QE pipeline for each."""
    from .streaming.consumer import start_kafka_consumer

    async def handle_event(event: ProductEvent) -> None:
        await run_pipeline(event, **kwargs)

    console.print(f"[bold]Watching Kafka[/bold]: {bootstrap_servers} / {topic}")
    await start_kafka_consumer(bootstrap_servers, topic, handle_event)


def main() -> None:
    parser = argparse.ArgumentParser(description="QE Agent — automated quality engineering pipeline")
    parser.add_argument("--pr", help="GitHub PR URL to analyze, e.g. https://github.com/owner/repo/pull/1")
    parser.add_argument("--webhook", help="Path to a JSON webhook payload file")
    parser.add_argument("--source", choices=["github", "jira"], default="github")
    parser.add_argument("--kafka", action="store_true", help="Start Kafka consumer loop")
    parser.add_argument("--repo", help="GitHub repo (owner/name) override")
    parser.add_argument("--repo-path", help="Local filesystem path to the repo")
    parser.add_argument("--device-farm", action="store_true", help="Use device farm for test execution")
    parser.add_argument("--serve", action="store_true", help="Start webhook server to receive GitHub/Jira events")
    parser.add_argument("--host", default=None, help="Webhook server host (default: WEBHOOK_HOST env var or 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Webhook server port (default: WEBHOOK_PORT env var or 8080)")
    parser.add_argument("--ngrok", action="store_true", help="Open an ngrok tunnel for local development (requires --serve)")
    parser.add_argument("--demo", action="store_true", help="Run with a demo event (no real integrations needed)")
    args = parser.parse_args()

    if not config.ANTHROPIC_API_KEY:
        console.print("[bold red]Error:[/bold red] ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
        sys.exit(1)

    kwargs = {
        "repo": args.repo,
        "repo_path": args.repo_path,
        "use_device_farm": args.device_farm,
    }

    if args.serve:
        asyncio.run(serve_webhooks(
            host=args.host or config.WEBHOOK_HOST,
            port=args.port or config.WEBHOOK_PORT,
            ngrok=args.ngrok,
            **kwargs,
        ))
        return

    if args.ngrok:
        console.print("[yellow]--ngrok requires --serve[/yellow]")
        sys.exit(1)

    if args.kafka:
        asyncio.run(watch_kafka(config.KAFKA_BOOTSTRAP_SERVERS, config.KAFKA_TOPIC, **kwargs))
        return

    if args.webhook:
        payload = json.loads(Path(args.webhook).read_text())
        event = (
            parse_github_webhook(payload)
            if args.source == "github"
            else parse_jira_webhook(payload)
        )
        if not event:
            console.print("[red]Could not parse webhook payload into a ProductEvent[/red]")
            sys.exit(1)
    elif args.pr:
        event = build_pr_event(args.pr)
    elif args.demo or True:  # default to demo mode
        console.print("[dim]No event specified — running in demo mode[/dim]")
        event = demo_event()

    asyncio.run(run_pipeline(event, **kwargs))


if __name__ == "__main__":
    main()
