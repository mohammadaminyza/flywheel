from pathlib import Path

from flywheel.delivery.telegram import TelegramNotifier
from flywheel.domain.run import Run
from flywheel.domain.task import Task
from flywheel.github.actions import ActionsService, WorkflowRun
from flywheel.github.board import BoardService
from flywheel.github.pulls import PullRequestService
from flywheel.storage import Ledger


class DeliveryService:
    """Monitor GitHub Actions and publish durable delivery evidence once per commit."""

    def __init__(
        self,
        actions: ActionsService,
        pulls: PullRequestService,
        board: BoardService,
        telegram: TelegramNotifier,
        ledger: Ledger,
        artifacts_dir: Path,
    ) -> None:
        self._actions = actions
        self._pulls = pulls
        self._board = board
        self._telegram = telegram
        self._ledger = ledger
        self._artifacts_dir = artifacts_dir

    def poll_all(self) -> list[Run]:
        tasks = {task.project_item_id: task for task in self._board.tasks()}
        latest: dict[str, Run] = {}
        for run in self._ledger.list_runs(300):
            latest.setdefault(run.project_item_id, run)

        reported: list[Run] = []
        for run in latest.values():
            if run.pull_request_number is None:
                continue
            task = tasks.get(run.project_item_id)
            if task is None:
                continue
            pull = self._pulls.get(task.repository, run.pull_request_number)
            if pull.get("state") != "open":
                continue
            head_sha = str(pull["head"]["sha"])
            if self._already_reported(run, head_sha):
                continue
            if self.poll(run, task, head_sha, self._summary(run)):
                reported.append(run)
        return reported

    def poll(self, run: Run, task: Task, head_sha: str, summary: str) -> bool:
        workflow_runs = self._actions.runs_for_sha(task.repository, head_sha)
        if not self._actions.all_finished(workflow_runs):
            return False

        failures = self._actions.failures(workflow_runs)
        ci_failures = [item for item in failures if item.name.strip().lower() == "ci"]
        preview_failures = [item for item in failures if item not in ci_failures]
        preview_url = self._actions.preview_url_from_deployments(
            task.repository, run.branch or ""
        )
        run.preview_url = preview_url
        self._ledger.save(run)
        screenshots = self._collect_screenshots(task, workflow_runs, run)

        if ci_failures:
            self._report_ci_failure(run, task, ci_failures, head_sha)
        else:
            self._report_result(
                run,
                task,
                workflow_runs,
                preview_failures,
                preview_url,
                screenshots,
                summary,
                head_sha,
            )
        return True

    def _already_reported(self, run: Run, head_sha: str) -> bool:
        return any(
            event["kind"] == "delivery_reported"
            and event["payload"].get("head_sha") == head_sha
            for event in self._ledger.events(run.id)
        )

    def _summary(self, run: Run) -> str:
        for event in reversed(self._ledger.events(run.id)):
            if event["kind"] == "agent_result":
                return str(event["payload"].get("summary") or "Implementation completed.")
        return "Implementation completed; GitHub Actions evidence is now available."

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

    def _report_result(
        self,
        run: Run,
        task: Task,
        workflow_runs: list[WorkflowRun],
        preview_failures: list[WorkflowRun],
        preview_url: str | None,
        screenshots: list[Path],
        summary: str,
        head_sha: str,
    ) -> None:
        assert run.pull_request_number is not None
        passed = [item for item in workflow_runs if item.succeeded]
        lines = ["### Flywheel delivery report", "", "**Code checks passed.**"]
        lines += [f"- ✅ [{item.name}]({item.html_url})" for item in passed]
        if preview_url:
            lines += ["", f"Preview: {preview_url}"]
        if screenshots:
            lines += ["", f"{len(screenshots)} screenshot(s) captured and sent to Telegram."]
        if preview_failures:
            lines += ["", "**Optional preview/deployment failures:**"]
            lines += [f"- ⚠️ [{item.name}]({item.html_url})" for item in preview_failures]
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
        if preview_failures:
            detail = "\n".join(f"- {item.name}: {item.html_url}" for item in preview_failures)
            self._telegram.update(
                task.repository.full_name,
                task.issue_number,
                "Preview or deployment needs attention",
                detail,
                pull_url,
            )
        self._ledger.append_event(
            run.id,
            "delivery_reported",
            {
                "head_sha": head_sha,
                "ci": "passed",
                "preview_failures": [item.name for item in preview_failures],
                "screenshots": len(screenshots),
                "preview_url": preview_url,
            },
        )

    def _report_ci_failure(
        self,
        run: Run,
        task: Task,
        failures: list[WorkflowRun],
        head_sha: str,
    ) -> None:
        assert run.pull_request_number is not None
        detail = "\n".join(f"- [{item.name}]({item.html_url})" for item in failures)
        self._pulls.comment(
            task.repository,
            run.pull_request_number,
            "### Flywheel delivery report\n\n**CI failed; this is not ready.**\n\n" + detail,
        )
        self._telegram.failed(
            task.repository.full_name,
            task.issue_number,
            "CI failed:\n" + detail,
            task.url,
        )
        self._ledger.append_event(
            run.id,
            "delivery_reported",
            {"head_sha": head_sha, "ci": "failed", "failures": [item.name for item in failures]},
        )
