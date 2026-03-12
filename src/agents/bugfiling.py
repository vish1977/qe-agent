"""Bug Filing Agent — creates Jira tickets for confirmed product bugs."""

from __future__ import annotations

import json

from ..models import BugReport, BugSeverity, FailureAnalysis, PRContext, TestResult
from ..tools.jira import JIRA_TOOLS
from .base import BaseAgent


class BugFilingAgent(BaseAgent):
    name = "BugFilingAgent"
    tools = JIRA_TOOLS

    def _system_prompt(self) -> str:
        return """You are the Bug Filing Agent in a QE pipeline.

Your job is to create well-structured Jira bug tickets for confirmed product defects found during automated QE.

Guidelines:
- Only file bugs for failures where is_product_bug = true
- Always search for existing bugs first to avoid duplicates
- Write clear, reproducible bug reports with specific steps
- Assign severity based on failure_category and affected component:
  - critical: auth, payments, data loss, security
  - high: core user flows broken, API contract violations
  - medium: feature degraded but workaround exists
  - low: minor visual/UX issues, edge cases
- Include test name, error message, and stack trace in the description
- Link to the PR that caused the regression if available

After filing, respond with a JSON array:
[
  {
    "test_name": "<failing test>",
    "title": "<bug title>",
    "description": "<full description>",
    "severity": "critical|high|medium|low",
    "steps_to_reproduce": ["step 1", "step 2"],
    "expected_behavior": "<what should happen>",
    "actual_behavior": "<what actually happens>",
    "affected_component": "<component>",
    "error_message": "<raw error>",
    "jira_ticket_id": "<QE-XXX or null if duplicate>",
    "duplicate_of": "<existing ticket if duplicate>"
  }
]"""

    def file_bugs(
        self,
        failures: list[FailureAnalysis],
        test_results: list[TestResult],
        context: PRContext | None = None,
    ) -> list[BugReport]:
        product_bugs = [f for f in failures if f.is_product_bug]
        if not product_bugs:
            return []

        # Map test names to results for error details
        result_map = {r.test_name: r for r in test_results}

        failures_with_errors = []
        for f in product_bugs:
            result = result_map.get(f.test_name)
            failures_with_errors.append({
                **f.model_dump(),
                "error_message": result.error_message if result else None,
                "stack_trace": (result.stack_trace or "")[:500] if result else None,
            })

        prompt = f"""File Jira bug tickets for the following confirmed product bugs.

Product bug failures:
{json.dumps(failures_with_errors, indent=2)}

PR Context:
{context.model_dump_json(indent=2) if context else "N/A"}

Steps:
1. For each failure, search_jira_bugs to check for duplicates
2. If no duplicate exists, create_jira_bug with full details
3. If a duplicate exists, update_jira_ticket with a comment linking this occurrence
4. Return the full bug report list
"""
        result = self.run(prompt)

        try:
            text = result.strip()
            if "```" in text:
                start = text.find("```json\n")
                if start == -1:
                    start = text.find("```\n")
                end = text.rfind("```")
                if start != -1 and end != -1:
                    text = text[start + text[start:].find("\n") + 1: end].strip()

            data = json.loads(text)
            return [
                BugReport(
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    severity=BugSeverity(item.get("severity", "medium")),
                    steps_to_reproduce=item.get("steps_to_reproduce", []),
                    expected_behavior=item.get("expected_behavior", ""),
                    actual_behavior=item.get("actual_behavior", ""),
                    affected_component=item.get("affected_component", ""),
                    test_name=item.get("test_name", ""),
                    error_message=item.get("error_message", ""),
                    jira_ticket_id=item.get("jira_ticket_id"),
                    duplicate_of=item.get("duplicate_of"),
                )
                for item in (data if isinstance(data, list) else [])
            ]
        except Exception:
            return []
