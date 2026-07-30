from pathlib import Path

from flywheel.domain.enums import AgentKind, RunStatus, TaskStatus
from flywheel.domain.run import Run
from flywheel.domain.task import Repository, Task
from flywheel.github.actions import WorkflowRun
from flywheel.services.delivery_service import DeliveryService
from flywheel.storage import Ledger


def task() -> Task:
    return Task(
        project_item_id="item-1",
        issue_number=7,
        issue_node_id="node-7",
        title="Landing page",
        url="https://github.com/acme/shop/issues/7",
        repository=Repository(owner="acme", name="shop"),
        agent=AgentKind.CODEX,
        status=TaskStatus.IN_REVIEW,
    )


class FakeActions:
    def __init__(self, screenshot: Path) -> None:
        self.screenshot = screenshot
        self.runs = [
            WorkflowRun(
                id=1,
                name="CI",
                status="completed",
                conclusion="success",
                html_url="https://github.test/actions/1",
                head_sha="sha-1",
            ),
            WorkflowRun(
                id=2,
                name="Preview environment",
                status="completed",
                conclusion="success",
                html_url="https://github.test/actions/2",
                head_sha="sha-1",
            ),
        ]

    def runs_for_sha(self, repository: Repository, head_sha: str) -> list[WorkflowRun]:
        return self.runs

    def all_finished(self, runs: list[WorkflowRun]) -> bool:
        return True

    def failures(self, runs: list[WorkflowRun]) -> list[WorkflowRun]:
        return [run for run in runs if not run.succeeded]

    def preview_url_from_deployments(self, repository: Repository, branch: str) -> str:
        return "https://preview.example.test"

    def download_screenshots(
        self, repository: Repository, run_id: int, destination: Path
    ) -> list[Path]:
        return [self.screenshot] if run_id == 2 else []


class FakePulls:
    def __init__(self) -> None:
        self.comments: list[str] = []

    def get(self, repository: Repository, number: int) -> dict[str, object]:
        return {"state": "open", "head": {"sha": "sha-1"}}

    def comment(self, repository: Repository, number: int, body: str) -> None:
        self.comments.append(body)


class FakeBoard:
    def tasks(self) -> list[Task]:
        return [task()]


class FakeTelegram:
    def __init__(self) -> None:
        self.ready = 0

    def pull_request_ready(self, **kwargs: object) -> bool:
        self.ready += 1
        assert kwargs["screenshots"]
        return True

    def update(self, *args: object, **kwargs: object) -> bool:
        return True

    def failed(self, *args: object, **kwargs: object) -> bool:
        return True


def test_delivery_reports_ci_and_screenshots_once(tmp_path: Path) -> None:
    screenshot = tmp_path / "home.png"
    screenshot.write_bytes(b"png")
    ledger = Ledger(tmp_path / "ledger.db")
    run = Run(
        id="run-1",
        project_item_id="item-1",
        issue_number=7,
        repository="acme/shop",
        agent=AgentKind.CODEX,
        status=RunStatus.SUCCEEDED,
        branch="feat/landing",
        pull_request_number=4,
    )
    ledger.save(run)
    pulls = FakePulls()
    telegram = FakeTelegram()
    service = DeliveryService(
        FakeActions(screenshot),  # type: ignore[arg-type]
        pulls,  # type: ignore[arg-type]
        FakeBoard(),  # type: ignore[arg-type]
        telegram,  # type: ignore[arg-type]
        ledger,
        tmp_path / "artifacts",
    )

    assert len(service.poll_all()) == 1
    assert service.poll_all() == []
    assert telegram.ready == 1
    assert len(pulls.comments) == 1
    assert "Code checks passed" in pulls.comments[0]
    assert "screenshot" in pulls.comments[0]
    ledger.close()
