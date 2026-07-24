from typing import Any

from flywheel.domain.enums import AgentKind, TaskStatus
from flywheel.domain.result import AgentQuestion, AgentResult
from flywheel.domain.run import Run
from flywheel.domain.task import Repository, Task
from flywheel.services.task_reporter import TaskReporter


class FakeIssues:
    def __init__(self, raises: bool = False) -> None:
        self.raises = raises
        self.comments: list[str] = []

    def comment(self, repository: Repository, issue_number: int, body: str) -> str:
        if self.raises:
            raise RuntimeError("GitHub unavailable")
        self.comments.append(body)
        return "comment-1"


class FakeTelegram:
    def __init__(self, raises: bool = False) -> None:
        self.raises = raises
        self.calls: list[str] = []

    def _record(self, name: str) -> bool:
        if self.raises:
            raise RuntimeError("Telegram unavailable")
        self.calls.append(name)
        return True

    def update(self, **kwargs: Any) -> bool:
        return self._record("update")

    def question(self, *args: Any, **kwargs: Any) -> bool:
        return self._record("question")

    def pull_request_ready(self, **kwargs: Any) -> bool:
        return self._record("ready")

    def failed(self, *args: Any, **kwargs: Any) -> bool:
        return self._record("failed")


def task() -> Task:
    return Task(
        project_item_id="ITEM",
        issue_number=7,
        issue_node_id="NODE",
        title="Build the page",
        body="Acceptance criteria",
        url="https://github.com/acme/app/issues/7",
        repository=Repository(owner="acme", name="app"),
        agent=AgentKind.CODEX,
        status=TaskStatus.TODO,
    )


def run() -> Run:
    return Run(
        id="run-1",
        project_item_id="ITEM",
        issue_number=7,
        repository="acme/app",
        agent=AgentKind.CODEX,
        attempt=2,
        pull_request_number=42,
    )


def test_reports_lifecycle_to_issue_and_telegram() -> None:
    issues = FakeIssues()
    telegram = FakeTelegram()
    reporter = TaskReporter(issues, telegram)  # type: ignore[arg-type]
    current_task = task()
    current_run = run()
    result = AgentResult(
        status="completed",
        summary="Implemented the page.",
        questions=[AgentQuestion(question="Which theme?")],
    )

    reporter.started(current_task, current_run, 3)
    reporter.progress(current_task, "Self-review started", "Reviewing the diff.")
    reporter.question(current_task, result)
    reporter.succeeded(current_task, current_run, result, "https://github.com/acme/app/pull/42")
    reporter.failed(current_task, current_run, "tests failed", 3, will_retry=True)

    assert any("Flywheel started" in body for body in issues.comments)
    assert any("Self-review started" in body for body in issues.comments)
    assert any("Pull request opened" in body for body in issues.comments)
    assert any("tests failed" in body for body in issues.comments)
    assert telegram.calls == ["update", "update", "question", "ready", "failed"]


def test_reporting_failures_never_abort_the_task() -> None:
    reporter = TaskReporter(  # type: ignore[arg-type]
        FakeIssues(raises=True),
        FakeTelegram(raises=True),
    )

    reporter.started(task(), run(), 3)
    reporter.failed(task(), run(), "network failure", 3, will_retry=True)
