"""Testing tools — submit test jobs, poll results, get logs."""

import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime
from typing import Any

import httpx

from ..config import config
from ..models import TestCase, TestResult


# ── In-process test runner (sandbox / local) ─────────────────────────────────

def run_tests_locally(test_cases: list[dict]) -> str:
    """
    Write test files to a temp dir and run pytest.
    Returns JSON summary with per-test results.
    """
    run_id = str(uuid.uuid4())[:8]
    results: list[dict] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for tc in test_cases:
            file_path = os.path.join(tmpdir, os.path.basename(tc["file_path"]))
            with open(file_path, "w") as f:
                f.write(tc["content"])

            start = datetime.utcnow()
            proc = subprocess.run(
                ["python", "-m", "pytest", file_path, "--tb=short", "-q", "--no-header"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=tmpdir,
            )
            duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)

            status = "passed" if proc.returncode == 0 else "failed"
            results.append({
                "test_name": tc["name"],
                "file_path": tc["file_path"],
                "status": status,
                "duration_ms": duration_ms,
                "stdout": proc.stdout[-2000:] if proc.stdout else "",
                "stderr": proc.stderr[-2000:] if proc.stderr else "",
                "error_message": proc.stdout[-500:] if status == "failed" else None,
            })

    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")

    return json.dumps({
        "run_id": run_id,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    })


def submit_device_farm_run(test_cases: list[dict], platform: str = "web") -> str:
    """Submit tests to the device farm grid."""
    run_id = f"df-{str(uuid.uuid4())[:8]}"
    if not config.DEVICE_FARM_ENDPOINT or config.DEVICE_FARM_ENDPOINT == "http://localhost:4444":
        # Fallback to local runner in dev/mock mode
        result = json.loads(run_tests_locally(test_cases))
        result["run_id"] = run_id
        result["platform"] = platform
        return json.dumps(result)

    payload = {
        "run_id": run_id,
        "platform": platform,
        "concurrency": config.DEVICE_FARM_CONCURRENCY,
        "tests": [{"name": tc["name"], "file": tc["file_path"], "content": tc["content"]} for tc in test_cases],
    }
    with httpx.Client(timeout=10) as client:
        r = client.post(f"{config.DEVICE_FARM_ENDPOINT}/submit", json=payload)
        r.raise_for_status()
        return r.text


def get_test_run_results(run_id: str) -> str:
    """Poll for test run results by run_id."""
    if config.DEVICE_FARM_ENDPOINT == "http://localhost:4444":
        return json.dumps({"run_id": run_id, "status": "completed", "results": []})
    with httpx.Client(timeout=10) as client:
        r = client.get(f"{config.DEVICE_FARM_ENDPOINT}/results/{run_id}")
        r.raise_for_status()
        return r.text


def get_test_logs(run_id: str, test_name: str) -> str:
    """Fetch detailed logs for a specific failed test."""
    if config.DEVICE_FARM_ENDPOINT == "http://localhost:4444":
        return json.dumps({
            "run_id": run_id,
            "test_name": test_name,
            "log": "No device farm configured — mock log output.\nAssertionError: expected 200, got 404",
        })
    with httpx.Client(timeout=10) as client:
        r = client.get(f"{config.DEVICE_FARM_ENDPOINT}/logs/{run_id}/{test_name}")
        r.raise_for_status()
        return r.text


def list_historical_failures(test_name: str, limit: int = 10) -> str:
    """Look up past failure records for a test to detect flakiness."""
    # In a real system this would query a test results DB / Elasticsearch
    return json.dumps({
        "test_name": test_name,
        "total_runs": 50,
        "failures": 3,
        "flaky_score": 0.06,
        "last_failures": [],
        "note": "Connect to your test history store to populate this.",
    })


# ── Tool schemas ─────────────────────────────────────────────────────────────

TESTING_TOOLS = [
    {
        "name": "run_tests_locally",
        "description": (
            "Execute a list of test cases in a sandbox using pytest. "
            "Each test case must have 'name', 'file_path', and 'content' fields. "
            "Returns a JSON summary with per-test pass/fail results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "test_cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "file_path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["name", "file_path", "content"],
                    },
                }
            },
            "required": ["test_cases"],
        },
    },
    {
        "name": "submit_device_farm_run",
        "description": "Submit test cases to the parallel device farm grid for execution across multiple platforms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "test_cases": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "platform": {
                    "type": "string",
                    "enum": ["web", "ios", "android"],
                    "default": "web",
                },
            },
            "required": ["test_cases"],
        },
    },
    {
        "name": "get_test_run_results",
        "description": "Poll for the status and results of a previously submitted test run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "get_test_logs",
        "description": "Fetch full logs and error details for a specific failed test in a run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "test_name": {"type": "string"},
            },
            "required": ["run_id", "test_name"],
        },
    },
    {
        "name": "list_historical_failures",
        "description": "Check the failure history for a test to determine if it is flaky.",
        "input_schema": {
            "type": "object",
            "properties": {
                "test_name": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["test_name"],
        },
    },
]


def execute_testing_tool(name: str, inp: dict) -> str:
    dispatch = {
        "run_tests_locally": lambda: run_tests_locally(inp["test_cases"]),
        "submit_device_farm_run": lambda: submit_device_farm_run(
            inp["test_cases"], inp.get("platform", "web")
        ),
        "get_test_run_results": lambda: get_test_run_results(inp["run_id"]),
        "get_test_logs": lambda: get_test_logs(inp["run_id"], inp["test_name"]),
        "list_historical_failures": lambda: list_historical_failures(
            inp["test_name"], inp.get("limit", 10)
        ),
    }
    fn = dispatch.get(name)
    if fn is None:
        return f"Error: unknown testing tool '{name}'"
    try:
        return fn()
    except Exception as e:
        return f"Error executing {name}: {e}"
