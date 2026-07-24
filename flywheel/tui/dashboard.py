from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from flywheel import probes
from flywheel.config import Settings
from flywheel.domain.enums import RunStatus
from flywheel.services.loop_controller import LoopController
from flywheel.storage import Ledger
from flywheel.tui.questions import QuestionsScreen

COLUMNS = [
    ("Queued", (RunStatus.PENDING,)),
    ("Working", (RunStatus.RUNNING, RunStatus.REVIEWING)),
    ("Needs you", (RunStatus.NEEDS_INPUT,)),
    ("Shipped", (RunStatus.SUCCEEDED,)),
    ("Failed", (RunStatus.FAILED, RunStatus.CANCELLED)),
]


class DashboardScreen(Screen[None]):
    BINDINGS = [
        ("s", "toggle_loop", "Start/pause"),
        ("r", "refresh", "Refresh"),
        ("a", "answer", "Questions"),
        ("w", "wizard", "Settings"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, settings: Settings, ledger: Ledger) -> None:
        super().__init__()
        self.settings = settings
        self.ledger = ledger
        self.controller = LoopController(settings, status_sink=self._on_status)
        self._last_event = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="board-columns"):
            for title, _ in COLUMNS:
                with VerticalScroll(classes="board-column", id=f"col-{title.replace(' ', '-')}"):
                    yield Static(title, classes="column-title")
        yield Static(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Flywheel"
        self.sub_title = self.settings.github.project_title or "no board connected"
        self.refresh_board()
        self.set_interval(3, self.refresh_board)

    def _on_status(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "error":
            self._last_event = f"error: {payload.get('error', '')[:80]}"
        elif kind == "event":
            self._last_event = f"{payload.get('kind', '')} #{payload.get('run_id', '')[:8]}"
        elif kind == "tick":
            self._last_event = f"polled ({payload.get('count', 0)} started)"

    def refresh_board(self) -> None:
        runs = self.ledger.list_runs(200)
        for title, statuses in COLUMNS:
            column = self.query_one(f"#col-{title.replace(' ', '-')}", VerticalScroll)
            column.remove_children()
            column.mount(Static(title, classes="column-title"))
            matching = [run for run in runs if run.status in statuses]
            if not matching:
                column.mount(Static("empty", classes="hint"))
                continue
            for run in matching:
                column.mount(
                    Static(
                        f"#{run.issue_number} {run.repository}\n"
                        f"[dim]{run.agent.value} - {run.phase.value} - ${run.cost_usd:.2f}[/dim]"
                    )
                )
        self.update_status()

    def update_status(self) -> None:
        state = "[green]running[/green]" if self.controller.running else "paused"
        board = self.settings.github.project_title or "not connected"
        active = self.ledger.active_count()
        waiting = len(self.ledger.awaiting_input())
        event = f"   [dim]{self._last_event}[/dim]" if self._last_event else ""
        self.query_one("#status-bar", Static).update(
            f" loop: {state}   board: {board}   active: {active}   waiting: {waiting}{event}"
        )

    def action_toggle_loop(self) -> None:
        if self.controller.running:
            self.controller.stop()
            self.notify("Loop paused.", severity="information")
            self.update_status()
            return
        if not self.settings.github.configured:
            self.notify("Connect a board first (press w).", severity="warning")
            return
        blocking = [r for r in probes.run_all(self.settings) if not r.ok and not r.optional]
        if blocking:
            names = ", ".join(result.name for result in blocking)
            self.notify(f"Not ready: {names}. Press w to fix.", severity="error")
            return
        self.controller.start()
        self.notify("Loop started.", severity="information")
        self.update_status()

    def action_refresh(self) -> None:
        self.controller.poke()
        self.refresh_board()

    def action_answer(self) -> None:
        self.app.push_screen(QuestionsScreen(self.settings, self.ledger))

    def action_wizard(self) -> None:
        from flywheel.tui.app import FlywheelApp

        app = self.app
        if isinstance(app, FlywheelApp):
            app.action_open_wizard()

    def on_unmount(self) -> None:
        self.controller.stop()
