"""Self-Healing Agent — patches flaky or broken tests automatically."""

from __future__ import annotations

import json
import uuid

from ..models import FailureAnalysis, HealingResult, TestCase, TestResult
from ..tools.github import GITHUB_TOOLS
from ..tools.code import CODE_TOOLS
from .base import BaseAgent


class SelfHealingAgent(BaseAgent):
    name = "SelfHealingAgent"
    tools = GITHUB_TOOLS + CODE_TOOLS

    def _system_prompt(self) -> str:
        return """You are the Self-Healing Test Agent in a QE pipeline.

Your job is to automatically fix broken or flaky test files — NOT production code.

You should only heal tests in these categories:
- flaky: add retries, fix timing issues, improve selectors
- assertion_error (when the test logic is wrong, not the product): update expected values
- environment: add proper setup/teardown, mock missing dependencies

Do NOT attempt to heal tests where:
- is_product_bug = true (those should be filed as bugs, not hidden by test changes)
- failure_category = regression (the product needs to be fixed)

For each healable failure:
1. Read the failing test file (use read_source_file or fetch_file_content)
2. Read the source being tested to understand what the correct behavior is
3. Produce a patched version of the test
4. Explain what you changed and why

Respond with a JSON array:
[
  {
    "test_name": "<test name>",
    "original_file": "<file path>",
    "patched_content": "<full new content of the test file>",
    "explanation": "<what was changed and why>"
  }
]

Be conservative — if you're not confident, skip healing and let a human review."""

    def heal(
        self,
        failures: list[FailureAnalysis],
        test_cases: list[TestCase],
        repo: str | None = None,
        branch: str | None = None,
    ) -> list[HealingResult]:
        # Only attempt to heal non-product-bug, non-regression failures
        healable = [
            f for f in failures
            if not f.is_product_bug and f.failure_category.value in ("flaky", "assertion_error", "environment")
        ]

        if not healable:
            return []

        # Map test names to their file paths
        test_file_map = {tc.name: tc.file_path for tc in test_cases}

        prompt = f"""Heal the following test failures.

Healable failures:
{json.dumps([f.model_dump() for f in healable], indent=2)}

Test file paths:
{json.dumps(test_file_map, indent=2)}

{'Repository: ' + repo if repo else 'No GitHub repo — use read_source_file for local files'}
{'Branch: ' + branch if branch else ''}

For each failure:
1. Read the current test file content
2. Understand the root cause: {[f.root_cause for f in healable]}
3. Apply the minimal fix to make the test reliable and correct
4. Commit if auto-healing is enabled (create_commit tool)
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
                HealingResult(
                    test_name=item.get("test_name", ""),
                    original_file=item.get("original_file", ""),
                    patched_content=item.get("patched_content", ""),
                    explanation=item.get("explanation", ""),
                    committed=item.get("committed", False),
                    commit_sha=item.get("commit_sha"),
                )
                for item in (data if isinstance(data, list) else [])
            ]
        except Exception:
            return []
