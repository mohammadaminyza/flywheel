import uuid
from pathlib import Path
from typing import Any

import pytest

from flywheel.config import Settings
from flywheel.domain.enums import AgentKind, RunPhase, RunStatus, TaskStatus
from flywheel.domain.project import ProjectConfig, ReviewSpec
from flywheel.domain.result import AgentQuestion, AgentResult
from flywheel.domain.run import Run, RunOutcome
from flywheel.domain.task import Comment, Repository, Task
from flywheel.services.clarification_service import ClarificationService
from flywheel.services.dispatcher import Dispatcher
from flywheel.services.scaffold_service import ScaffoldService
from flywheel.storage import Ledger


class FakeBoard:
    def __init__(self, tasks: list[Task]) -> None:
        self._tasks = tasks
        self.statuses: list[tuple[int, TaskStatus]] = []

    def tasks(self) -> list[Task]:
        return self._tasks

    def claimable(self) -> list[Task]:
        return [task for task in self._tasks if task.status == TaskStatus.TODO]

    def set_status(self, task: Task, status: TaskStatus) -> None:
        self.statuses.append((task.issue_number, status))
        task.status = status


class FakeIssues:
    def __init__(self, reply: str | None = None) -> None:
        self.comments_posted: list[str] = []
        self.questions: list[list[AgentQuestion]] = []
        self._reply = reply

    def comment(self, repository: Repository, issue_number: int, body: str) -> str:
        self.comments_posted.append(body)
        return "c1"

    def ask(self, repository, issue_number, questions, agent, mention) -> str:  # type: ignore[no-untyped-def]
        self.questions.append(questions)
        return "q1"

    def human_reply_after(self, repository, issue_number, comment_id):  # type: ignore[no-untyped-def]
        if self._reply is None:
            return None
        from datetime import UTC, datetime

        return Comment(id="c2", author="mohammad", body=self._reply, created_at=datetime.now(UTC))


class FakePulls:
    def __init__(self) -> None:
        self.opened: list[dict[str, Any]] = []

    def find_open(self, repository: Repository, head: str) -> dict[str, Any] | None:
        return None

    def open(self, repository, head, base, title, body, draft=False):  # type: ignore[no-untyped-def]
        payload = {
            "number": 101,
            "html_url": "https://github.com/acme/api/pull/101",
            "title": title,
        }
        self.opened.append({**payload, "body": body, "head": head, "base": base})
        return payload

    def body_for(self, issue_number, result, agent, model, cost):  # type: ignore[no-untyped-def]
        return f"Closes #{issue_number}"


class FakeProjects:
    def __init__(self, config: ProjectConfig | None = None, empty: bool = False) -> None:
        self._config = config or ProjectConfig(review=ReviewSpec(enabled=False))
        self._empty = empty

    def load(self, repository: Repository) -> ProjectConfig:
        return self._config

    def is_empty(self, repository: Repository) -> bool:
        return self._empty


class FakeWorkspace:
    def __init__(self, path: Path, empty: bool = False, changes: bool = True) -> None:
        self.path = path
        self.is_empty_repository = empty
        self._changes = changes
        self.pushed: list[str] = []
        self.destroyed = False
        self.committed: list[str] = []
        path.mkdir(parents=True, exist_ok=True)

    def default_branch(self) -> str:
        return "main"

    def create_branch(self, branch: str) -> None:
        self.branch = branch

    def has_changes(self) -> bool:
        return False

    def commit_all(self, message: str) -> None:
        self.committed.append(message)

    def commits_ahead(self, base: str) -> int:
        return 1 if self._changes else 0

    def push(self, branch: str) -> None:
        self.pushed.append(branch)

    def destroy(self) -> None:
        self.destroyed = True


class FakeWorkspaceFactory:
    def __init__(self, root: Path, empty: bool = False, changes: bool = True) -> None:
        self._root = root
        self._empty = empty
        self._changes = changes
        self.created: list[FakeWorkspace] = []

    def prepare(self, repository: Repository, run_id: str) -> FakeWorkspace:
        workspace = FakeWorkspace(self._root / run_id, self._empty, self._changes)
        self.created.append(workspace)
        return workspace


