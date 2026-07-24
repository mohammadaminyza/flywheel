from pathlib import Path

from flywheel.delivery.telegram import TelegramNotifier
from flywheel.domain.enums import RunStatus, TaskStatus
from flywheel.domain.run import Run
from flywheel.domain.task import Task
from flywheel.github.actions import ActionsService, WorkflowRun
from flywheel.github.board import BoardService
from flywheel.github.pulls import PullRequestService


class DeliveryService:
    def __init__(
        self,
        actions: ActionsService,
        pulls: PullRequestService,
        board: BoardService,
        telegram: TelegramNotifier,
        artifacts_dir: Path,
    ) -> None:
        self._actions = actions
        self._pulls = pulls
        self._board = board
        self._telegram = telegram
        self._artifacts_dir = artifacts_dir

    def poll(self, run: Run, task: Task, head_sha: str, summary: str) -> bool:
        if run.status != RunStatus.SUCCEEDED or run.pull_request_number is None:
            return False

        workflow_runs = self._actions.runs_for_sha(task.repository, head_sha)
        if not self._actions.all_finished(workflow_runs):
            return False

        failures = self._actions.failures(workflow_runs)
        if failures:
            self._report_ci_failure(run, task, failures)
            return True

        preview_url = self._actions.preview_url_from_deployments(task.repository, run.branch or "")
        run.preview_url = preview_url
        screenshots = self._collect_screenshots(task, workflow_runs, run)
        self._report_success(run, task, preview_url, screenshots, summary)
        return True

    def _collect_screenshots(
        self, task: Task, workflow_runs: list[WorkflowRun], run: Run
    ) -> list[Path]:
        destination = self._artifacts_dir / run.id
        collected: list[Path] = []
        for workflow_run in workflow_runs:
            collected += self._actions.download_screenshots(
                task.repository, workflow_run.id, destination
            )
        return collected

    def _report_success(
        self,
        run: Run,
        task: Task,
        preview_url: str | None,
        screenshots: list[Path],
        summary: str,
    ) -> None:
        assert run.pull_request_number is not None
        lines = ["All checks passed."]
        if preview_url:
            lines.append(f"\nPreview environment: {preview_url}")
        if screenshots:
            lines.append(f"\n{len(screenshots)} screenshot(s) attached to the workflow run.")
        self._pulls.comment(task.repository, run.pull_request_number, "\n".join(lines))

        pull_url = f"https://github.com/{task.repository.full_name}/pull/{run.pull_request_number}"
        self._telegram.pull_request_ready(
            repository=task.repository.full_name,
            issue_number=task.issue_number,
            title=task.title,
            pull_request_url=pull_url,
            preview_url=preview_url,
            screenshots=screenshots,
            summary=summary,
        )

    def _report_ci_failure(self, run: Run, task: Task, failures: list[WorkflowRun]) -> None:
        assert run.pull_request_number is not None
        detail = "\n".join(f"- {failure.name}: {failure.html_url}" for failure in failures)
        self._pulls.comment(
            task.repository,
            run.pull_request_number,
            f"CI failed, so this is not ready yet.\n\n{detail}",
        )
        self._board.set_status(task, TaskStatus.TODO)
        self._telegram.failed(
            task.repository.full_name,
            task.issue_number,
            f"CI failed:\n{detail}",
            task.url,
        )
