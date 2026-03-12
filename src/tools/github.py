"""GitHub tools — fetch PR diffs, commits, and file contents."""

import json
import os
from typing import Any

import httpx

from ..config import config

_BASE = config.GITHUB_API_URL
_HEADERS = {
    "Authorization": f"Bearer {config.GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _gh_get(path: str) -> Any:
    if not config.GITHUB_TOKEN:
        return {"_mock": True, "note": "No GITHUB_TOKEN set — returning mock data"}
    with httpx.Client(timeout=30) as client:
        r = client.get(f"{_BASE}{path}", headers=_HEADERS)
        r.raise_for_status()
        return r.json()


def _gh_get_text(path: str, accept: str = "application/vnd.github.diff") -> str:
    if not config.GITHUB_TOKEN:
        return "--- mock diff ---\n+++ no GITHUB_TOKEN configured"
    headers = {**_HEADERS, "Accept": accept}
    with httpx.Client(timeout=30) as client:
        r = client.get(f"{_BASE}{path}", headers=headers)
        r.raise_for_status()
        return r.text


# ── Tool handlers ────────────────────────────────────────────────────────────

def fetch_pr_details(repo: str, pr_number: int) -> str:
    data = _gh_get(f"/repos/{repo}/pulls/{pr_number}")
    if isinstance(data, dict) and data.get("_mock"):
        return json.dumps({
            "title": "feat: add new payment flow",
            "body": "Implements Stripe checkout integration",
            "user": {"login": "dev-user"},
            "base": {"ref": "main"},
            "head": {"ref": "feat/stripe-checkout"},
            "changed_files": 8,
            "additions": 312,
            "deletions": 44,
        })
    return json.dumps({
        "title": data.get("title"),
        "body": data.get("body"),
        "user": data.get("user", {}).get("login"),
        "base": data.get("base", {}).get("ref"),
        "head": data.get("head", {}).get("ref"),
        "changed_files": data.get("changed_files"),
        "additions": data.get("additions"),
        "deletions": data.get("deletions"),
    })


def fetch_pr_diff(repo: str, pr_number: int) -> str:
    try:
        return _gh_get_text(f"/repos/{repo}/pulls/{pr_number}")
    except Exception as e:
        return f"Error fetching diff: {e}"


def fetch_pr_files(repo: str, pr_number: int) -> str:
    data = _gh_get(f"/repos/{repo}/pulls/{pr_number}/files")
    if isinstance(data, dict) and data.get("_mock"):
        return json.dumps([
            {"filename": "src/payments/stripe.ts", "status": "added", "additions": 180, "deletions": 0},
            {"filename": "src/payments/types.ts", "status": "modified", "additions": 24, "deletions": 8},
            {"filename": "tests/payments/stripe.test.ts", "status": "added", "additions": 108, "deletions": 0},
        ])
    files = [
        {
            "filename": f.get("filename"),
            "status": f.get("status"),
            "additions": f.get("additions"),
            "deletions": f.get("deletions"),
            "patch": f.get("patch", "")[:500],  # truncate large diffs
        }
        for f in (data if isinstance(data, list) else [])
    ]
    return json.dumps(files)


def fetch_file_content(repo: str, file_path: str, ref: str = "main") -> str:
    import base64
    data = _gh_get(f"/repos/{repo}/contents/{file_path}?ref={ref}")
    if isinstance(data, dict) and data.get("_mock"):
        return f"// mock content for {file_path}"
    if isinstance(data, dict) and data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return json.dumps(data)


def fetch_commit_details(repo: str, sha: str) -> str:
    data = _gh_get(f"/repos/{repo}/commits/{sha}")
    if isinstance(data, dict) and data.get("_mock"):
        return json.dumps({"sha": sha, "message": "mock commit", "files": []})
    commit = data.get("commit", {})
    return json.dumps({
        "sha": sha,
        "message": commit.get("message", ""),
        "author": commit.get("author", {}).get("name"),
        "date": commit.get("author", {}).get("date"),
        "files": [f.get("filename") for f in data.get("files", [])],
    })


def create_commit(repo: str, branch: str, file_path: str, content: str, message: str) -> str:
    """Commit a file change — used by self-healing agent."""
    if not config.GITHUB_TOKEN or not config.GIT_COMMIT_AUTO_HEAL:
        return json.dumps({"committed": False, "reason": "auto-commit disabled or no token"})

    import base64
    # Get current file SHA (needed for update)
    try:
        existing = _gh_get(f"/repos/{repo}/contents/{file_path}?ref={branch}")
        file_sha = existing.get("sha") if isinstance(existing, dict) else None
    except Exception:
        file_sha = None

    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if file_sha:
        payload["sha"] = file_sha

    with httpx.Client(timeout=30) as client:
        r = client.put(
            f"{_BASE}/repos/{repo}/contents/{file_path}",
            headers=_HEADERS,
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        return json.dumps({
            "committed": True,
            "sha": data.get("commit", {}).get("sha"),
        })


# ── Tool schema definitions ──────────────────────────────────────────────────

GITHUB_TOOLS = [
    {
        "name": "fetch_pr_details",
        "description": "Fetch metadata for a GitHub Pull Request (title, description, author, branch, file counts).",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Owner/repo, e.g. acme/backend"},
                "pr_number": {"type": "integer", "description": "PR number"},
            },
            "required": ["repo", "pr_number"],
        },
    },
    {
        "name": "fetch_pr_diff",
        "description": "Fetch the full unified diff for a GitHub Pull Request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "pr_number": {"type": "integer"},
            },
            "required": ["repo", "pr_number"],
        },
    },
    {
        "name": "fetch_pr_files",
        "description": "List all changed files in a PR with addition/deletion counts and patches.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "pr_number": {"type": "integer"},
            },
            "required": ["repo", "pr_number"],
        },
    },
    {
        "name": "fetch_file_content",
        "description": "Fetch the content of a specific file at a given ref (branch/commit).",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "file_path": {"type": "string", "description": "Path relative to repo root"},
                "ref": {"type": "string", "description": "Branch name or commit SHA", "default": "main"},
            },
            "required": ["repo", "file_path"],
        },
    },
    {
        "name": "fetch_commit_details",
        "description": "Get commit metadata and list of changed files for a specific commit SHA.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "sha": {"type": "string"},
            },
            "required": ["repo", "sha"],
        },
    },
    {
        "name": "create_commit",
        "description": "Commit a file change to a branch — used only by the self-healing agent to fix broken tests.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "branch": {"type": "string"},
                "file_path": {"type": "string"},
                "content": {"type": "string", "description": "Full new content of the file"},
                "message": {"type": "string", "description": "Commit message"},
            },
            "required": ["repo", "branch", "file_path", "content", "message"],
        },
    },
]


def execute_github_tool(name: str, inp: dict) -> str:
    dispatch = {
        "fetch_pr_details": lambda: fetch_pr_details(inp["repo"], inp["pr_number"]),
        "fetch_pr_diff": lambda: fetch_pr_diff(inp["repo"], inp["pr_number"]),
        "fetch_pr_files": lambda: fetch_pr_files(inp["repo"], inp["pr_number"]),
        "fetch_file_content": lambda: fetch_file_content(inp["repo"], inp["file_path"], inp.get("ref", "main")),
        "fetch_commit_details": lambda: fetch_commit_details(inp["repo"], inp["sha"]),
        "create_commit": lambda: create_commit(
            inp["repo"], inp["branch"], inp["file_path"], inp["content"], inp["message"]
        ),
    }
    fn = dispatch.get(name)
    if fn is None:
        return f"Error: unknown github tool '{name}'"
    try:
        return fn()
    except Exception as e:
        return f"Error executing {name}: {e}"
