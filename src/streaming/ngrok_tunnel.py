"""
ngrok tunnel — expose the local webhook server to the internet for development.

Usage:
  python -m src.main --serve --ngrok

Requires:
  pip install pyngrok
  NGROK_AUTH_TOKEN set in .env  (free account is enough for basic usage)

Optional:
  NGROK_DOMAIN=your-reserved.ngrok.app  (paid plan — gives a stable URL)
"""

from __future__ import annotations

import atexit

from rich.console import Console
from rich.panel import Panel

from ..config import config

console = Console()

_active_tunnel = None


def open_tunnel(port: int) -> str:
    """
    Open an ngrok HTTPS tunnel to localhost:port.
    Returns the public URL (e.g. https://abc123.ngrok.app).
    Registers cleanup on process exit.
    """
    try:
        from pyngrok import ngrok, conf, exception as ngrok_exc
    except ImportError:
        console.print(
            "[bold red]pyngrok not installed.[/bold red] "
            "Run: pip install pyngrok"
        )
        raise

    # Configure auth token if provided
    if config.NGROK_AUTH_TOKEN:
        conf.get_default().auth_token = config.NGROK_AUTH_TOKEN
    else:
        console.print(
            "[yellow]⚠ NGROK_AUTH_TOKEN not set — using anonymous tunnel "
            "(limited to 1 concurrent connection, no custom domain).[/yellow]"
        )

    # Build tunnel options
    options: dict = {"addr": port, "proto": "http"}
    if config.NGROK_DOMAIN:
        options["hostname"] = config.NGROK_DOMAIN

    try:
        tunnel = ngrok.connect(**options)
    except ngrok_exc.PyngrokNgrokError as e:
        console.print(f"[bold red]ngrok failed to start:[/bold red] {e}")
        raise

    global _active_tunnel
    _active_tunnel = tunnel
    atexit.register(_close_tunnel)

    public_url: str = tunnel.public_url
    # Always use https
    if public_url.startswith("http://"):
        public_url = "https://" + public_url[7:]

    _print_setup_instructions(public_url)
    return public_url


def _close_tunnel() -> None:
    global _active_tunnel
    if _active_tunnel is not None:
        try:
            from pyngrok import ngrok
            ngrok.disconnect(_active_tunnel.public_url)
        except Exception:
            pass
        _active_tunnel = None


def close_tunnel() -> None:
    """Explicitly close the tunnel (called on graceful shutdown)."""
    _close_tunnel()


def _print_setup_instructions(public_url: str) -> None:
    github_url = f"{public_url}/webhook/github"
    jira_url = f"{public_url}/webhook/jira"
    health_url = f"{public_url}/health"

    instructions = (
        f"[bold]Public URL:[/bold] [link={public_url}]{public_url}[/link]\n\n"
        "[bold]GitHub Webhook Setup[/bold]\n"
        "  1. Go to your repo → [bold]Settings → Webhooks → Add webhook[/bold]\n"
        f"  2. Payload URL:    [cyan]{github_url}[/cyan]\n"
        "  3. Content type:  [cyan]application/json[/cyan]\n"
        f"  4. Secret:        [cyan]{config.GITHUB_WEBHOOK_SECRET or '<set GITHUB_WEBHOOK_SECRET>'}[/cyan]\n"
        "  5. Events:        [cyan]Pull requests, Check runs, Workflow runs[/cyan]\n\n"
        "[bold]Endpoints[/bold]\n"
        f"  GitHub webhook:  [cyan]{github_url}[/cyan]\n"
        f"  Jira webhook:    [cyan]{jira_url}[/cyan]\n"
        f"  Health check:    [cyan]{health_url}[/cyan]\n"
        f"  SSE stream:      [cyan]{public_url}/events[/cyan]"
    )

    console.print(Panel(instructions, title="🌐 ngrok tunnel active", border_style="green"))
