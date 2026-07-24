from pathlib import Path

import pytest
from typer.testing import CliRunner

from flywheel.cli.app import app
from flywheel.config import load_settings

runner = CliRunner()


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FLYWHEEL_HOME", str(tmp_path))
    return tmp_path


def test_login_rejects_a_bad_token(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from flywheel import probes
    from flywheel.probes import ProbeResult

    monkeypatch.setattr(
        probes,
        "probe_github_token",
        lambda t: ProbeResult(name="GitHub token", ok=False, detail="rejected", fix="regen"),
    )

    result = runner.invoke(app, ["login", "--token", "bad"])

    assert result.exit_code == 1
    assert "rejected" in result.output
    assert not load_settings().github.token


def test_login_saves_a_good_token_and_detects_owner(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flywheel import probes
    from flywheel.probes import ProbeResult

    good = ProbeResult(name="GitHub token", ok=True, detail="authenticated as octo")
    monkeypatch.setattr(probes, "probe_github_token", lambda t: good)
    monkeypatch.setattr(probes, "detect_github_login", lambda t: "octo")

    result = runner.invoke(app, ["login", "--token", "ghp_realish"])

    assert result.exit_code == 0
    settings = load_settings()
    assert settings.github.token == "ghp_realish"
    assert settings.github.owner == "octo"


def test_board_requires_a_token_first(home: Path) -> None:
    result = runner.invoke(app, ["board"])

    assert result.exit_code == 1
    assert "flywheel login" in result.output


def test_board_reports_when_none_found(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from flywheel import probes
    from flywheel.config import save_settings

    settings = load_settings()
    settings.github.token = "t"
    settings.github.owner = "acme"
    save_settings(settings)
    monkeypatch.setattr(probes, "list_boards", lambda token, owner: [])

    result = runner.invoke(app, ["board"])

    assert result.exit_code == 1
    assert "No open boards" in result.output


def test_board_sets_selection_by_number(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from flywheel import probes
    from flywheel.config import save_settings

    settings = load_settings()
    settings.github.token = "t"
    settings.github.owner = "acme"
    save_settings(settings)
    monkeypatch.setattr(
        probes,
        "list_boards",
        lambda token, owner: [{"number": 3, "title": "Roadmap"}, {"number": 7, "title": "Bugs"}],
    )

    result = runner.invoke(app, ["board", "--number", "7"])

    assert result.exit_code == 0
    settings = load_settings()
    assert settings.github.project_number == 7
    assert settings.github.project_title == "Bugs"
    assert settings.setup_completed is True
