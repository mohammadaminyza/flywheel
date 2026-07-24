from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static, TextArea

from flywheel import bootstrap
from flywheel.config import Settings
from flywheel.domain.run import Run
from flywheel.storage import Ledger


class QuestionsScreen(Screen[None]):
    BINDINGS = [("escape", "back", "Back"), ("r", "refresh", "Refresh")]

    def __init__(self, settings: Settings, ledger: Ledger) -> None:
        super().__init__()
        self.settings = settings
        self.ledger = ledger
        self._selected: Run | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with VerticalScroll(id="question-list", classes="board-column"):
                yield Static("Waiting on you", classes="column-title")
            with Vertical(id="question-detail", classes="board-column"):
                yield Static("Select a question on the left.", id="question-body")
                yield TextArea(id="answer-box")
                yield Button("Post answer and resume", id="post-answer", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Questions inbox"
        self.refresh_list()

    def refresh_list(self) -> None:
        column = self.query_one("#question-list", VerticalScroll)
        column.remove_children()
        column.mount(Static("Waiting on you", classes="column-title"))
        waiting = self.ledger.awaiting_input()
        if not waiting:
            column.mount(Static("nothing pending", classes="hint"))
            return
        for run in waiting:
            button = Button(
                f"#{run.issue_number} {run.repository}", id=f"q-{run.id}", variant="default"
            )
            column.mount(button)

    @on(Button.Pressed)
    def handle_button(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("q-"):
            run_id = button_id[2:]
            self._select(run_id)
        elif button_id == "post-answer":
            self._post_answer()

    def _select(self, run_id: str) -> None:
        run = self.ledger.get(run_id)
        if run is None:
            return
        self._selected = run
        events = self.ledger.events(run_id)
        questions = next(
            (
                event["payload"].get("questions")
                for event in reversed(events)
                if event["kind"] == "needs_input"
            ),
            None,
        )
        lines = [f"[bold]#{run.issue_number} {run.repository}[/bold]", ""]
        if questions:
            for index, question in enumerate(questions, start=1):
                lines.append(f"{index}. {question}")
        else:
            lines.append("The agent is waiting for input.")
        lines.append("\n[dim]Type your answer below and post it.[/dim]")
        self.query_one("#question-body", Static).update("\n".join(lines))
        self.query_one("#answer-box", TextArea).text = ""

    def _post_answer(self) -> None:
        run = self._selected
        if run is None:
            self.notify("Select a question first.", severity="warning")
            return
        answer = self.query_one("#answer-box", TextArea).text.strip()
        if not answer:
            self.notify("Write an answer first.", severity="warning")
            return
        self.query_one("#question-body", Static).update("Posting...")
        self._deliver(run, answer)

    @work(thread=True)
    def _deliver(self, run: Run, answer: str) -> None:
        container = bootstrap.build(self.settings)
        try:
            tasks = {task.project_item_id: task for task in container.board.tasks()}
            task = tasks.get(run.project_item_id)
            if task is None:
                self.app.call_from_thread(
                    self.notify, "That card is no longer on the board.", severity="error"
                )
                return
            container.client.post(
                f"/repos/{task.repository.full_name}/issues/{task.issue_number}/comments",
                {"body": answer},
            )
        finally:
            container.close()
        self.app.call_from_thread(self._answered)

    def _answered(self) -> None:
        self.notify("Answer posted. The agent resumes on the next poll.", severity="information")
        self.refresh_list()
        self.query_one("#question-body", Static).update("Answer posted.")
        self._selected = None

    def action_refresh(self) -> None:
        self.refresh_list()

    def action_back(self) -> None:
        self.app.pop_screen()
