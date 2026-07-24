import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from flywheel import probes
from flywheel.config import config_path, load_settings, save_settings
from flywheel.storage import Ledger

app = typer.Typer(
    name="flywheel",
    help="Autonomous software factory: board -> agent -> tested PR -> preview environment.",
    no_args_is_help=False,
    add_completion=False,
)
console = Console()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        from flywheel.tui.app import FlywheelApp

        FlywheelApp().run()


@app.command()
def doctor() -> None:
    settings = load_settings()
    results = probes.run_all(settings)

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("")
    table.add_column("Check", style="bold")
    table.add_column("Detail")

    for result in results:
        table.add_row(result.icon, result.name, result.detail)

    console.print()
    console.print(Panel(table, title="Flywheel readiness", border_style="cyan", padding=(1, 2)))

    problems = [result for result in results if not result.ok and result.fix]
    if problems:
        console.print("\n[bold]How to fix[/bold]\n")
        for result in problems:
            marker = "optional" if result.optional else "required"
            console.print(f"  [{marker}] {result.name}: {result.fix}")

    blocking = [result for result in results if not result.ok and not result.optional]
    console.print()
    if blocking:
        count = len(blocking)
        console.print(
            f"[red]{count} required check(s) failing.[/red] Run [bold]flywheel setup[/bold]."
        )
        raise typer.Exit(code=1)
    console.print("[green]All required checks passed.[/green]")


@app.command()
def setup() -> None:
    from flywheel.tui.app import FlywheelApp

    FlywheelApp(force_wizard=True).run()


@app.command("config-path")
def show_config_path() -> None:
    console.print(str(config_path()))


@app.command()
def login(
    token: str = typer.Option("", "--token", "-t", help="GitHub PAT. Prompts securely if omitted."),
) -> None:
    if not token:
        console.print("Paste a GitHub PAT (repo + project scopes). Input is hidden.")
        token = typer.prompt("Token", hide_input=True)
    token = token.strip()
    result = probes.probe_github_token(token)
    if not result.ok:
        console.print(f"[red]{result.detail}[/red]")
        if result.fix:
            console.print(f"[dim]{result.fix}[/dim]")
        raise typer.Exit(code=1)
    settings = load_settings()
    settings.github.token = token
    detected = probes.detect_github_login(token)
    if detected:
        settings.github.owner = detected
    save_settings(settings)
    console.print(f"[green]Saved. {result.detail}.[/green]")
    console.print("Next: [bold]flywheel board[/bold] to pick your project board.")


@app.command()
def board(
    owner: str = typer.Option("", "--owner", "-o", help="User or org that owns the board."),
    number: int = typer.Option(0, "--number", "-n", help="Board number. Prompts if omitted."),
) -> None:
    settings = load_settings()
    if not settings.github.token:
        console.print("[red]No token configured.[/red] Run [bold]flywheel login[/bold] first.")
        raise typer.Exit(code=1)
    owner = (owner or settings.github.owner).strip()
    if not owner:
        owner = typer.prompt("Owner (GitHub user or org)").strip()
    settings.github.owner = owner

    boards = probes.list_boards(settings.github.token, owner)
    if not boards:
        console.print(
            f"[red]No open boards found for '{owner}'.[/red]\n"
            "[dim]If you have one, the token is likely missing the 'project' scope. "
            "Regenerate it at github.com/settings/tokens with 'project' ticked.[/dim]"
        )
        raise typer.Exit(code=1)

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Number")
    table.add_column("Title")
    for entry in boards:
        table.add_row(str(entry["number"]), str(entry["title"]))
    console.print(table)

    if number <= 0:
        number = int(typer.prompt("Board number"))
    match = next((entry for entry in boards if entry["number"] == number), None)
    if match is None:
        console.print(f"[red]No board numbered {number} for '{owner}'.[/red]")
        raise typer.Exit(code=1)

    settings.github.project_number = number
    settings.github.project_title = str(match["title"])
    settings.setup_completed = True
    save_settings(settings)
    console.print(f"[green]Board set to #{number} {match['title']}.[/green]")
    console.print(
        "You're ready. Start with [bold]flywheel loop[/bold] or open [bold]flywheel gui[/bold]."
    )


def _print_event(run_id: str, kind: str, payload: dict[str, object]) -> None:
    label = {
        "agent_start": "[cyan]start[/cyan]",
        "tool": "[dim]tool[/dim]",
        "text": "[white]say[/white]",
        "needs_input": "[yellow]asks[/yellow]",
        "pull_request": "[green]pr[/green]",
        "failed": "[red]fail[/red]",
        "review_start": "[cyan]review[/cyan]",
        "scaffolded": "[cyan]scaffold[/cyan]",
    }.get(kind, f"[dim]{kind}[/dim]")
    detail = payload.get("name") or payload.get("text") or payload.get("error") or ""
    console.print(f"  {run_id[:8]} {label} {str(detail)[:120]}")


def _run_tick(once: bool, interval: int) -> None:
    import time

    from flywheel import bootstrap

    settings = load_settings()
    container = bootstrap.build(settings, on_event=_print_event)
    try:
        while True:
            runs = container.dispatcher.tick()
            if runs:
                for run in runs:
                    console.print(
                        f"[bold]{run.id[:8]}[/bold] #{run.issue_number} {run.repository} "
                        f"-> {run.status.value}"
                    )
            else:
                console.print("[dim]nothing to do[/dim]")
            if once:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]stopped[/yellow]")
    finally:
        container.close()


@app.command("run-once")
def run_once() -> None:
    _run_tick(once=True, interval=0)


@app.command()
def loop(
    interval: int = typer.Option(0, help="Seconds between polls (defaults to your config)."),
) -> None:
    settings = load_settings()
    _run_tick(once=False, interval=interval or settings.loop.poll_interval_seconds)


@app.command()
def gui(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8765, help="Port."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser."),
) -> None:
    import threading
    import webbrowser

    import uvicorn

    url = f"http://{host}:{port}"
    console.print(f"Opening the Flywheel GUI at [bold]{url}[/bold]  (Ctrl+C to stop)")
    if not no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("flywheel.gui.server:app", host=host, port=port, log_level="warning")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8787, help="Port."),
) -> None:
    import uvicorn

    uvicorn.run("flywheel.api:app", host=host, port=port)


@app.command("build-runner")
def build_runner() -> None:
    import subprocess
    from pathlib import Path

    settings = load_settings()
    root = Path(__file__).resolve().parents[2]
    console.print(f"Building [bold]{settings.runner.image}[/bold] ...")
    result = subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(root / "docker" / "runner.Dockerfile"),
            "-t",
            settings.runner.image,
            str(root),
        ],
        check=False,
    )
    raise typer.Exit(code=result.returncode)


@app.command()
def runs(limit: int = typer.Option(20, help="How many runs to show.")) -> None:
    settings = load_settings()
    ledger = Ledger(settings.database_path)
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    for column in ("Run", "Issue", "Repository", "Agent", "Status", "Phase", "Cost"):
        table.add_column(column)
    for run in ledger.list_runs(limit):
        table.add_row(
            run.id[:8],
            f"#{run.issue_number}",
            run.repository,
            run.agent.value,
            run.status.value,
            run.phase.value,
            f"${run.cost_usd:.2f}",
        )
    ledger.close()
    console.print(table)
