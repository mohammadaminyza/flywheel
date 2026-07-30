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
    assert "Repository from this GitHub Project" in response.text
    assert "Backlog grooming" in response.text
    assert 'id="autoRefine"' in response.text


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


def test_mcp_connections_are_saved_and_secret_values_are_masked(
    client: TestClient, tmp_path: Path
) -> None:
    response = client.post(
        "/api/mcp",
        json={
            "servers": {
                "notion": {
                    "enabled": True,
                    "url": "https://mcp.example.test",
                    "env": {"Authorization": "Bearer secret"},
                    "required": False,
                }
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["servers"]["notion"]["env"]["Authorization"] == "<saved>"

    client.post(
        "/api/mcp",
        json={
            "servers": {
                "notion": {
                    "enabled": True,
                    "url": "https://mcp.example.test/v2",
                    "env": {"Authorization": "<saved>"},
                }
            }
        },
    )

    from flywheel.config import load_settings

    saved = load_settings().mcp_servers["notion"]
    assert saved.url == "https://mcp.example.test/v2"
    assert saved.env["Authorization"] == "Bearer secret"


def test_start_and_stop_persist_loop_preference(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flywheel import probes
    from flywheel.config import load_settings

    monkeypatch.setattr(probes, "run_all", lambda _settings: [])
    monkeypatch.setattr("flywheel.gui.server.LoopController.start", lambda self: None)
    monkeypatch.setattr("flywheel.gui.server.LoopController.stop", lambda self: None)

    assert client.post("/api/loop/start").status_code == 200
    assert load_settings().loop.auto_start is True
    assert client.post("/api/loop/stop").status_code == 200
    assert load_settings().loop.auto_start is False


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


def test_loop_status_carries_everything_the_panel_shows(client: TestClient) -> None:
    status = client.get("/api/loop").json()

    assert status["ticks"] == 0
    assert status["events"] == []
    assert status["busy"] is False
    assert status["stopping"] is False
    assert status["poll_interval_seconds"] == 60
    assert status["max_parallel"] >= 1
    assert status["auto_start"] is False


def test_poking_a_stopped_loop_is_refused(client: TestClient) -> None:
    response = client.post("/api/loop/poke")

    assert response.status_code == 400
    assert "not running" in response.json()["detail"]


def test_readiness_is_not_recomputed_on_every_poll(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flywheel import probes

    calls: list[int] = []

    def run_all(_settings: object) -> list[object]:
        calls.append(1)
        return []

    monkeypatch.setattr(probes, "run_all", run_all)
    client.get("/api/loop")
    client.get("/api/loop")
    client.get("/api/loop")

    assert len(calls) == 1
    client.get("/api/doctor", params={"refresh": "true"})
    assert len(calls) == 2


def test_loop_settings_are_saved_from_the_loop_panel(client: TestClient) -> None:
    client.post(
        "/api/config",
        json={"poll_interval_seconds": 15, "max_attempts": 5, "max_parallel": 3},
    )

    config = client.get("/api/config").json()

    assert config["poll_interval_seconds"] == 15
    assert config["max_attempts"] == 5
    assert config["max_parallel"] == 3


def test_templates_come_from_your_folder_and_the_bundled_one(
    client: TestClient, tmp_path: Path
) -> None:
    mine = tmp_path / "my-templates" / "acme-stack"
    (mine / "template").mkdir(parents=True)
    (mine / "template.yml").write_text("id: acme-stack\nname: Acme stack\n", encoding="utf-8")

    client.post("/api/config", json={"templates_dir": str(tmp_path / "my-templates")})
    config = client.get("/api/config").json()

    catalog = {entry["id"]: entry for entry in config["template_catalog"]}
    assert config["templates_dir"] == str(tmp_path / "my-templates")
    assert catalog["acme-stack"]["custom"] is True
    assert catalog["acme-stack"]["name"] == "Acme stack"
    assert catalog["python-fastapi-nextjs"]["custom"] is False
    assert config["bundled_templates_dir"].endswith("templates")


def test_saving_a_templates_folder_returns_what_it_holds(
    client: TestClient, tmp_path: Path
) -> None:
    single = tmp_path / "acme-stack"
    (single / "template").mkdir(parents=True)
    (single / "template.yml").write_text("id: acme-stack\nname: Acme\n", encoding="utf-8")

    # The dropdown is filled from the answer to the same call that saves the folder,
    # so the path and the list can never show different things.
    saved = client.post("/api/config", json={"templates_dir": str(single)}).json()

    ids = [entry["id"] for entry in saved["template_catalog"]]
    assert ids == ["acme-stack", "python-fastapi-nextjs"]
    assert saved["templates"] == ids
    chosen = next(entry for entry in saved["template_catalog"] if entry["id"] == "acme-stack")
    assert chosen["path"] == str(single)
    assert chosen["custom"] is True


def test_a_templates_folder_that_does_not_exist_is_refused(client: TestClient) -> None:
    response = client.post("/api/config", json={"templates_dir": "Z:/nope/nowhere"})

    assert response.status_code == 400
    assert "not a folder" in response.json()["detail"]


def test_planning_agents_round_trip(client: TestClient) -> None:
    client.post(
        "/api/config",
        json={"planning_agents": ["codex"], "planning_deep_research": False},
    )

    config = client.get("/api/config").json()

    assert config["planning_agents"] == ["codex"]
    assert config["planning_deep_research"] is False


def test_an_unknown_planning_agent_is_refused(client: TestClient) -> None:
    response = client.post("/api/config", json={"planning_agents": ["gpt-9"]})

    assert response.status_code == 400
    assert "unknown agent" in response.json()["detail"]


def test_project_context_lists_repositories_from_the_setup_credentials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flywheel.github import board as board_module
    from flywheel.gui import server as gui

    class EmptyBoard:
        def __init__(self, *args: object, **kwargs: object) -> None: ...
        def load(self) -> object:
            return type("Loaded", (), {"title": "Delivery board"})()

        def repositories(self) -> list[object]:
            return []

    monkeypatch.setattr(board_module, "BoardService", EmptyBoard)
    monkeypatch.setattr(gui, "_owner_repositories", lambda client, owner: ["acme/shop"])
    client.post(
        "/api/config",
        json={"token": "t", "owner": "acme", "project_number": 3, "project_title": "Delivery"},
    )

    context = client.get("/api/project/context").json()

    assert context["connected"] is True
    assert context["title"] == "Delivery board"
    assert context["repositories"] == ["acme/shop"]
    assert context["board_repositories"] == []


def test_project_context_says_what_is_missing_before_setup(client: TestClient) -> None:
    context = client.get("/api/project/context").json()

    assert context["connected"] is False
    assert "Setup" in context["error"]


def test_retrying_an_unknown_card_is_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flywheel import bootstrap

    class Board:
        def tasks(self) -> list[object]:
            return []

    class Container:
        board = Board()

        def close(self) -> None: ...

    monkeypatch.setattr(bootstrap, "build", lambda settings, on_event=None: Container())
    client.post("/api/config", json={"token": "t", "owner": "acme", "project_number": 2})

    response = client.post("/api/board/retry", json={"project_item_id": "gone"})

    assert response.status_code == 404


def test_retry_puts_a_blocked_card_back_in_todo(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flywheel import bootstrap
    from flywheel.domain.enums import TaskStatus
    from flywheel.domain.task import Repository, Task

    task = Task(
        project_item_id="I7",
        issue_number=7,
        issue_node_id="N7",
        title="blocked task",
        body="",
        url="https://github.com/acme/api/issues/7",
        repository=Repository(owner="acme", name="api"),
        agent=AgentKind.CLAUDE_CODE,
        status=TaskStatus.BLOCKED,
    )
    retried: list[Task] = []

    class Board:
        def tasks(self) -> list[Task]:
            return [task]

    class Dispatcher:
        def retry(self, item: Task) -> None:
            retried.append(item)

    class Container:
        board = Board()
        dispatcher = Dispatcher()

        def close(self) -> None: ...

    monkeypatch.setattr(bootstrap, "build", lambda settings, on_event=None: Container())
    client.post("/api/config", json={"token": "t", "owner": "acme", "project_number": 2})

    response = client.post("/api/board/retry", json={"project_item_id": "I7"})

    assert response.status_code == 200
    assert response.json() == {"issue_number": 7, "status": "Todo"}
    assert retried == [task]


def test_backlog_is_empty_before_setup(client: TestClient) -> None:
    backlog = client.get("/api/project/backlog").json()

    assert backlog["connected"] is False
    assert backlog["pending"] == []


def test_backlog_lists_the_cards_the_loop_would_groom(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flywheel import bootstrap
    from flywheel.domain.enums import TaskStatus
    from flywheel.domain.task import Repository, Task
    from flywheel.services.dispatcher import mark_refined

    def _task(number: int, body: str) -> Task:
        return Task(
            project_item_id=f"I{number}",
            issue_number=number,
            issue_node_id=f"N{number}",
            title=f"task {number}",
            body=body,
            url=f"https://github.com/acme/api/issues/{number}",
            repository=Repository(owner="acme", name="api"),
            agent=AgentKind.CLAUDE_CODE,
            status=TaskStatus.TODO,
        )

    class Board:
        def tasks(self) -> list[Task]:
            return [_task(7, "rough"), _task(8, mark_refined("already groomed"))]

    class Container:
        board = Board()

        def close(self) -> None: ...

    monkeypatch.setattr(bootstrap, "build", lambda settings, on_event=None: Container())
    client.post("/api/config", json={"token": "t", "owner": "acme", "project_number": 2})

    backlog = client.get("/api/project/backlog").json()

    assert backlog["connected"] is True
    assert backlog["auto_refine"] is True
    assert [item["issue_number"] for item in backlog["pending"]] == [7]


def test_grooming_settings_round_trip(client: TestClient) -> None:
    client.post(
        "/api/config",
        json={"planning_auto_refine": False, "planning_refine_batch_size": 2},
    )

    config = client.get("/api/config").json()

    assert config["planning_auto_refine"] is False
    assert config["planning_refine_batch_size"] == 2


def test_manual_grooming_before_setup_asks_for_setup(client: TestClient) -> None:
    response = client.post("/api/project/backlog/refine", json={})

    assert response.status_code == 400
    assert "Setup" in response.json()["detail"]


def test_creating_a_task_before_setup_asks_for_setup(client: TestClient) -> None:
    response = client.post(
        "/api/project/tasks",
        json={"repository": "acme/app", "title": "Do it", "body": "", "branch": ""},
    )

    assert response.status_code == 400
    assert "Setup" in response.json()["detail"]


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


def test_activity_endpoint_includes_agent_events(client: TestClient, tmp_path: Path) -> None:
    from flywheel.config import load_settings

    ledger = Ledger(load_settings().database_path)
    ledger.save(
        Run(
            id="activity-1",
            project_item_id="I",
            issue_number=7,
            repository="acme/api",
            agent=AgentKind.CODEX,
        )
    )
    ledger.append_event("activity-1", "tool", {"name": "pytest -q"})
    ledger.close()

    data = client.get("/api/activity").json()

    assert data[0]["events"][0]["kind"] == "tool"
    assert data[0]["events"][0]["payload"]["name"] == "pytest -q"


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


def test_manual_project_task_requires_a_title(client: TestClient) -> None:
    response = client.post(
        "/api/project/tasks",
        json={"repository": "acme/app", "title": "", "body": "", "branch": ""},
    )

    assert response.status_code == 400
    assert "title" in response.json()["detail"]


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
