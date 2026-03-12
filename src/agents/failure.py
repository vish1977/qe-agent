"""Failure Analysis Agent — classifies test failures and determines root causes."""

from __future__ import annotations

import json

from ..models import FailureAnalysis, FailureCategory, TestResult, TestRun
from ..tools.testing import TESTING_TOOLS
from ..tools.github import GITHUB_TOOLS
from .base import BaseAgent


class FailureAnalysisAgent(BaseAgent):
    name = "FailureAnalysisAgent"
    tools = TESTING_TOOLS + GITHUB_TOOLS

    def _system_prompt(self) -> str:
        return """You are the Failure Analysis Agent in a QE pipeline.

Given test run results containing failures, you must:
1. Classify each failure by category: assertion_error | timeout | infrastructure | flaky | regression | environment | new_bug
2. Determine the root cause (not just the surface error)
3. Identify whether each failure indicates a product bug vs a test issue
4. Check failure history to detect flaky tests
5. Suggest a concrete fix for each failure

Categories:
- assertion_error: test expectation doesn't match actual behavior → likely a bug or test needs update
- timeout: test took too long → could be performance regression or infra issue
- infrastructure: test runner/environment issue → not a product bug
- flaky: intermittently fails/passes → test reliability issue
- regression: previously passing test now fails → product bug introduced
- environment: missing env vars, missing services → not a product bug
- new_bug: new test catches a real bug → must file a Jira ticket

Respond with a JSON array:
[
  {
    "test_name": "<test name>",
    "failure_category": "<category>",
    "root_cause": "<detailed explanation>",
    "is_flaky": <bool>,
    "is_product_bug": <bool>,
    "suggested_fix": "<concrete suggestion>",
    "confidence": <0.0 to 1.0>,
    "related_component": "<component name>"
  }
]"""

    def analyze(self, test_run: TestRun) -> list[FailureAnalysis]:
        failed = [r for r in test_run.results if r.status == "failed"]
        if not failed:
            return []

        failed_summaries = [
            {
                "test_name": r.test_name,
                "file_path": r.file_path,
                "error_message": r.error_message,
                "stack_trace": r.stack_trace,
                "duration_ms": r.duration_ms,
            }
            for r in failed
        ]

        prompt = f"""Analyze these {len(failed)} test failures from run {test_run.run_id}.

Failed tests:
{json.dumps(failed_summaries, indent=2)}

For each failure:
1. Call list_historical_failures to check if it's flaky
2. Get full logs with get_test_logs if available
3. Classify and determine root cause
4. Identify whether it's a product bug requiring a Jira ticket
"""
        result = self.run(prompt)

        try:
            text = result
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1 and end > start:
                text = text[start:end + 1]
            data = json.loads(text)
            analyses = []
            for item in (data if isinstance(data, list) else []):
                analyses.append(FailureAnalysis(
                    test_name=item.get("test_name", ""),
                    failure_category=FailureCategory(item.get("failure_category", "assertion_error")),
                    root_cause=item.get("root_cause", ""),
                    is_flaky=item.get("is_flaky", False),
                    is_product_bug=item.get("is_product_bug", False),
                    suggested_fix=item.get("suggested_fix", ""),
                    confidence=float(item.get("confidence", 0.5)),
                    related_component=item.get("related_component", "unknown"),
                ))
            return analyses
        except Exception as e:
            # Fallback — create minimal analysis for each failure
            return [
                FailureAnalysis(
                    test_name=r.test_name,
                    failure_category=FailureCategory.ASSERTION_ERROR,
                    root_cause=r.error_message or "Unknown",
                    is_flaky=False,
                    is_product_bug=True,
                    suggested_fix="Review the error and fix manually",
                    confidence=0.5,
                    related_component="unknown",
                )
                for r in failed
            ]
