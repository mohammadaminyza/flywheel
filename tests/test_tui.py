from pathlib import Path

import pytest

from flywheel.config import Settings
from flywheel.tui.app import FlywheelApp
from flywheel.tui.wizard import STEPS, WizardScreen


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("FLYWHEEL_HOME", str(tmp_path))
    created = Settings(workspace_dir=str(tmp_path / "workspaces"))
    return created


async def test_app_opens_wizard_on_first_run(settings: Settings, tmp_path: Path) -> None:
    app = FlywheelApp(settings=settings)
    app.ledger._connection.close()
    from flywheel.storage import Ledger

    app.ledger = Ledger(tmp_path / "flywheel.db")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, WizardScreen)


async def test_wizard_walks_every_step_without_error(settings: Settings, tmp_path: Path) -> None:
    from flywheel.storage import Ledger

    app = FlywheelApp(settings=settings)
    app.ledger._connection.close()
    app.ledger = Ledger(tmp_path / "flywheel.db")
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, WizardScreen)
        for index in range(len(STEPS)):
            screen.index = index
            screen.render_step()
            await pilot.pause()


async def test_dashboard_opens_when_setup_completed(settings: Settings, tmp_path: Path) -> None:
    from flywheel.storage import Ledger
    from flywheel.tui.dashboard import DashboardScreen

    settings.setup_completed = True
    app = FlywheelApp(settings=settings)
    app.ledger._connection.close()
    app.ledger = Ledger(tmp_path / "flywheel.db")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


async def test_next_through_every_step_never_crashes(settings: Settings, tmp_path: Path) -> None:
    from flywheel.storage import Ledger

    app = FlywheelApp(settings=settings)
    app.ledger._connection.close()
    app.ledger = Ledger(tmp_path / "flywheel.db")
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, WizardScreen)
        for _ in range(len(STEPS)):
            screen.go_next()
            await pilot.pause()
        assert settings.setup_completed


async def test_board_step_with_empty_select_does_not_crash(
    settings: Settings, tmp_path: Path
) -> None:
    from flywheel.storage import Ledger

    app = FlywheelApp(settings=settings)
    app.ledger._connection.close()
    app.ledger = Ledger(tmp_path / "flywheel.db")
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, WizardScreen)
        screen.index = next(i for i, step in enumerate(STEPS) if step.key == "board")
        screen.render_step()
        await pilot.pause()
        screen._collect()
        assert settings.github.project_number == 0


async def test_first_text_field_is_focused_on_a_step(settings: Settings, tmp_path: Path) -> None:
    from textual.widgets import Input

    from flywheel.storage import Ledger

    app = FlywheelApp(settings=settings)
    app.ledger._connection.close()
    app.ledger = Ledger(tmp_path / "flywheel.db")
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, WizardScreen)
        screen.index = next(i for i, step in enumerate(STEPS) if step.key == "github")
        screen.render_step()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.focused, Input)
        assert app.focused.id == "github-token"
