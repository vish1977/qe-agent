"""Jira tools — fetch tickets, search for duplicates, create/update issues."""

import json
from typing import Any

import httpx

from ..config import config

_AUTH = (config.JIRA_EMAIL, config.JIRA_API_TOKEN)
_BASE = config.JIRA_BASE_URL


def _jira_get(path: str) -> Any:
    if not config.JIRA_API_TOKEN:
        return {"_mock": True}
    with httpx.Client(timeout=30) as client:
        r = client.get(f"{_BASE}/rest/api/3{path}", auth=_AUTH,
                       headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()


def _jira_post(path: str, body: dict) -> Any:
    if not config.JIRA_API_TOKEN:
        return {"_mock": True, "id": "QE-999", "key": "QE-999"}
    with httpx.Client(timeout=30) as client:
        r = client.post(
            f"{_BASE}/rest/api/3{path}",
            auth=_AUTH,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=body,
        )
        r.raise_for_status()
        return r.json()


def fetch_jira_ticket(ticket_id: str) -> str:
    data = _jira_get(f"/issue/{ticket_id}")
    if data.get("_mock"):
        return json.dumps({
            "key": ticket_id,
            "summary": "Mock ticket: implement Stripe payment flow",
            "description": "As a user I want to pay via Stripe...",
            "status": "In Progress",
            "priority": "High",
            "labels": ["payments", "backend"],
        })
    fields = data.get("fields", {})
    return json.dumps({
        "key": data.get("key"),
        "summary": fields.get("summary"),
        "description": str(fields.get("description", ""))[:1000],
        "status": fields.get("status", {}).get("name"),
        "priority": fields.get("priority", {}).get("name"),
        "labels": fields.get("labels", []),
        "components": [c.get("name") for c in fields.get("components", [])],
    })


def search_jira_bugs(query: str, max_results: int = 5) -> str:
    jql = f'project = {config.JIRA_PROJECT_KEY} AND issuetype = Bug AND text ~ "{query}" ORDER BY created DESC'
    data = _jira_get(f"/search?jql={jql}&maxResults={max_results}")
    if data.get("_mock"):
        return json.dumps({"total": 0, "issues": []})
    issues = [
        {
            "key": i.get("key"),
            "summary": i.get("fields", {}).get("summary"),
            "status": i.get("fields", {}).get("status", {}).get("name"),
        }
        for i in data.get("issues", [])
    ]
    return json.dumps({"total": data.get("total", 0), "issues": issues})


def create_jira_bug(
    title: str,
    description: str,
    severity: str,
    component: str,
    steps: list[str],
    expected: str,
    actual: str,
) -> str:
    priority_map = {"critical": "Highest", "high": "High", "medium": "Medium", "low": "Low"}
    body = {
        "fields": {
            "project": {"key": config.JIRA_PROJECT_KEY},
            "issuetype": {"name": "Bug"},
            "summary": title,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": description}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": f"Steps to reproduce:\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": f"Expected: {expected}\nActual: {actual}"}]},
                ],
            },
            "priority": {"name": priority_map.get(severity, "Medium")},
            "labels": ["qe-agent", "automated"],
            "components": [{"name": component}] if component else [],
        }
    }
    data = _jira_post("/issue", body)
    return json.dumps({"ticket_id": data.get("key"), "url": f"{_BASE}/browse/{data.get('key')}"})


def update_jira_ticket(ticket_id: str, comment: str) -> str:
    body = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment}]}],
        }
    }
    data = _jira_post(f"/issue/{ticket_id}/comment", body)
    return json.dumps({"success": True, "comment_id": data.get("id")})


# ── Tool schemas ─────────────────────────────────────────────────────────────

JIRA_TOOLS = [
    {
        "name": "fetch_jira_ticket",
        "description": "Fetch summary, description, status, and priority for a Jira ticket.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "Jira ticket key, e.g. QE-123"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "search_jira_bugs",
        "description": "Search Jira for existing bug reports matching a query string — used to detect duplicates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Error message or description to search"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_jira_bug",
        "description": "Create a new Bug in Jira for a confirmed product defect found during QE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "component": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "expected": {"type": "string"},
                "actual": {"type": "string"},
            },
            "required": ["title", "description", "severity", "component", "steps", "expected", "actual"],
        },
    },
    {
        "name": "update_jira_ticket",
        "description": "Add a comment to an existing Jira ticket (e.g. to update an existing bug with new evidence).",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "comment": {"type": "string"},
            },
            "required": ["ticket_id", "comment"],
        },
    },
]


def execute_jira_tool(name: str, inp: dict) -> str:
    dispatch = {
        "fetch_jira_ticket": lambda: fetch_jira_ticket(inp["ticket_id"]),
        "search_jira_bugs": lambda: search_jira_bugs(inp["query"], inp.get("max_results", 5)),
        "create_jira_bug": lambda: create_jira_bug(
            inp["title"], inp["description"], inp["severity"],
            inp.get("component", ""), inp["steps"], inp["expected"], inp["actual"]
        ),
        "update_jira_ticket": lambda: update_jira_ticket(inp["ticket_id"], inp["comment"]),
    }
    fn = dispatch.get(name)
    if fn is None:
        return f"Error: unknown jira tool '{name}'"
    try:
        return fn()
    except Exception as e:
        return f"Error executing {name}: {e}"
