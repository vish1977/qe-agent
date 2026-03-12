"""
Webhook server tests.

Tests the HTTP layer only — no Claude API calls are made.
The pipeline runner is replaced with a mock that captures received events.

Run:
  cd qe-agent
  pip install pytest pytest-asyncio aiohttp
  pytest tests/test_webhook.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

FIXTURES = Path(__file__).parent / "fixtures"
TEST_SECRET = "test-webhook-secret-abc123"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def sign(body: bytes, secret: str = TEST_SECRET) -> str:
    """Compute X-Hub-Signature-256 value for a body."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _make_app_with_secret(secret: str):
    """Import and create the aiohttp app with a patched webhook secret."""
    # Import inside the patch so the module sees the mocked config
    from src.streaming.webhook_server import create_app
    return create_app()


# ── Health ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint():
    """GET /health returns 200 with status=ok."""
    with patch("src.streaming.webhook_server.config") as cfg:
        cfg.GITHUB_WEBHOOK_SECRET = TEST_SECRET
        cfg.JIRA_WEBHOOK_SECRET = ""
        app = _make_app_with_secret(TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/health")
            assert resp.status == 200
            data = await resp.json()

    assert data["status"] == "ok"
    assert "timestamp" in data


# ── PR opened ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pr_opened_valid_signature():
    """
    POST /webhook/github with a valid PR-opened payload + correct HMAC
    returns 202 and injects a ProductEvent with the right fields.
    """
    payload = load_fixture("github_pr_opened.json")
    body = json.dumps(payload).encode()

    received: list = []

    async def fake_inject(event):
        received.append(event)

    with patch("src.streaming.webhook_server.config") as cfg, \
         patch("src.streaming.webhook_server.inject_event", new=fake_inject):
        cfg.GITHUB_WEBHOOK_SECRET = TEST_SECRET
        cfg.JIRA_WEBHOOK_SECRET = ""
        app = _make_app_with_secret(TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "abc-delivery-001",
                    "X-Hub-Signature-256": sign(body),
                },
            )
            assert resp.status == 202
            data = await resp.json()

    assert data["accepted"] is True
    assert "event_id" in data

    assert len(received) == 1
    event = received[0]
    assert event.event_type == "pr_opened"
    assert event.repo == "acme/backend"
    assert event.pr_number == 42
    assert event.branch == "feat/stripe-checkout"
    assert event.commit_sha == "abc1234def5678"


@pytest.mark.asyncio
async def test_pr_opened_invalid_signature():
    """Wrong HMAC signature → 401, event NOT injected."""
    payload = load_fixture("github_pr_opened.json")
    body = json.dumps(payload).encode()

    received: list = []

    async def fake_inject(event):
        received.append(event)

    with patch("src.streaming.webhook_server.config") as cfg, \
         patch("src.streaming.webhook_server.inject_event", new=fake_inject):
        cfg.GITHUB_WEBHOOK_SECRET = TEST_SECRET
        cfg.JIRA_WEBHOOK_SECRET = ""
        app = _make_app_with_secret(TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "pull_request",
                    "X-Hub-Signature-256": "sha256=badsignature",
                },
            )
            assert resp.status == 401

    assert len(received) == 0


@pytest.mark.asyncio
async def test_pr_opened_missing_signature():
    """No signature header → 401 when secret is configured."""
    payload = load_fixture("github_pr_opened.json")
    body = json.dumps(payload).encode()

    with patch("src.streaming.webhook_server.config") as cfg:
        cfg.GITHUB_WEBHOOK_SECRET = TEST_SECRET
        cfg.JIRA_WEBHOOK_SECRET = ""
        app = _make_app_with_secret(TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=body,
                headers={"Content-Type": "application/json", "X-GitHub-Event": "pull_request"},
            )
            assert resp.status == 401


# ── CI check run ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_run_failed_event():
    """CI failure check_run payload is parsed as ci_failed event."""
    payload = load_fixture("github_check_run_failed.json")
    body = json.dumps(payload).encode()

    received: list = []

    async def fake_inject(event):
        received.append(event)

    with patch("src.streaming.webhook_server.config") as cfg, \
         patch("src.streaming.webhook_server.inject_event", new=fake_inject):
        cfg.GITHUB_WEBHOOK_SECRET = TEST_SECRET
        cfg.JIRA_WEBHOOK_SECRET = ""
        app = _make_app_with_secret(TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "check_run",
                    "X-GitHub-Delivery": "abc-delivery-002",
                    "X-Hub-Signature-256": sign(body),
                },
            )
            assert resp.status == 202
            data = await resp.json()

    assert data["accepted"] is True
    assert len(received) == 1
    assert received[0].event_type == "ci_failed"
    assert received[0].repo == "acme/backend"


# ── Edge cases ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unsupported_github_event():
    """Unhandled event type (e.g. 'star') returns 200 with accepted=False."""
    payload = {"action": "created", "repository": {"full_name": "acme/backend"}}
    body = json.dumps(payload).encode()

    received: list = []

    async def fake_inject(event):
        received.append(event)

    with patch("src.streaming.webhook_server.config") as cfg, \
         patch("src.streaming.webhook_server.inject_event", new=fake_inject):
        cfg.GITHUB_WEBHOOK_SECRET = TEST_SECRET
        cfg.JIRA_WEBHOOK_SECRET = ""
        app = _make_app_with_secret(TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "star",
                    "X-Hub-Signature-256": sign(body),
                },
            )
            assert resp.status == 200
            data = await resp.json()

    assert data["accepted"] is False
    assert len(received) == 0


@pytest.mark.asyncio
async def test_malformed_json():
    """Non-JSON body returns 400 regardless of signature."""
    body = b"this is not json {"

    with patch("src.streaming.webhook_server.config") as cfg:
        cfg.GITHUB_WEBHOOK_SECRET = TEST_SECRET
        cfg.JIRA_WEBHOOK_SECRET = ""
        app = _make_app_with_secret(TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "pull_request",
                    "X-Hub-Signature-256": sign(body),
                },
            )
            assert resp.status == 400


@pytest.mark.asyncio
async def test_no_secret_skips_verification():
    """
    When GITHUB_WEBHOOK_SECRET is empty the signature check is skipped —
    any payload is accepted (useful for initial dev setup).
    """
    payload = load_fixture("github_pr_opened.json")
    body = json.dumps(payload).encode()

    received: list = []

    async def fake_inject(event):
        received.append(event)

    with patch("src.streaming.webhook_server.config") as cfg, \
         patch("src.streaming.webhook_server.inject_event", new=fake_inject):
        cfg.GITHUB_WEBHOOK_SECRET = ""   # empty → skip verification
        cfg.JIRA_WEBHOOK_SECRET = ""
        app = _make_app_with_secret("")

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "pull_request",
                    # No signature header
                },
            )
            assert resp.status == 202

    assert len(received) == 1
