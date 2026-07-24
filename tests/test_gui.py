from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from flywheel.domain.enums import AgentKind, RunStatus  # noqa: E402
from flywheel.domain.run import Run  # noqa: E402
from flywheel.gui.server import create_gui_app  # noqa: E402
from flywheel.storage import Ledger  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "Iterator[TestClient]":
    monkeypatch.setenv("FLYWHEEL_HOME", str(tmp_path))
    with TestClient(create_gui_app()) as test_client:
        yield test_client


def test_serves_the_spa(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Flywheel" in response.text
    assert "<title>" in response.text


def test_doctor_endpoint_returns_checks(client: TestClient) -> None:
    checks = client.get("/api/doctor").json()
    names = {c["name"] for c in checks}
    assert "Docker" in names
    assert "GitHub token" in names


def test_config_round_trip_and_token_is_never_returned(client: TestClient) -> None:
    client.post("/api/config", json={"token": "secret-token", "owner": "acme", "max_parallel": 4})

    config = client.get("/api/config").json()

    assert config["owner"] == "acme"
    assert config["max_parallel"] == 4
    assert config["token_set"] is True
    assert "secret-token" not in str(config)
    assert "token" not in config


def test_setup_marked_complete_once_board_configured(client: TestClient) -> None:
    client.post(
        "/api/config",
        json={"token": "t", "owner": "acme", "project_number": 5, "project_title": "Board"},
    )

    assert client.get("/api/config").json()["setup_completed"] is True


def test_loop_start_refused_until_ready(client: TestClient) -> None:
    response = client.post("/api/loop/start")

    assert response.status_code == 400
    assert "not ready" in response.json()["detail"].lower()


def test_loop_status_reports_blocking_checks(client: TestClient) -> None:
    status = client.get("/api/loop").json()

    assert status["running"] is False
    assert "GitHub token" in status["blocking"]


def test_runs_endpoint_groups_by_status(client: TestClient, tmp_path: Path) -> None:
    from flywheel.config import load_settings

    ledger = Ledger(load_settings().database_path)
    ledger.save(
        Run(
            id="r1",
            project_item_id="I",
            issue_number=7,
            repository="acme/api",
            agent=AgentKind.CLAUDE_CODE,
            status=RunStatus.SUCCEEDED,
            pull_request_number=12,
        )
    )
    ledger.close()

    data = client.get("/api/runs").json()

    assert len(data["runs"]["succeeded"]) == 1
    assert data["runs"]["succeeded"][0]["pull_request_number"] == 12


def test_questions_endpoint_includes_asked_questions(client: TestClient) -> None:
    from flywheel.config import load_settings

    ledger = Ledger(load_settings().database_path)
    ledger.save(
        Run(
            id="q1",
            project_item_id="I",
            issue_number=9,
            repository="acme/api",
            agent=AgentKind.CLAUDE_CODE,
            status=RunStatus.NEEDS_INPUT,
        )
    )
    ledger.append_event("q1", "needs_input", {"questions": ["Which database?"]})
    ledger.close()

    questions = client.get("/api/questions").json()

    assert questions[0]["issue_number"] == 9
    assert questions[0]["questions"] == ["Which database?"]


def test_answer_to_unknown_run_is_404(client: TestClient) -> None:
    response = client.post("/api/questions/missing/answer", json={"answer": "hi"})

    assert response.status_code == 404


def test_config_persists_ssh_password_and_telegram_username(client: TestClient) -> None:
    client.post(
        "/api/config",
        json={
            "deploy_auth_method": "password",
            "deploy_ssh_password": "s3cret",
            "deploy_host": "h",
            "deploy_domain": "d",
            "telegram_username": "@mo",
        },
    )

    config = client.get("/api/config").json()

    assert config["deploy_auth_method"] == "password"
    assert config["deploy_ssh_password_set"] is True
    assert "s3cret" not in str(config)
    assert config["telegram_username"] == "@mo"


def test_telegram_username_counts_as_configured() -> None:
    from flywheel.config import TelegramSettings

    telegram = TelegramSettings(bot_token="t", username="mo")

    assert telegram.configured is True
    assert telegram.target == "@mo"


def test_deploy_password_auth_reports_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    from flywheel import probes

    class FakeClient:
        def set_missing_host_key_policy(self, policy: object) -> None: ...
        def connect(self, **kwargs: object) -> None:
            raise OSError("connection refused")

        def close(self) -> None: ...

    fake = types.SimpleNamespace(SSHClient=lambda: FakeClient(), AutoAddPolicy=lambda: object())
    monkeypatch.setattr(probes, "paramiko", fake)

    result = probes.probe_deploy_host("10.0.0.9", "user", password="pw", auth_method="password")

    assert not result.ok
    assert result.optional
    assert "connection refused" in result.detail


def test_deploy_test_persists_what_you_typed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flywheel import probes
    from flywheel.probes import ProbeResult

    monkeypatch.setattr(
        probes,
        "probe_deploy_host",
        lambda *a, **k: ProbeResult(
            name="Deploy host", ok=False, detail="unreachable", optional=True
        ),
    )

    client.post(
        "/api/deploy/test",
        json={
            "deploy_host": "192.168.90.5",
            "deploy_user": "ad\\administrator",
            "deploy_auth_method": "password",
            "deploy_ssh_password": "pw",
            "deploy_domain": "ex.com",
        },
    )
    config = client.get("/api/config").json()

    assert config["deploy_host"] == "192.168.90.5"
    assert config["deploy_user"] == "ad\\administrator"
    assert config["deploy_auth_method"] == "password"
    assert config["deploy_ssh_password_set"] is True
    assert config["deploy_domain"] == "ex.com"
    assert "pw" not in str(config)


def test_telegram_test_persists_and_returns_chat_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flywheel import probes
    from flywheel.delivery import telegram

    monkeypatch.setattr(probes, "telegram_bot_username", lambda t: "my_bot")
    monkeypatch.setattr(probes, "resolve_telegram_chat_id", lambda t, u: "5894797066")
    monkeypatch.setattr(telegram.TelegramNotifier, "send", lambda self, text: True)

    response = client.post(
        "/api/telegram/test",
        json={"telegram_bot_token": "tok", "telegram_username": "@MAMIN_YZ"},
    ).json()

    assert response["ok"] is True
    assert response["chat_id"] == "5894797066"
    config = client.get("/api/config").json()
    assert config["telegram_username"] == "@MAMIN_YZ"
    assert config["telegram_chat_id"] == "5894797066"
    assert config["telegram_bot_set"] is True


def test_parses_verbose_docker_version_from_a_windows_host() -> None:
    from flywheel.probes import _parse_server_version

    verbose = (
        "Client:\r\n Version: 27.1.0\r\n API version: 1.46\r\n"
        "Server: Docker Engine - Community\r\n Engine:\r\n  Version: 26.1.4\r\n"
        "  API version: 1.45 (minimum version 1.24)\r\n"
    )

    assert _parse_server_version(verbose) == "26.1.4"
    assert _parse_server_version("27.1.0\r\n") == "27.1.0"
    assert _parse_server_version("'docker' is not recognized") is None


def test_deploy_password_auth_succeeds_via_paramiko(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    from flywheel import probes

    class FakeStd:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

    class FakeClient:
        def set_missing_host_key_policy(self, policy: object) -> None: ...
        def connect(self, **kwargs: object) -> None: ...
        def exec_command(self, cmd: str, timeout: int = 15) -> tuple[object, FakeStd, FakeStd]:
            return object(), FakeStd(b"27.1.0\n"), FakeStd(b"")

        def close(self) -> None: ...

    fake = types.SimpleNamespace(SSHClient=lambda: FakeClient(), AutoAddPolicy=lambda: object())
    monkeypatch.setattr(probes, "paramiko", fake)

    result = probes.probe_deploy_host(
        "192.168.90.5", "ad\\administrator", password="pw", auth_method="password"
    )

    assert result.ok
    assert "27.1.0" in result.detail
