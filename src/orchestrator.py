"""
QE Agent Orchestrator — the central state machine that coordinates all agents.

Flow:
  ProductEvent
    → ContextAgent         (analyze PR/commit/ticket)
    → ImpactAnalysisAgent  (blast radius, coverage gaps, risk level)
    → TestGenerationAgent  (generate test cases)
    → Test Execution       (run in sandbox / device farm)
    → FailureAnalysisAgent (classify failures, detect product bugs)
    → SelfHealingAgent     (patch flaky/broken tests)
    → BugFilingAgent       (file Jira tickets for product bugs)
    → Metrics / Summary
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .agents import (
    BugFilingAgent,
    ContextAgent,
    FailureAnalysisAgent,
    ImpactAnalysisAgent,
    SelfHealingAgent,
    TestGenerationAgent,
)
from .models import (
    PipelineStatus,
    ProductEvent,
    QEPipelineState,
    RiskLevel,
    TestResult,
    TestRun,
)
from .tools.testing import run_tests_locally, submit_device_farm_run

console = Console()


class QEOrchestrator:
    """
    Orchestrates the full QE pipeline for a product event.
    Each stage updates the shared QEPipelineState and emits rich progress output.
    """

    def __init__(
        self,
        repo: str | None = None,
        repo_path: str | None = None,
        use_device_farm: bool = False,
    ) -> None:
        self.repo = repo
        self.repo_path = repo_path
        self.use_device_farm = use_device_farm

        self.context_agent = ContextAgent()
        self.impact_agent = ImpactAnalysisAgent()
        self.testgen_agent = TestGenerationAgent()
        self.failure_agent = FailureAnalysisAgent()
        self.healing_agent = SelfHealingAgent()
        self.bug_agent = BugFilingAgent()

    # ── Public entry point ───────────────────────────────────────────────────

    async def run(self, event: ProductEvent) -> QEPipelineState:
        state = QEPipelineState(event=event)
        console.print(Panel(
            f"[bold]QE Pipeline started[/bold]\n"
            f"Event: [cyan]{event.event_type}[/cyan]  |  ID: {event.event_id}\n"
            f"Source: {event.source}  |  Repo: {event.repo or 'N/A'}",
            title="🤖 QE Agent Orchestrator",
            border_style="blue",
        ))

        try:
            state = await self._stage_context(state)
            state = await self._stage_impact(state)

            # Skip test gen for low-risk clean CI passes
            if state.impact and state.impact.risk_level == RiskLevel.LOW and event.event_type.value == "ci_passed":
                console.print("[dim]Low-risk CI pass — skipping test generation[/dim]")
            else:
                state = await self._stage_testgen(state)
                state = await self._stage_execution(state)
                state = await self._stage_failure_analysis(state)
                state = await self._stage_healing(state)
                state = await self._stage_bug_filing(state)

            state.status = PipelineStatus.COMPLETED
            state.completed_at = datetime.utcnow()

        except Exception as e:
            state.status = PipelineStatus.FAILED
            state.errors.append(str(e))
            console.print(f"[bold red]Pipeline failed:[/bold red] {e}")

        self._print_summary(state)
        return state

    # ── Pipeline stages ──────────────────────────────────────────────────────

    async def _stage_context(self, state: QEPipelineState) -> QEPipelineState:
        state.status = PipelineStatus.ANALYZING_CONTEXT
        console.rule("[bold blue]Stage 1 · Context Analysis[/bold blue]")
        state.pr_context = await asyncio.to_thread(
            self.context_agent.analyze, state.event
        )
        if state.pr_context:
            console.print(f"  Title:   [bold]{state.pr_context.title}[/bold]")
            console.print(f"  Author:  {state.pr_context.author}")
            console.print(f"  Files:   {len(state.pr_context.changed_files)} changed")
            if state.pr_context.risk_notes:
                console.print(f"  [yellow]Risk notes:[/yellow] {state.pr_context.risk_notes}")
        return state

    async def _stage_impact(self, state: QEPipelineState) -> QEPipelineState:
        if not state.pr_context:
            return state
        state.status = PipelineStatus.ANALYZING_IMPACT
        console.rule("[bold blue]Stage 2 · Impact Analysis[/bold blue]")
        state.impact = await asyncio.to_thread(
            self.impact_agent.analyze, state.pr_context, self.repo
        )
        risk_color = {
            RiskLevel.LOW: "green",
            RiskLevel.MEDIUM: "yellow",
            RiskLevel.HIGH: "red",
            RiskLevel.CRITICAL: "bold red",
        }.get(state.impact.risk_level, "white")
        console.print(f"  Risk level:     [{risk_color}]{state.impact.risk_level.upper()}[/{risk_color}]")
        console.print(f"  Affected modules:  {state.impact.affected_modules}")
        console.print(f"  Coverage gaps:     {state.impact.coverage_gaps}")
        return state

    async def _stage_testgen(self, state: QEPipelineState) -> QEPipelineState:
        if not state.pr_context or not state.impact:
            return state
        state.status = PipelineStatus.GENERATING_TESTS
        console.rule("[bold blue]Stage 3 · Test Generation[/bold blue]")
        state.test_cases = await asyncio.to_thread(
            self.testgen_agent.generate,
            state.pr_context,
            state.impact,
            self.repo,
            self.repo_path,
            state.event.payload,
        )
        console.print(f"  Generated [bold]{len(state.test_cases)}[/bold] test cases:")
        for tc in state.test_cases:
            priority_color = {"p0": "red", "p1": "yellow", "p2": "dim"}.get(tc.priority, "white")
            console.print(f"    [{priority_color}]{tc.priority}[/{priority_color}] {tc.name} [{tc.test_type}]")
        return state

    async def _stage_execution(self, state: QEPipelineState) -> QEPipelineState:
        if not state.test_cases:
            return state
        state.status = PipelineStatus.EXECUTING_TESTS
        console.rule("[bold blue]Stage 4 · Test Execution[/bold blue]")

        test_dicts = [
            {"name": tc.name, "file_path": tc.file_path, "content": tc.content}
            for tc in state.test_cases
        ]

        raw = await asyncio.to_thread(
            submit_device_farm_run if self.use_device_farm else run_tests_locally,
            test_dicts,
        )

        run_data = json.loads(raw)
        results = [
            TestResult(
                test_name=r.get("test_name", r.get("name", "")),
                file_path=r.get("file_path", ""),
                status=r.get("status", "error"),
                duration_ms=r.get("duration_ms", 0),
                error_message=r.get("error_message") or r.get("stderr"),
                stack_trace=r.get("stack_trace"),
            )
            for r in run_data.get("results", [])
        ]

        state.test_run = TestRun(
            run_id=run_data.get("run_id", str(uuid.uuid4())[:8]),
            test_cases=state.test_cases,
            results=results,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            total=run_data.get("total", len(results)),
            passed=run_data.get("passed", sum(1 for r in results if r.status == "passed")),
            failed=run_data.get("failed", sum(1 for r in results if r.status == "failed")),
            skipped=run_data.get("skipped", sum(1 for r in results if r.status == "skipped")),
        )

        console.print(f"  {state.test_run.summary()}")
        return state

    async def _stage_failure_analysis(self, state: QEPipelineState) -> QEPipelineState:
        if not state.test_run or state.test_run.failed == 0:
            return state
        state.status = PipelineStatus.ANALYZING_FAILURES
        console.rule("[bold blue]Stage 5 · Failure Analysis[/bold blue]")
        state.failure_analyses = await asyncio.to_thread(
            self.failure_agent.analyze, state.test_run
        )
        for fa in state.failure_analyses:
            bug_label = "[red]BUG[/red]" if fa.is_product_bug else "[yellow]test issue[/yellow]"
            console.print(f"  {fa.test_name}: {fa.failure_category} → {bug_label} (conf: {fa.confidence:.0%})")
        return state

    async def _stage_healing(self, state: QEPipelineState) -> QEPipelineState:
        if not state.failure_analyses:
            return state
        state.status = PipelineStatus.HEALING
        console.rule("[bold blue]Stage 6 · Self-Healing[/bold blue]")
        state.healing_results = await asyncio.to_thread(
            self.healing_agent.heal,
            state.failure_analyses,
            state.test_cases,
            self.repo,
            state.event.branch,
        )
        if state.healing_results:
            for hr in state.healing_results:
                committed = "[green]committed[/green]" if hr.committed else "[dim]local only[/dim]"
                console.print(f"  Healed: {hr.test_name} — {committed}")
        else:
            console.print("  [dim]No tests eligible for auto-healing[/dim]")
        return state

    async def _stage_bug_filing(self, state: QEPipelineState) -> QEPipelineState:
        if not state.failure_analyses:
            return state
        state.status = PipelineStatus.FILING_BUGS
        console.rule("[bold blue]Stage 7 · Bug Filing[/bold blue]")
        state.filed_bugs = await asyncio.to_thread(
            self.bug_agent.file_bugs,
            state.failure_analyses,
            state.test_run.results if state.test_run else [],
            state.pr_context,
        )
        if state.filed_bugs:
            for bug in state.filed_bugs:
                ticket = bug.jira_ticket_id or "(mock)"
                console.print(f"  [red]Bug filed:[/red] {ticket} — {bug.title}")
        else:
            console.print("  [dim]No product bugs to file[/dim]")
        return state

    # ── Summary ──────────────────────────────────────────────────────────────

    def _print_summary(self, state: QEPipelineState) -> None:
        summary = state.to_summary()
        table = Table(title="QE Pipeline Summary", border_style="blue")
        table.add_column("Metric", style="bold")
        table.add_column("Value")

        table.add_row("Status", f"[green]{summary['status']}[/green]" if summary["status"] == "completed" else f"[red]{summary['status']}[/red]")
        table.add_row("Risk Level", str(summary.get("risk_level") or "—"))
        table.add_row("Tests Generated", str(summary["tests_generated"]))
        table.add_row("Tests Passed", f"[green]{summary['tests_passed']}[/green]")
        table.add_row("Tests Failed", f"[red]{summary['tests_failed']}[/red]" if summary["tests_failed"] else "0")
        table.add_row("Failures Analyzed", str(summary["failures_analyzed"]))
        table.add_row("Tests Healed", str(summary["tests_healed"]))
        table.add_row("Bugs Filed", str(summary["bugs_filed"]))

        if state.completed_at and state.started_at:
            elapsed = (state.completed_at - state.started_at).total_seconds()
            table.add_row("Total Time", f"{elapsed:.1f}s")

        if summary["errors"]:
            table.add_row("[red]Errors[/red]", "\n".join(summary["errors"]))

        console.print(table)
