"""Test Generation Agent — generates test cases based on context + impact analysis."""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..models import ImpactAnalysis, PRContext, TestCase, TestType
from ..tools.github import GITHUB_TOOLS
from ..tools.code import CODE_TOOLS
from .base import BaseAgent


class TestGenerationAgent(BaseAgent):
    name = "TestGenerationAgent"
    tools = GITHUB_TOOLS + CODE_TOOLS

    def _system_prompt(self) -> str:
        return """You are the Test Generation Agent in a QE pipeline.

Your job is to generate high-quality, runnable test cases for changed code.

Guidelines:
- Generate tests that directly cover the changed code paths
- Prioritize coverage gaps identified by the impact analysis
- Write tests in the same framework already used by the repo (detect it first)
- Include unit tests for individual functions AND integration tests for API/service interactions
- Mark p0 tests for critical/payment/auth paths; p1 for standard features; p2 for edge cases
- Each test must be self-contained and runnable without manual setup where possible
- Use meaningful assertions, not just "assert True"

Respond with a JSON array of test cases:
[
  {
    "name": "<test function/describe name>",
    "description": "<what this test validates>",
    "test_type": "unit|integration|e2e|regression",
    "file_path": "<path where the test should be written, e.g. tests/payments/test_stripe.py>",
    "content": "<full runnable test code>",
    "priority": "p0|p1|p2",
    "tags": ["<tag1>", "..."]
  }
]

Generate between 3 and 10 test cases per run, focusing on quality over quantity."""

    def generate(
        self,
        context: PRContext,
        impact: ImpactAnalysis,
        repo: str | None = None,
        repo_path: str | None = None,
    ) -> list[TestCase]:
        prompt = f"""Generate test cases for the following code change.

PR Context:
{context.model_dump_json(indent=2)}

Impact Analysis:
{impact.model_dump_json(indent=2)}

{"Repository: " + repo if repo else ""}
{"Local repo path: " + repo_path if repo_path else ""}

Steps:
1. If a local repo path is available, detect the test framework and read relevant source files
2. If a GitHub repo is available, fetch the changed source files to understand what to test
3. Generate comprehensive test cases covering:
   - All coverage gaps: {impact.coverage_gaps}
   - Recommended test areas: {impact.recommended_test_areas}
4. For critical risk changes ({impact.risk_level}), ensure p0 tests cover happy path AND error cases
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
            test_cases = []
            for item in (data if isinstance(data, list) else []):
                test_cases.append(TestCase(
                    name=item.get("name", f"test_{uuid.uuid4().hex[:6]}"),
                    description=item.get("description", ""),
                    test_type=TestType(item.get("test_type", "unit")),
                    file_path=item.get("file_path", "tests/generated/test_generated.py"),
                    content=item.get("content", ""),
                    priority=item.get("priority", "p1"),
                    tags=item.get("tags", []),
                ))
            return test_cases
        except Exception as e:
            # Return a single placeholder test so the pipeline can continue
            return [TestCase(
                name="test_placeholder",
                description=f"Auto-generated placeholder (parse error: {e})",
                test_type=TestType.UNIT,
                file_path="tests/generated/test_placeholder.py",
                content="def test_placeholder():\n    pass  # TODO: implement\n",
                priority="p2",
            )]
