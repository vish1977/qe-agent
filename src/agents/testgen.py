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
    max_tokens = 16000

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
        event_payload: dict | None = None,
    ) -> list[TestCase]:
        has_source = bool(repo or repo_path or context.changed_files)
        jira_context = ""
        if event_payload:
            acs = event_payload.get("acceptance_criteria", [])
            desc = event_payload.get("description", "")
            if acs or desc:
                jira_context = f"""
Jira Ticket Context:
Description: {desc}
Acceptance Criteria:
{chr(10).join(f'  - {ac}' for ac in acs)}
"""

        prompt = f"""Generate test cases for the following {'code change' if has_source else 'Jira ticket requirements'}.

PR Context:
{context.model_dump_json(indent=2)}

Impact Analysis:
{impact.model_dump_json(indent=2)}
{jira_context}
{"Repository: " + repo if repo else ""}
{"Local repo path: " + repo_path if repo_path else ""}

Steps:
{"1. Detect the test framework and read relevant source files" if has_source else "1. No source code is available — generate tests purely from the Jira description and acceptance criteria above"}
{"2. Fetch the changed source files to understand what to test" if repo else "2. Write pytest tests that validate each acceptance criterion as a black-box API/integration test"}
3. Generate comprehensive test cases covering:
   - All coverage gaps: {impact.coverage_gaps}
   - Recommended test areas: {impact.recommended_test_areas}
4. For {'critical' if str(impact.risk_level) in ('high','critical') else 'this'} risk change, ensure p0 tests cover happy path AND error/empty state cases
{"5. Since there is no source code, use requests or httpx to call the BFF API endpoints described in the ticket" if not has_source else ""}
"""
        result = self.run(prompt)

        try:
            # Robustly extract the first JSON array from anywhere in the response
            text = result
            import re as _re
            # Find the first [{ to skip any leading [ in prose text
            m = _re.search(r'\[\s*\{', text)
            if m:
                start = m.start()
            else:
                start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1 and end > start:
                text = text[start:end + 1]
            # Use strict=False to allow literal newlines in content strings
            try:
                data = json.loads(text, strict=False)
            except json.JSONDecodeError:
                # Response may be truncated — extract all complete {...} objects we can find
                objects = _re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, _re.DOTALL)
                data = []
                for obj_str in objects:
                    try:
                        data.append(json.loads(obj_str, strict=False))
                    except json.JSONDecodeError:
                        pass
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
