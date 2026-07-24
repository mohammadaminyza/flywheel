# ruff: noqa: SIM105

from flywheel.delivery.telegram import TelegramNotifier
from flywheel.domain.result import AgentResult
from flywheel.domain.run import Run
from flywheel.domain.task import Task
from flywheel.github.issues import IssueService


class TaskReporter:
    """Publish durable, human-readable task lifecycle updates."""

    def __init__(self, issues: IssueService, telegram: TelegramNotifier) -> None:
        self._issues = issues
        self._telegram = telegram

    def _comment(self, task: Task, body: str) -> None:
        try:
            self._issues.comment(task.repository, task.issue_number, body)
        except Exception:  # noqa: BLE001
            pass

    def _telegram_update(self, task: Task, heading: str, detail: str) -> None:
        try:
            self._telegram.update(
                repository=task.repository.full_name,
                issue_number=task.issue_number,
                heading=heading,
                detail=detail.replace("**", "*"),
                issue_url=task.url,
            )
        except Exception:  # noqa: BLE001
            pass

    def started(self, task: Task, run: Run, max_attempts: int) -> None:
        detail = (
            f"Attempt **{run.attempt} of {max_attempts}** started with "
            f"`{task.agent.value}`. The workspace is being prepared."
        )
        self._comment(task, f"### Flywheel started\n\n{detail}")
        self._telegram_update(task, "Flywheel started", detail)

    def resumed(self, task: Task, run: Run) -> None:
        detail = (
            f"Your answer was received. Attempt **{run.attempt}** is resuming the same "
            f"`{task.agent.value}` session."
        )
        self._comment(task, f"### Flywheel resumed\n\n{detail}")
        self._telegram_update(task, "Flywheel resumed", detail)

    def progress(self, task: Task, heading: str, detail: str) -> None:
        self._comment(task, f"### {heading}\n\n{detail}")
        self._telegram_update(task, heading, detail)

    def question(self, task: Task, result: AgentResult) -> None:
        try:
            self._telegram.question(
                task.repository.full_name,
                task.issue_number,
                result.questions,
                task.url,
            )
        except Exception:  # noqa: BLE001
            pass

    def succeeded(self, task: Task, run: Run, result: AgentResult, pull_url: str) -> None:
        summary = result.summary.strip() or "Implementation and self-review completed."
        detail = (
            f"Pull request [#{run.pull_request_number}]({pull_url}) was opened after "
            f"attempt **{run.attempt}**.\n\n**Agent summary:** {summary[:1200]}"
        )
        self._comment(task, f"### Pull request opened\n\n{detail}")
        try:
            self._telegram.pull_request_ready(
                repository=task.repository.full_name,
                issue_number=task.issue_number,
                title=task.title,
                pull_request_url=pull_url,
                preview_url=None,
                screenshots=[],
                summary=summary,
            )
        except Exception:  # noqa: BLE001
            pass

    def failed(
        self,
        task: Task,
        run: Run,
        detail: str,
        max_attempts: int,
        will_retry: bool,
    ) -> None:
        safe_detail = detail.replace("```", "'''")[:1500]
        if will_retry:
            next_step = (
                "The card was returned to **Todo** and will be retried automatically "
                "on the next poll."
            )
        else:
            next_step = (
                "The factory could not complete this task. No automatic attempts remain. "
                "The card was moved to **Blocked**; "
                "move it back to **Todo** to explicitly retry."
            )
        body = (
            "### Flywheel attempt failed\n\n"
            f"Attempt **{run.attempt} of {max_attempts}** failed.\n\n"
            f"```text\n{safe_detail}\n```\n\n{next_step}"
        )
        self._comment(task, body)
        try:
            self._telegram.failed(
                task.repository.full_name,
                task.issue_number,
                f"Attempt {run.attempt}/{max_attempts}: {detail}",
                task.url,
            )
        except Exception:  # noqa: BLE001
            pass
