"""Impact Analysis Agent — determines blast radius and test coverage gaps."""

from __future__ import annotations

import json

from ..models import ImpactAnalysis, PRContext, RiskLevel
from ..tools.github import GITHUB_TOOLS
from ..tools.code import CODE_TOOLS
from .base import BaseAgent


class ImpactAnalysisAgent(BaseAgent):
    name = "ImpactAnalysisAgent"
    tools = GITHUB_TOOLS + CODE_TOOLS

    def _system_prompt(self) -> str:
        return """You are the Impact Analysis Agent in a QE pipeline.

Given a PR context, you must determine:
1. Which modules/components are affected by the changes
2. Which existing test files cover those areas
3. Where there are coverage gaps (changed code with no corresponding tests)
4. The risk level of the change: low | medium | high | critical
5. Which test areas should be prioritized

Risk level guidance:
- critical: core auth, payments, data migrations, security changes
- high: API contract changes, database schema, shared utilities
- medium: new features in existing modules, UI changes
- low: docs, minor refactors, test-only changes

Respond with a JSON object in this exact format:
{
  "affected_modules": ["<module1>", "..."],
  "affected_test_files": ["<test_file1>", "..."],
  "coverage_gaps": ["<area with no tests>", "..."],
  "risk_level": "low|medium|high|critical",
  "recommended_test_areas": ["<area1>", "..."],
  "rationale": "<explanation of your risk assessment>"
}"""

    def analyze(self, context: PRContext, repo: str | None = None) -> ImpactAnalysis:
        prompt = f"""Perform impact analysis for the following PR context.
{f'Repository: {repo}' if repo else ''}

PR Context:
{context.model_dump_json(indent=2)}

Steps:
1. Identify which modules are directly modified
2. Check if test files exist for those modules (use fetch_file_content or find_test_files if repo is available)
3. Identify gaps
4. Assess overall risk
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
            return ImpactAnalysis(
                affected_modules=data.get("affected_modules", []),
                affected_test_files=data.get("affected_test_files", []),
                coverage_gaps=data.get("coverage_gaps", []),
                risk_level=RiskLevel(data.get("risk_level", "medium")),
                recommended_test_areas=data.get("recommended_test_areas", []),
                rationale=data.get("rationale", ""),
            )
        except Exception:
            # Fallback — infer from context
            changed = [f.path for f in context.changed_files]
            return ImpactAnalysis(
                affected_modules=list({p.split("/")[0] for p in changed if "/" in p}),
                affected_test_files=[f.path for f in context.changed_files if f.is_test_file],
                coverage_gaps=[f.path for f in context.changed_files if not f.is_test_file],
                risk_level=RiskLevel.MEDIUM,
                recommended_test_areas=changed[:5],
                rationale=result[:300],
            )
