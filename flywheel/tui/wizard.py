from dataclasses import dataclass
from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from flywheel import probes
from flywheel.config import Settings, save_settings
from flywheel.domain.enums import AgentKind, ExecutionMode, Transport


@dataclass
class Step:
    key: str
    title: str
    help: str


STEPS = [
    Step("welcome", "Welcome", "This wizard connects your board, your agents and your servers."),
    Step(
        "github", "GitHub access", "Paste a personal access token with 'repo' and 'project' scopes."
    ),
    Step("board", "Project board", "Pick the Projects board the factory should watch for work."),
    Step(
        "agents",
        "Coding agents",
        "Detected on this machine. The board's Agent field picks per task.",
    ),
    Step(
        "runner",
        "How work runs",
        "Containers keep each task isolated; host mode uses the installed CLIs.",
    ),
    Step("template", "Default template", "Used when a repository is empty and needs scaffolding."),
    Step(
        "deploy",
        "Preview environments",
        "Optional. A Docker host gives every pull request a live URL.",
    ),
    Step("telegram", "Notifications", "Optional. Get screenshots and preview links on your phone."),
    Step("done", "Ready", "Everything is saved. You can change any of this later in Settings."),
]


class WizardScreen(Screen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.index = 0
        self._boards: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="wizard-body"):
            yield Vertical(id="stepper")
            with VerticalScroll(id="step-panel"):
                yield Static(id="step-title")
                yield Static(id="step-help")
                yield Vertical(id="step-content")
                yield Static(id="probe-output")
        with Horizontal(id="wizard-buttons"):
            yield Button("Back", id="back", variant="default")
            yield Button("Next", id="next", variant="primary")

    def on_mount(self) -> None:
        self.render_step()

    @property
    def step(self) -> Step:
        return STEPS[self.index]

    def render_step(self) -> None:
        stepper = self.query_one("#stepper", Vertical)
        stepper.remove_children()
        for position, step in enumerate(STEPS):
            if position < self.index:
                style, marker = "step-done", "[x]"
            elif position == self.index:
                style, marker = "step-active", "[>]"
            else:
                style, marker = "step-todo", "[ ]"
            stepper.mount(Static(f"{marker} {step.title}", classes=style))

        self.query_one("#step-title", Static).update(self.step.title)
        self.query_one("#step-help", Static).update(self.step.help)
        self.query_one("#probe-output", Static).update("")

        content = self.query_one("#step-content", Vertical)
        content.remove_children()
        builder = getattr(self, f"_build_{self.step.key}", None)
        if builder:
            builder(content)

        self.query_one("#back", Button).disabled = self.index == 0
        self.query_one("#next", Button).label = "Finish" if self.step.key == "done" else "Next"
        self.call_after_refresh(self._focus_first_field)

    def _focus_first_field(self) -> None:
        for widget in self.query("#step-content Input, #step-content Select"):
            widget.focus()
            return

    def _build_welcome(self, content: Vertical) -> None:
        content.mount(
            Static(
                "The factory watches a GitHub Projects board. When a card is marked Todo and\n"
                "assigned to an agent, it checks out the repository, writes the feature and its\n"
                "tests in an isolated container, opens a pull request, deploys a preview and\n"
                "sends you the link.\n\n"
                "If the agent needs a decision, it asks you in the issue comments and waits.\n\n"
                "Nothing here needs programming knowledge. Press Next to begin.",
                classes="hint",
            )
        )

    def _build_github(self, content: Vertical) -> None:
        content.mount(Label("Personal access token", classes="field-label"))
        content.mount(
            Input(
                value=self.settings.github.token,
                password=True,
                placeholder="ghp_... or github_pat_...",
                id="github-token",
            )
        )
        content.mount(
            Static(
                "Create one at github.com/settings/tokens -> classic,\n"
                "ticking 'repo' and 'project'.",
                classes="hint",
            )
        )
        content.mount(Button("Test connection", id="test-github", variant="default"))

    def _build_board(self, content: Vertical) -> None:
        content.mount(Label("Owner (user or organisation)", classes="field-label"))
        content.mount(
            Input(
                value=self.settings.github.owner,
                placeholder="your-github-username",
                id="board-owner",
            )
        )
        content.mount(Button("Load boards", id="load-boards", variant="default"))
        content.mount(Label("Board", classes="field-label"))
        content.mount(
            Select[int](options=[], prompt="Load boards first", id="board-select", allow_blank=True)
        )

    def _build_agents(self, content: Vertical) -> None:
        for kind in (AgentKind.CLAUDE_CODE, AgentKind.CODEX):
            result = probes.probe_agent(kind)
            settings = self.settings.agent(kind)
            style = "ok" if result.ok else ("warn" if result.optional else "bad")
            content.mount(Label(kind.value, classes="field-label"))
            content.mount(Static(f"{result.detail}", classes=style))
            if not result.ok and result.fix:
                content.mount(Static(f"Fix: {result.fix}", classes="hint"))
            content.mount(
                Select[str](
                    options=[
                        ("Command line (always works)", Transport.CLI.value),
                        ("MCP server (delegates as a tool)", Transport.MCP.value),
                    ],
                    value=settings.transport.value,
                    id=f"transport-{kind.name}",
                    allow_blank=False,
                )
            )

    def _build_runner(self, content: Vertical) -> None:
        content.mount(Label("Where tasks run", classes="field-label"))
        content.mount(
            Select[str](
                options=[
                    (
                        "Docker container per task (isolated, recommended)",
                        ExecutionMode.CONTAINER.value,
                    ),
                    ("Directly on this machine (simplest)", ExecutionMode.HOST.value),
                ],
                value=self.settings.runner.execution_mode.value,
                id="execution-mode",
                allow_blank=False,
            )
        )
        content.mount(Label("Tasks at the same time", classes="field-label"))
        content.mount(Input(value=str(self.settings.runner.max_parallel), id="max-parallel"))
        content.mount(
            Static(
                "Subscription logins share one credential file, so the flywheel runs one task per\n"
                "agent at a time to avoid a token refresh clash.",
                classes="hint",
            )
        )

    def _build_template(self, content: Vertical) -> None:
        path = self.settings.templates_path
        available: list[tuple[str, str]] = []
        if path.exists():
            for child in sorted(path.iterdir()):
                if (child / "template.yml").exists():
                    available.append((child.name, child.name))
        if not available:
            available = [(self.settings.default_template, self.settings.default_template)]
        content.mount(Label("Template for empty repositories", classes="field-label"))
        content.mount(
            Select[str](
                options=available,
                value=self.settings.default_template
                if self.settings.default_template in dict(available)
                else available[0][1],
                id="default-template",
                allow_blank=False,
            )
        )
        content.mount(
            Static(
                "A project can override this with a Template field on its board card, or a\n"
                ".factory/project.yml file in the repository.",
                classes="hint",
            )
        )

    def _build_deploy(self, content: Vertical) -> None:
        content.mount(Label("Enable preview environments", classes="field-label"))
        content.mount(Switch(value=self.settings.deploy.configured, id="deploy-enabled"))
        content.mount(Label("Docker host", classes="field-label"))
        content.mount(
            Input(
                value=self.settings.deploy.host, placeholder="deploy.example.com", id="deploy-host"
            )
        )
        content.mount(Label("SSH user", classes="field-label"))
        content.mount(Input(value=self.settings.deploy.user, id="deploy-user"))
        content.mount(Label("SSH key path", classes="field-label"))
        content.mount(
            Input(
                value=self.settings.deploy.ssh_key_path,
                placeholder="~/.ssh/id_ed25519",
                id="deploy-key",
            )
        )
        content.mount(Label("Wildcard domain", classes="field-label"))
        content.mount(
            Input(value=self.settings.deploy.domain, placeholder="example.com", id="deploy-domain")
        )
        content.mount(
            Static(
                "Pull requests get https://pr-12.dev.<domain>, tags get stage and production.",
                classes="hint",
            )
        )
        content.mount(Button("Test connection", id="test-deploy", variant="default"))

    def _build_telegram(self, content: Vertical) -> None:
        content.mount(Label("Bot token", classes="field-label"))
        content.mount(
            Input(
                value=self.settings.telegram.bot_token,
                password=True,
                placeholder="from @BotFather",
                id="tg-token",
            )
        )
        content.mount(Label("Chat id", classes="field-label"))
        content.mount(
            Input(value=self.settings.telegram.chat_id, placeholder="123456789", id="tg-chat")
        )
        content.mount(Button("Send test message", id="test-telegram", variant="default"))

    def _build_done(self, content: Vertical) -> None:
        results = probes.run_all(self.settings)
        for result in results:
            style = "ok" if result.ok else ("warn" if result.optional else "bad")
            mark = "OK  " if result.ok else ("--  " if result.optional else "XX  ")
            content.mount(Static(f"{mark}{result.name}: {result.detail}", classes=style))
        content.mount(
            Static(
                "\nPress Finish to open the dashboard. Optional items can be added later.",
                classes="hint",
            )
        )

    def _selected(self, selector: str) -> int | str | None:
        value = self.query_one(selector, Select).value
        return value if isinstance(value, int | str) else None

    def _collect(self) -> None:
        key = self.step.key
        if key == "github":
            self.settings.github.token = self.query_one("#github-token", Input).value.strip()
        elif key == "board":
            self.settings.github.owner = self.query_one("#board-owner", Input).value.strip()
            selected = self._selected("#board-select")
            if selected is not None:
                number = int(selected)
                self.settings.github.project_number = number
                for board in self._boards:
                    if board["number"] == number:
                        self.settings.github.project_title = str(board["title"])
        elif key == "agents":
            for kind in (AgentKind.CLAUDE_CODE, AgentKind.CODEX):
                selected = self._selected(f"#transport-{kind.name}")
                if selected is not None:
                    self.settings.agents[kind].transport = Transport(str(selected))
        elif key == "runner":
            mode = self._selected("#execution-mode")
            if mode is not None:
                self.settings.runner.execution_mode = ExecutionMode(str(mode))
            raw = self.query_one("#max-parallel", Input).value.strip()
            if raw.isdigit():
                self.settings.runner.max_parallel = max(1, int(raw))
        elif key == "template":
            selected = self._selected("#default-template")
            if selected is not None:
                self.settings.default_template = str(selected)
        elif key == "deploy":
            self.settings.deploy.host = self.query_one("#deploy-host", Input).value.strip()
            self.settings.deploy.user = (
                self.query_one("#deploy-user", Input).value.strip() or "deploy"
            )
            self.settings.deploy.ssh_key_path = self.query_one("#deploy-key", Input).value.strip()
            self.settings.deploy.domain = self.query_one("#deploy-domain", Input).value.strip()
        elif key == "telegram":
            self.settings.telegram.bot_token = self.query_one("#tg-token", Input).value.strip()
            self.settings.telegram.chat_id = self.query_one("#tg-chat", Input).value.strip()

    def _report(self, result: probes.ProbeResult) -> None:
        style = "ok" if result.ok else "bad"
        text = f"[{style}]{result.detail}[/{style}]"
        if not result.ok and result.fix:
            text += f"\n[dim]{result.fix}[/dim]"
        self.query_one("#probe-output", Static).update(text)

    @on(Button.Pressed, "#test-github")
    def test_github(self) -> None:
        self._collect()
        self.query_one("#probe-output", Static).update("Checking...")
        self._probe_github(self.settings.github.token)

    @work(thread=True)
    def _probe_github(self, token: str) -> None:
        result = probes.probe_github_token(token)
        self.app.call_from_thread(self._report, result)
        if result.ok and not self.settings.github.owner:
            login = probes.detect_github_login(token)
            if login:
                self.settings.github.owner = login

    @on(Button.Pressed, "#load-boards")
    def load_boards(self) -> None:
        self._collect()
        self.query_one("#probe-output", Static).update("Loading boards...")
        self._fetch_boards(self.settings.github.token, self.settings.github.owner)

    @work(thread=True)
    def _fetch_boards(self, token: str, owner: str) -> None:
        boards = probes.list_boards(token, owner)
        self.app.call_from_thread(self._apply_boards, boards)

    def _apply_boards(self, boards: list[dict[str, Any]]) -> None:
        self._boards = boards
        select = self.query_one("#board-select", Select)
        if not boards:
            self.query_one("#probe-output", Static).update(
                "[bad]No open boards found for that owner.[/bad]\n"
                "[dim]Create one at github.com/<owner>?tab=projects, or check the owner name.[/dim]"
            )
            return
        select.set_options([(f"#{b['number']}  {b['title']}", b["number"]) for b in boards])
        current = self.settings.github.project_number
        if any(b["number"] == current for b in boards):
            select.value = current
        self.query_one("#probe-output", Static).update(
            f"[ok]Found {len(boards)} board(s). Pick one above.[/ok]"
        )

    @on(Button.Pressed, "#test-deploy")
    def test_deploy(self) -> None:
        self._collect()
        self.query_one("#probe-output", Static).update("Connecting...")
        deploy = self.settings.deploy
        self._probe_deploy(deploy.host, deploy.user, deploy.ssh_key_path)

    @work(thread=True)
    def _probe_deploy(self, host: str, user: str, key: str) -> None:
        result = probes.probe_deploy_host(host, user, key)
        self.app.call_from_thread(self._report, result)

    @on(Button.Pressed, "#test-telegram")
    def test_telegram(self) -> None:
        self._collect()
        self.query_one("#probe-output", Static).update("Sending...")
        self._probe_telegram(self.settings.telegram.bot_token, self.settings.telegram.chat_id)

    @work(thread=True)
    def _probe_telegram(self, token: str, chat_id: str) -> None:
        result = probes.probe_telegram(token, chat_id)
        self.app.call_from_thread(self._report, result)

    @on(Button.Pressed, "#back")
    def go_back(self) -> None:
        self._collect()
        if self.index > 0:
            self.index -= 1
            self.render_step()

    @on(Button.Pressed, "#next")
    def go_next(self) -> None:
        self._collect()
        if self.step.key == "done":
            self.settings.setup_completed = True
            save_settings(self.settings)
            self.dismiss(True)
            return
        self.index += 1
        self.render_step()

    def action_cancel(self) -> None:
        self.dismiss(False)