class FakeRunner:
    def __init__(self, outcomes: list[RunOutcome]) -> None:
        self._outcomes = outcomes
        self.calls = 0
        self.specs: list[Any] = []

    def run(self, spec, on_event):  # type: ignore[no-untyped-def]
        self.specs.append(spec)
        outcome = self._outcomes[min(self.calls, len(self._outcomes) - 1)]
        self.calls += 1
        return outcome


def _task(status: TaskStatus = TaskStatus.TODO) -> Task:
    return Task(
        project_item_id="ITEM_1",
        issue_number=7,
        issue_node_id="NODE",
        title="Add health endpoint",
        body="Return version.",
        url="https://github.com/acme/api/issues/7",
        repository=Repository(owner="acme", name="api"),
        agent=AgentKind.CLAUDE_CODE,
        status=status,
    )


def _outcome(status: str = "completed", questions: list[AgentQuestion] | None = None) -> RunOutcome:
    return RunOutcome(
        result=AgentResult(status=status, summary="done", questions=questions or []),
        session_id="sess-1",
        cost_usd=0.25,
    )


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    created = Settings(workspace_dir=str(tmp_path / "ws"))
    created.github.token = "tok"
    created.github.owner = "acme"
    created.github.project_number = 1
    return created


def _build(
    settings: Settings,
    tmp_path: Path,
    tasks: list[Task],
    outcomes: list[RunOutcome],
    reply: str | None = None,
    empty: bool = False,
    changes: bool = True,
    project: ProjectConfig | None = None,
) -> tuple[Dispatcher, FakeBoard, FakeIssues, FakePulls, Ledger, FakeRunner, FakeWorkspaceFactory]:
    ledger = Ledger(tmp_path / "ledger.db")
    board = FakeBoard(tasks)
    issues = FakeIssues(reply)
    pulls = FakePulls()
    projects = FakeProjects(project, empty)
    workspaces = FakeWorkspaceFactory(tmp_path / "workspaces", empty, changes)
    runner = FakeRunner(outcomes)
    clarification = ClarificationService(issues, board, timeout_hours=48)  # type: ignore[arg-type]
    scaffold = ScaffoldService(tmp_path / "templates")
    dispatcher = Dispatcher(
        settings=settings,
        ledger=ledger,
        board=board,  # type: ignore[arg-type]
        issues=issues,  # type: ignore[arg-type]
        pulls=pulls,  # type: ignore[arg-type]
        projects=projects,  # type: ignore[arg-type]
        workspaces=workspaces,  # type: ignore[arg-type]
        clarification=clarification,
        scaffold=scaffold,
        runner_factory=lambda *args: runner,  # type: ignore[arg-type,return-value]
    )
    return dispatcher, board, issues, pulls, ledger, runner, workspaces


def test_successful_task_opens_pull_request(settings: Settings, tmp_path: Path) -> None:
    dispatcher, board, _, pulls, ledger, _, workspaces = _build(
        settings, tmp_path, [_task()], [_outcome()]
    )

    runs = dispatcher.tick()

    assert len(runs) == 1
    assert runs[0].status == RunStatus.SUCCEEDED
    assert runs[0].pull_request_number == 101
    assert pulls.opened[0]["head"] == "feat/7-add-health-endpoint"
    assert (7, TaskStatus.IN_PROGRESS) in board.statuses
    assert (7, TaskStatus.IN_REVIEW) in board.statuses
    assert workspaces.created[0].destroyed


def test_records_cost_and_session(settings: Settings, tmp_path: Path) -> None:
    dispatcher, _, _, _, _, _, _ = _build(settings, tmp_path, [_task()], [_outcome()])

    run = dispatcher.tick()[0]

    assert run.cost_usd == 0.25
    assert run.session_id == "sess-1"


