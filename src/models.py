from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Event Layer ─────────────────────────────────────────────────────────────

class EventType(str, Enum):
    PR_OPENED = "pr_opened"
    PR_UPDATED = "pr_updated"
    PR_MERGED = "pr_merged"
    CI_FAILED = "ci_failed"
    CI_PASSED = "ci_passed"
    JIRA_UPDATED = "jira_updated"
    RELEASE_CREATED = "release_created"
    SLACK_TRIGGER = "slack_trigger"


class ProductEvent(BaseModel):
    event_id: str
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str                       # "github" | "jira" | "jenkins" | "slack"
    repo: Optional[str] = None
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    commit_sha: Optional[str] = None
    jira_ticket: Optional[str] = None
    branch: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Context Agent outputs ────────────────────────────────────────────────────

class ChangedFile(BaseModel):
    path: str
    additions: int
    deletions: int
    language: Optional[str] = None
    is_test_file: bool = False


class PRContext(BaseModel):
    pr_url: str
    title: str
    description: str
    author: str
    base_branch: str
    head_branch: str
    changed_files: list[ChangedFile]
    diff_summary: str
    jira_ticket: Optional[str] = None
    jira_summary: Optional[str] = None
    risk_notes: str = ""


# ── Impact Analysis Agent outputs ────────────────────────────────────────────

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ImpactAnalysis(BaseModel):
    affected_modules: list[str]
    affected_test_files: list[str]
    coverage_gaps: list[str]
    risk_level: RiskLevel
    recommended_test_areas: list[str]
    rationale: str


# ── Test Generation Agent outputs ────────────────────────────────────────────

class TestType(str, Enum):
    UNIT = "unit"
    INTEGRATION = "integration"
    E2E = "e2e"
    REGRESSION = "regression"


class TestCase(BaseModel):
    name: str
    description: str
    test_type: TestType
    file_path: str
    content: str
    priority: str = "p1"             # p0 | p1 | p2
    tags: list[str] = Field(default_factory=list)


# ── Test Execution ────────────────────────────────────────────────────────────

class TestResult(BaseModel):
    test_name: str
    file_path: str
    status: str                       # passed | failed | skipped | error
    duration_ms: int = 0
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    screenshot_path: Optional[str] = None
    attempt: int = 1


class TestRun(BaseModel):
    run_id: str
    test_cases: list[TestCase]
    results: list[TestResult] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0

    def summary(self) -> str:
        return (
            f"Run {self.run_id}: {self.passed}/{self.total} passed, "
            f"{self.failed} failed, {self.skipped} skipped"
        )


# ── Failure Analysis Agent outputs ───────────────────────────────────────────

class FailureCategory(str, Enum):
    ASSERTION_ERROR = "assertion_error"
    TIMEOUT = "timeout"
    INFRASTRUCTURE = "infrastructure"
    FLAKY = "flaky"
    REGRESSION = "regression"
    ENVIRONMENT = "environment"
    NEW_BUG = "new_bug"


class FailureAnalysis(BaseModel):
    test_name: str
    failure_category: FailureCategory
    root_cause: str
    is_flaky: bool
    is_product_bug: bool
    suggested_fix: str
    confidence: float                  # 0.0 – 1.0
    related_component: str


# ── Self-Healing Agent outputs ───────────────────────────────────────────────

class HealingResult(BaseModel):
    test_name: str
    original_file: str
    patched_content: str
    explanation: str
    committed: bool = False
    commit_sha: Optional[str] = None


# ── Bug Filing Agent outputs ─────────────────────────────────────────────────

class BugSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BugReport(BaseModel):
    title: str
    description: str
    severity: BugSeverity
    steps_to_reproduce: list[str]
    expected_behavior: str
    actual_behavior: str
    affected_component: str
    test_name: str
    error_message: str
    jira_ticket_id: Optional[str] = None
    duplicate_of: Optional[str] = None


# ── Orchestrator State ───────────────────────────────────────────────────────

class PipelineStatus(str, Enum):
    PENDING = "pending"
    ANALYZING_CONTEXT = "analyzing_context"
    ANALYZING_IMPACT = "analyzing_impact"
    GENERATING_TESTS = "generating_tests"
    EXECUTING_TESTS = "executing_tests"
    ANALYZING_FAILURES = "analyzing_failures"
    HEALING = "healing"
    FILING_BUGS = "filing_bugs"
    COMPLETED = "completed"
    FAILED = "failed"


class QEPipelineState(BaseModel):
    event: ProductEvent
    status: PipelineStatus = PipelineStatus.PENDING
    pr_context: Optional[PRContext] = None
    impact: Optional[ImpactAnalysis] = None
    test_cases: list[TestCase] = Field(default_factory=list)
    test_run: Optional[TestRun] = None
    failure_analyses: list[FailureAnalysis] = Field(default_factory=list)
    healing_results: list[HealingResult] = Field(default_factory=list)
    filed_bugs: list[BugReport] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    def to_summary(self) -> dict[str, Any]:
        return {
            "event_id": self.event.event_id,
            "event_type": self.event.event_type,
            "status": self.status,
            "risk_level": self.impact.risk_level if self.impact else None,
            "tests_generated": len(self.test_cases),
            "tests_run": self.test_run.total if self.test_run else 0,
            "tests_passed": self.test_run.passed if self.test_run else 0,
            "tests_failed": self.test_run.failed if self.test_run else 0,
            "failures_analyzed": len(self.failure_analyses),
            "tests_healed": len(self.healing_results),
            "bugs_filed": len(self.filed_bugs),
            "errors": self.errors,
        }
