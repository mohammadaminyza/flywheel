from pathlib import Path

import pytest

from flywheel.config import Settings
from flywheel.domain.enums import AgentKind, RunStatus
from flywheel.domain.run import Run
from flywheel.storage import Ledger
from flywheel.tui.app import FlywheelApp
from flywheel.tui.dashboard import DashboardScreen
from flywheel.tui.questions import QuestionsScreen


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("FLYWHEEL_HOME", str(tmp_path))
    created = Settings(workspace_dir=str(tmp_path / "ws"), setup_completed=True)
    created.github.token = "tok"
    created.github.owner = "acme"
    created.github.project_number = 1
    created.github.project_title = "Board"
    return created


def _app_with_ledger(settings: Settings, tmp_path: Path) -> FlywheelApp:
    app = FlywheelApp(settings=settings)
    app.ledger._connection.close()
    app.ledger = Ledger(tmp_path / "flywheel.db")
    return app


def _parked_run() -> Run:
    return Run(
        id="run-1",
        project_item_id="ITEM",
        issue_number=7,
        repository="acme/api",
        agent=AgentKind.CLAUDE_CODE,
        status=RunStatus.NEEDS_INPUT,
        pending_question_comment_id="q1",
    )


async def test_dashboard_shows_waiting_count(settings: Settings, tmp_path: Path) -> None:
    app = _app_with_ledger(settings, tmp_path)
    app.ledger.save(_parked_run())
    app.ledger.append_event("run-1", "needs_input", {"questions": ["Which database?"]})

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        app.screen.refresh_board()
        await pilot.pause()
        status = app.screen.query_one("#status-bar").render()
        assert "waiting: 1" in str(status)


async def test_questions_screen_lists_and_selects(settings: Settings, tmp_path: Path) -> None:
    app = _app_with_ledger(settings, tmp_path)
    app.ledger.save(_parked_run())
    app.ledger.append_event("run-1", "needs_input", {"questions": ["Which database?"]})

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = QuestionsScreen(settings, app.ledger)
        await app.push_screen(screen)
        await pilot.pause()
        screen._select("run-1")
        await pilot.pause()
        body = str(screen.query_one("#question-body").render())
        assert "Which database?" in body


def test_loop_controller_reports_config_error_without_board(tmp_path: Path) -> None:
    from flywheel.services.loop_controller import LoopController

    settings = Settings(workspace_dir=str(tmp_path / "ws"))
    events: list[tuple[str, dict]] = []
    controller = LoopController(
        settings, status_sink=lambda kind, payload: events.append((kind, payload))
    )

    controller.start()
    controller.stop()

    assert any(kind == "error" for kind, _ in events)


def test_status_api_serves_runs(settings: Settings, tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    ledger = Ledger(settings.database_path)
    ledger.save(_parked_run())
    ledger.close()

    from flywheel.api import create_app

    with TestClient(create_app()) as client:
        assert client.get("/health").json() == {"status": "ok"}
        runs = client.get("/runs").json()
        assert runs[0]["issue_number"] == 7
        assert client.get("/questions").json()[0]["id"] == "run-1"
        assert client.get("/runs/run-1").json()["repository"] == "acme/api"
        assert client.get("/runs/missing").status_code == 404