def test_needs_input_parks_run_and_asks_on_the_issue(settings: Settings, tmp_path: Path) -> None:
    outcome = _outcome("needs_input", [AgentQuestion(question="Which auth?")])
    dispatcher, board, issues, pulls, _, _, _ = _build(settings, tmp_path, [_task()], [outcome])

    run = dispatcher.tick()[0]

    assert run.status == RunStatus.NEEDS_INPUT
    assert run.pending_question_comment_id == "q1"
    assert issues.questions[0][0].question == "Which auth?"
    assert (7, TaskStatus.NEEDS_INFO) in board.statuses
    assert pulls.opened == []


def test_answer_resumes_the_same_session(settings: Settings, tmp_path: Path) -> None:
    parked = _outcome("needs_input", [AgentQuestion(question="Which auth?")])
    dispatcher, _, _, pulls, _, runner, _ = _build(
        settings, tmp_path, [_task()], [parked, _outcome()], reply="Use JWT."
    )

    dispatcher.tick()
    resumed = dispatcher.tick()

    assert resumed[0].status == RunStatus.SUCCEEDED
    assert "Use JWT." in runner.specs[-1].prompt
    assert runner.specs[-1].resume_session_id == "sess-1"
    assert pulls.opened


def test_failed_result_returns_card_to_todo_for_retry(settings: Settings, tmp_path: Path) -> None:
    dispatcher, board, _, _, ledger, _, _ = _build(
        settings, tmp_path, [_task()], [_outcome("failed")]
    )

    run = dispatcher.tick()[0]

    assert run.status == RunStatus.FAILED
    assert (7, TaskStatus.TODO) in board.statuses
    assert not ledger.is_claimed("ITEM_1")


def test_blocks_after_max_attempts(settings: Settings, tmp_path: Path) -> None:
    settings.loop.max_attempts = 1
    dispatcher, board, issues, _, _, _, _ = _build(
        settings, tmp_path, [_task()], [_outcome("failed")]
    )

    dispatcher.tick()

    assert (7, TaskStatus.BLOCKED) in board.statuses
    assert any("could not complete this task" in body for body in issues.comments_posted)


def test_agent_committing_nothing_is_a_failure(settings: Settings, tmp_path: Path) -> None:
    dispatcher, _, _, pulls, _, _, _ = _build(
        settings, tmp_path, [_task()], [_outcome()], changes=False
    )

    run = dispatcher.tick()[0]

    assert run.status == RunStatus.FAILED
    assert "committed nothing" in (run.error or "")
    assert pulls.opened == []


def test_recovers_invalid_pull_request_base_without_rerunning_agent(
    settings: Settings, tmp_path: Path
) -> None:
    task = _task()
    dispatcher, board, issues, pulls, ledger, runner, _ = _build(
        settings, tmp_path, [task], [_outcome()]
    )
    ledger.save(
        Run(
            id="failed-delivery",
            project_item_id=task.project_item_id,
            issue_number=task.issue_number,
            repository=task.repository.full_name,
            agent=task.agent,
            status=RunStatus.FAILED,
            phase=RunPhase.FINISHED,
            attempt=settings.loop.max_attempts,
            branch=task.branch_name,
            error="POST /pulls returned 422: field base is invalid",
        )
    )

    recovered = dispatcher.tick()

    assert recovered[0].status == RunStatus.SUCCEEDED
    assert recovered[0].pull_request_number == 101
    assert recovered[0].error is None
    assert runner.calls == 0
    assert pulls.opened[0]["base"] == "main"
    assert (task.issue_number, TaskStatus.IN_REVIEW) in board.statuses
    assert any("Repairing pull request delivery" in body for body in issues.comments_posted)


def test_claim_prevents_double_dispatch(settings: Settings, tmp_path: Path) -> None:
    task = _task()
    dispatcher, _, _, pulls, ledger, runner, _ = _build(settings, tmp_path, [task], [_outcome()])

    dispatcher.tick()
    before = runner.calls
    dispatcher.tick()

    assert runner.calls == before


