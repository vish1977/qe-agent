"""Base agent — shared agentic loop logic for all QE agents."""

from __future__ import annotations

import json
from typing import Any

import anthropic
from rich.console import Console

from ..config import config
from ..tools import execute_tool

console = Console()

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


class BaseAgent:
    """
    Wraps the Claude API agentic loop with tool use.
    Subclasses configure their own system prompt and tool subset.
    """

    name: str = "base"
    tools: list[dict] = []
    max_tokens: int = 8192

    def __init__(self) -> None:
        self.client = _client

    def _system_prompt(self) -> str:  # override in subclasses
        return "You are a QE agent."

    def run(self, user_message: str, context: dict[str, Any] | None = None) -> str:
        """
        Run the agentic loop until Claude returns end_turn.
        Returns the final text response.
        """
        messages: list[dict] = [{"role": "user", "content": user_message}]
        if context:
            ctx_str = json.dumps(context, default=str)
            messages[0]["content"] = f"<context>\n{ctx_str}\n</context>\n\n{user_message}"

        console.print(f"[bold cyan]▶ {self.name}[/bold cyan] starting...")

        while True:
            # Stream the response (handles large outputs without timeout)
            with self.client.messages.stream(
                model=config.CLAUDE_MODEL,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},
                system=self._system_prompt(),
                tools=self.tools,
                messages=messages,
            ) as stream:
                response = stream.get_final_message()

            if response.stop_reason == "end_turn":
                text = next(
                    (b.text for b in response.content if b.type == "text"), ""
                )
                console.print(f"[bold green]✓ {self.name}[/bold green] done")
                return text

            if response.stop_reason == "max_tokens":
                # Response was truncated — return what we have so parsers can try to recover
                console.print(f"[yellow]⚠ {self.name}[/yellow] hit max_tokens limit")
                return next((b.text for b in response.content if b.type == "text"), "")

            if response.stop_reason != "tool_use":
                # Unexpected stop — return whatever text we have
                return next((b.text for b in response.content if b.type == "text"), "")

            # Execute tool calls
            tool_results: list[dict] = []
            for block in response.content:
                if block.type == "tool_use":
                    console.print(
                        f"  [yellow]⚙ tool:[/yellow] {block.name} "
                        f"[dim]{json.dumps(block.input)[:80]}[/dim]"
                    )
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
