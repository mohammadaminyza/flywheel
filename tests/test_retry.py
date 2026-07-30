import subprocess
from pathlib import Path

import pytest

from flywheel.config import Settings
from flywheel.domain.enums import AgentKind, RunStatus, TaskStatus
from flywheel.domain.run import Run, RunOutcome
from flywheel.services.dispatcher import is_infrastructure_failure
from flywheel.storage import Ledger
from flywheel.workspace import WorkspaceFactory, remove_tree
from tests.test_dispatcher import _build, _task


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    created = Settings(workspace_dir=str(tmp_path / "ws"))
    created.github.token = "tok"
    created.github.owner = "acme"
    created.github.project_number = 1
    created.planning.auto_refine = False
    return created


def test_environment_failures_are_told_apart_from_agent_failures() -> None:
    assert is_infrastructure_failure(
        "WorkspaceException: workspace error: could not clone acme/api: fatal: destination path"
    )
    assert is_infrastructure_failure("interrupted by factory restart")
    assert is_infrastructure_failure("HTTPError: 503 Service Unavailable")
    assert not is_infrastructure_failure("the agent committed nothing")
    assert not is_infrastructure_failure("self-review rejected the implementation")


def test_a_clone_failure_does_not_spend_an_attempt(settings: Settings, tmp_path: Path) -> None:
    dispatcher, board, _, _, ledger, _, _ = _build(
        settings,
        tmp_path,
        [_task()],
        [RunOutcome(exit_code=1, error="WorkspaceException: could not clone acme/api")],
    )

    runs = dispatcher.tick()

    assert runs[0].status == RunStatus.FAILED
    assert runs[0].counts_toward_attempts is False
    assert ledger.attempts_for("ITEM_1") == 0
    # The card goes back to Todo, not Blocked, so the next cycle picks it up again.
    assert board.statuses[-1] == (7, TaskStatus.TODO)


def test_an_agent_failure_still_spends_an_attempt(settings: Settings, tmp_path: Path) -> None:
    dispatcher, _, _, _, ledger, _, _ = _build(
        settings,
        tmp_path,
        [_task()],
        [RunOutcome(exit_code=1, error="the agent committed nothing")],
    )

    dispatcher.tick()

    assert ledger.attempts_for("ITEM_1") == 1


def test_endless_environment_failures_eventually_block_the_card(
    settings: Settings, tmp_path: Path
) -> None:
    settings.loop.max_infrastructure_retries = 2
    task = _task()
    dispatcher, board, _, _, ledger, _, _ = _build(
        settings,
        tmp_path,
        [task],
        [RunOutcome(exit_code=1, error="WorkspaceException: could not clone acme/api")],
    )

    # Two free environment retries, then its three real attempts, then it stops.
    for _ in range(2 + settings.loop.max_attempts):
        task.status = TaskStatus.TODO
        dispatcher.tick()

    assert ledger.attempts_for("ITEM_1") == settings.loop.max_attempts
    assert board.statuses[-1] == (7, TaskStatus.BLOCKED)


def test_retry_gives_a_blocked_card_its_attempts_back(
    settings: Settings, tmp_path: Path
) -> None:
    task = _task(TaskStatus.BLOCKED)
    dispatcher, board, _, _, ledger, _, _ = _build(
        settings, tmp_path, [task], [RunOutcome(exit_code=1, error="the agent committed nothing")]
    )
    for _ in range(settings.loop.max_attempts):
        task.status = TaskStatus.TODO
        dispatcher.tick()
    assert ledger.attempts_for("ITEM_1") == settings.loop.max_attempts
    assert dispatcher.tick() == []  # exhausted: nothing starts

    dispatcher.retry(task)

    assert ledger.attempts_for("ITEM_1") == 0
    assert board.statuses[-1] == (7, TaskStatus.TODO)
    assert ledger.is_claimed("ITEM_1") is False
    # The history is kept, not deleted.
    assert len(ledger.recent_for_item("ITEM_1")) == settings.loop.max_attempts


def test_a_restart_does_not_spend_an_attempt(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.db")
    ledger.save(
        Run(
            id="r1",
            project_item_id="ITEM_1",
            issue_number=7,
            repository="acme/api",
            agent=AgentKind.CLAUDE_CODE,
            status=RunStatus.RUNNING,
        )
    )

    recovered = ledger.recover_stale()

    assert recovered == 1
    assert ledger.attempts_for("ITEM_1") == 0
    assert ledger.get("r1").error == "interrupted by factory restart"  # type: ignore[union-attr]
    ledger.close()


def test_a_ledger_written_before_this_column_existed_still_opens(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE runs (id TEXT PRIMARY KEY, project_item_id TEXT NOT NULL, "
        "issue_number INTEGER NOT NULL, repository TEXT NOT NULL, agent TEXT NOT NULL, "
        "status TEXT NOT NULL, phase TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1, "
        "branch TEXT, session_id TEXT, pull_request_number INTEGER, preview_url TEXT, "
        "cost_usd REAL NOT NULL DEFAULT 0, error TEXT, pending_question_comment_id TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO runs (id, project_item_id, issue_number, repository, agent, status, "
        "phase, attempt, cost_usd, created_at, updated_at) VALUES "
        "('r1', 'ITEM_1', 7, 'acme/api', 'codex', 'failed', 'finished', 2, 0, "
        "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    legacy.commit()
    legacy.close()

    ledger = Ledger(path)

    assert ledger.attempts_for("ITEM_1") == 2
    assert ledger.reset_attempts("ITEM_1") == 1
    assert ledger.attempts_for("ITEM_1") == 0
    ledger.close()


def test_a_workspace_git_left_read_only_is_still_removed(tmp_path: Path) -> None:
    workspace = tmp_path / "clone"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "file.txt").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-m", "x"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )

    assert remove_tree(workspace) is True
    assert not workspace.exists()


def test_a_leftover_directory_never_blocks_the_next_run(tmp_path: Path) -> None:
    factory = WorkspaceFactory(tmp_path / "workspaces", token="tok")
    leftover = tmp_path / "workspaces" / "run-1"
    leftover.mkdir(parents=True)
    (leftover / "stale.txt").write_text("left behind", encoding="utf-8")

    path = factory._clean_directory("run-1")

    assert path == leftover
    assert list(path.iterdir()) == []