def test_respects_parallel_capacity(settings: Settings, tmp_path: Path) -> None:
    settings.runner.max_parallel = 0
    dispatcher, _, _, pulls, _, runner, _ = _build(settings, tmp_path, [_task()], [_outcome()])

    runs = dispatcher.tick()

    assert runs == []
    assert runner.calls == 0


def test_review_pass_runs_when_enabled(settings: Settings, tmp_path: Path) -> None:
    project = ProjectConfig(review=ReviewSpec(enabled=True))
    dispatcher, _, _, _, _, runner, _ = _build(
        settings, tmp_path, [_task()], [_outcome(), _outcome()], project=project
    )

    dispatcher.tick()

    assert runner.calls == 2
    assert "Review your own change" in runner.specs[1].prompt


def test_empty_repository_prompt_asks_for_scaffolding(settings: Settings, tmp_path: Path) -> None:
    templates = tmp_path / "templates" / "python-fastapi-nextjs" / "template"
    templates.mkdir(parents=True)
    (templates / "README.md").write_text("# {{project_name}}", encoding="utf-8")
    (templates.parent / "template.yml").write_text("id: python-fastapi-nextjs\n", encoding="utf-8")

    dispatcher, _, _, _, _, runner, workspaces = _build(
        settings, tmp_path, [_task()], [_outcome()], empty=True
    )

    dispatcher.tick()

    assert "Scaffold a new project" in runner.specs[0].prompt
    assert workspaces.created[0].committed[0] == "chore: initialize project scaffold"
    assert workspaces.created[0].pushed[0] == "main"
    assert workspaces.created[0].pushed[1] == "feat/7-add-health-endpoint"


def test_run_id_is_unique_per_attempt(settings: Settings, tmp_path: Path) -> None:
    dispatcher, _, _, _, ledger, _, _ = _build(settings, tmp_path, [_task()], [_outcome("failed")])

    first = dispatcher.tick()[0]
    dispatcher._board._tasks[0].status = TaskStatus.TODO  # type: ignore[attr-defined]
    second = dispatcher.tick()[0]

    assert first.id != second.id
    assert second.attempt == 2
    assert uuid.UUID(hex=first.id)


def test_codex_agent_selects_the_codex_runner(settings: Settings, tmp_path: Path) -> None:
    from flywheel.agents.claude_code import ClaudeCodeRunner
    from flywheel.agents.codex import CodexRunner

    task = _task()
    task.agent = AgentKind.CODEX
    ledger = Ledger(tmp_path / "ledger.db")
    board = FakeBoard([task])
    dispatcher = Dispatcher(
        settings=settings,
        ledger=ledger,
        board=board,  # type: ignore[arg-type]
        issues=FakeIssues(),  # type: ignore[arg-type]
        pulls=FakePulls(),  # type: ignore[arg-type]
        projects=FakeProjects(),  # type: ignore[arg-type]
        workspaces=FakeWorkspaceFactory(tmp_path / "workspaces"),  # type: ignore[arg-type]
        clarification=ClarificationService(FakeIssues(), board, 48),  # type: ignore[arg-type]
        scaffold=ScaffoldService(tmp_path / "templates"),
    )

    from flywheel.domain.run import Run

    run = Run(
        id="r1",
        project_item_id=task.project_item_id,
        issue_number=task.issue_number,
        repository=task.repository.full_name,
        agent=AgentKind.CODEX,
    )
    from flywheel.domain.template import TemplateManifest

    workspace = FakeWorkspace(tmp_path / "cw")
    spec = dispatcher._spec(
        run,
        task,
        ProjectConfig(),
        TemplateManifest(),
        workspace,
        "p",
        "s",  # type: ignore[arg-type]
    )
    runner = dispatcher._runner_for(run, task, ProjectConfig(), workspace, spec)  # type: ignore[arg-type]

    assert isinstance(runner, CodexRunner)
    assert not isinstance(runner, ClaudeCodeRunner)
