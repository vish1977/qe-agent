"""Context Agent — analyzes a ProductEvent to extract PR/commit/ticket context."""

from __future__ import annotations

import json

from ..models import PRContext, ProductEvent, ChangedFile
from ..tools.github import GITHUB_TOOLS
from ..tools.jira import JIRA_TOOLS
from .base import BaseAgent


class ContextAgent(BaseAgent):
    name = "ContextAgent"
    tools = GITHUB_TOOLS + JIRA_TOOLS

    def _system_prompt(self) -> str:
        return """You are the Context Agent in a QE (Quality Engineering) pipeline.

Your job is to gather and synthesize all relevant context about a product change event:
- Fetch PR details, diff, and changed files from GitHub
- Fetch the associated Jira ticket if available
- Produce a clear, structured summary of what changed and why

When you have gathered enough context, respond with a JSON object in this exact format:
{
  "pr_url": "<url>",
  "title": "<PR title>",
  "description": "<description>",
  "author": "<github username>",
  "base_branch": "<target branch>",
  "head_branch": "<source branch>",
  "changed_files": [
    {"path": "<file>", "additions": <n>, "deletions": <n>, "language": "<lang>", "is_test_file": <bool>}
  ],
  "diff_summary": "<2-3 sentence summary of what changed>",
  "jira_ticket": "<ticket key or null>",
  "jira_summary": "<ticket summary or null>",
  "risk_notes": "<any notable risk observations>"
}

Be thorough — this context drives all downstream QE decisions."""

    def analyze(self, event: ProductEvent) -> PRContext | None:
        prompt_parts = [f"Analyze the following product event and gather its full context.\n\nEvent: {event.model_dump_json(indent=2)}"]

        if event.pr_url:
            prompt_parts.append(f"\nPR URL: {event.pr_url}")
        if event.pr_number and event.repo:
            prompt_parts.append(f"\nFetch PR #{event.pr_number} from repo {event.repo}")
        if event.jira_ticket:
            prompt_parts.append(f"\nJira ticket: {event.jira_ticket}")

        result = self.run("\n".join(prompt_parts))

        # Extract JSON from the response
        try:
            # Claude may wrap JSON in markdown code blocks
            text = result.strip()
            if "```" in text:
                start = text.find("```json\n")
                if start == -1:
                    start = text.find("```\n")
                end = text.rfind("```")
                if start != -1 and end != -1:
                    text = text[start + text[start:].find("\n") + 1 : end].strip()

            data = json.loads(text)
            changed_files = [ChangedFile(**f) for f in data.get("changed_files", [])]
            return PRContext(
                pr_url=data.get("pr_url", event.pr_url or ""),
                title=data.get("title", ""),
                description=data.get("description", ""),
                author=data.get("author", ""),
                base_branch=data.get("base_branch", ""),
                head_branch=data.get("head_branch", ""),
                changed_files=changed_files,
                diff_summary=data.get("diff_summary", ""),
                jira_ticket=data.get("jira_ticket"),
                jira_summary=data.get("jira_summary"),
                risk_notes=data.get("risk_notes", ""),
            )
        except Exception as e:
            # Return a minimal context with the raw summary as diff_summary
            return PRContext(
                pr_url=event.pr_url or "",
                title=f"Event: {event.event_type}",
                description=result[:500],
                author="",
                base_branch="main",
                head_branch=event.branch or "",
                changed_files=[],
                diff_summary=result[:300],
            )
