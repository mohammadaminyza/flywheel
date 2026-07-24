from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from flywheel import bootstrap, probes
from flywheel.config import Settings, load_settings, save_settings
from flywheel.domain.enums import AgentKind, RunStatus
from flywheel.domain.run import Run
from flywheel.services.loop_controller import LoopController
from flywheel.storage import Ledger

STATIC = Path(__file__).parent / "static"


class ConfigPatch(BaseModel):
    token: str | None = None
    owner: str | None = None
    project_number: int | None = None
    project_title: str | None = None
    default_template: str | None = None
    default_agent: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_username: str | None = None
    deploy_host: str | None = None
    deploy_user: str | None = None
    deploy_auth_method: str | None = None
    deploy_ssh_key_path: str | None = None
    deploy_ssh_password: str | None = None
    deploy_domain: str | None = None
    max_parallel: int | None = None


class AnswerBody(BaseModel):
    answer: str


class GuiState:
    def __init__(self) -> None:
        self.controller: LoopController | None = None
        self.last_event: dict[str, Any] = {}

    def sink(self, kind: str, payload: dict[str, Any]) -> None:
        self.last_event = {"kind": kind, **payload}


def _redact(settings: Settings) -> dict[str, Any]:
    return {
        "token_set": bool(settings.github.token),
        "owner": settings.github.owner,
        "project_number": settings.github.project_number,
        "project_title": settings.github.project_title,
        "default_template": settings.default_template,
        "default_agent": settings.default_agent.value,
        "telegram_bot_set": bool(settings.telegram.bot_token),
        "telegram_chat_id": settings.telegram.chat_id,
        "telegram_username": settings.telegram.username,
        "telegram_configured": settings.telegram.configured,
        "deploy_host": settings.deploy.host,
        "deploy_user": settings.deploy.user,
        "deploy_auth_method": settings.deploy.auth_method,
        "deploy_ssh_key_path": settings.deploy.ssh_key_path,
        "deploy_ssh_password_set": bool(settings.deploy.ssh_password),
        "deploy_domain": settings.deploy.domain,
        "deploy_configured": settings.deploy.configured,
        "max_parallel": settings.runner.max_parallel,
        "setup_completed": settings.setup_completed,
    }


def create_gui_app() -> FastAPI:
    app = FastAPI(title="Flywheel", version="0.1.0")
    state = GuiState()

    def ledger() -> Ledger:
        return Ledger(load_settings().database_path)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/doctor")
    def doctor() -> list[dict[str, Any]]:
        return [result.model_dump() for result in probes.run_all(load_settings())]

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        settings = load_settings()
        data = _redact(settings)
        data["templates"] = probes.probe_templates(settings).detail.split(", ")
        return data

    @app.post("/api/config")
    def set_config(patch: ConfigPatch) -> dict[str, Any]:
        settings = load_settings()
        if patch.token is not None:
            settings.github.token = patch.token.strip()
        if patch.owner is not None:
            settings.github.owner = patch.owner.strip()
        if patch.project_number is not None:
            settings.github.project_number = patch.project_number
        if patch.project_title is not None:
            settings.github.project_title = patch.project_title
        if patch.default_template is not None:
            settings.default_template = patch.default_template
        if patch.default_agent is not None:
            settings.default_agent = AgentKind(patch.default_agent)
        if patch.telegram_bot_token is not None:
            settings.telegram.bot_token = patch.telegram_bot_token.strip()
        if patch.telegram_chat_id is not None:
            settings.telegram.chat_id = patch.telegram_chat_id.strip()
        if patch.telegram_username is not None:
            settings.telegram.username = patch.telegram_username.strip()
        if patch.deploy_host is not None:
            settings.deploy.host = patch.deploy_host.strip()
        if patch.deploy_user is not None:
            settings.deploy.user = patch.deploy_user.strip() or "deploy"
        if patch.deploy_auth_method is not None:
            settings.deploy.auth_method = patch.deploy_auth_method.strip() or "key"
        if patch.deploy_ssh_key_path is not None:
            settings.deploy.ssh_key_path = patch.deploy_ssh_key_path.strip()
        if patch.deploy_ssh_password is not None:
            settings.deploy.ssh_password = patch.deploy_ssh_password
        if patch.deploy_domain is not None:
            settings.deploy.domain = patch.deploy_domain.strip()
        if patch.max_parallel is not None:
            settings.runner.max_parallel = max(1, patch.max_parallel)
        if settings.github.configured:
            settings.setup_completed = True
        save_settings(settings)
        return _redact(settings)

    @app.post("/api/token/test")
    def test_token(patch: ConfigPatch) -> dict[str, Any]:
        token = (patch.token or load_settings().github.token).strip()
        result = probes.probe_github_token(token)
        payload = result.model_dump()
        if result.ok and not (patch.owner or "").strip():
            payload["login"] = probes.detect_github_login(token)
        return payload

    @app.post("/api/deploy/test")
    def test_deploy(patch: ConfigPatch) -> dict[str, Any]:
        settings = load_settings()
        host = (
            patch.deploy_host if patch.deploy_host is not None else settings.deploy.host
        ).strip()
        user = (
            patch.deploy_user if patch.deploy_user is not None else settings.deploy.user
        ).strip()
        method = (
            patch.deploy_auth_method
            if patch.deploy_auth_method is not None
            else settings.deploy.auth_method
        ).strip() or "key"
        key = (
            patch.deploy_ssh_key_path
            if patch.deploy_ssh_key_path is not None
            else settings.deploy.ssh_key_path
        ).strip()
        password = (
            patch.deploy_ssh_password if patch.deploy_ssh_password else settings.deploy.ssh_password
        )
        settings.deploy.host = host
        settings.deploy.user = user or "deploy"
        settings.deploy.auth_method = method
        settings.deploy.ssh_key_path = key
        if patch.deploy_ssh_password:
            settings.deploy.ssh_password = patch.deploy_ssh_password
        if patch.deploy_domain is not None:
            settings.deploy.domain = patch.deploy_domain.strip()
        save_settings(settings)
        return probes.probe_deploy_host(host, user or "deploy", key, password, method).model_dump()

    @app.post("/api/telegram/test")
    def test_telegram(patch: ConfigPatch) -> dict[str, Any]:
        from flywheel.config import TelegramSettings
        from flywheel.delivery.telegram import TelegramNotifier

        settings = load_settings()
        token = (
            patch.telegram_bot_token if patch.telegram_bot_token else settings.telegram.bot_token
        ).strip()
        chat = (
            patch.telegram_chat_id
            if patch.telegram_chat_id is not None
            else settings.telegram.chat_id
        ).strip()
        username = (
            patch.telegram_username
            if patch.telegram_username is not None
            else settings.telegram.username
        ).strip()

        bot = probes.telegram_bot_username(token) if token else None
        # Persist whatever the user typed so it survives a reload.
        settings.telegram.bot_token = token
        settings.telegram.username = username
        if chat:
            settings.telegram.chat_id = chat

        if bot is None:
            save_settings(settings)
            return probes.ProbeResult(
                name="Telegram", ok=False, detail="bot token rejected or missing", optional=True
            ).model_dump()

        chat_id = chat
        if not chat_id and username:
            chat_id = probes.resolve_telegram_chat_id(token, username) or ""
            if chat_id:
                settings.telegram.chat_id = chat_id
        save_settings(settings)

        if not chat_id:
            handle = f"@{bot}"
            result = probes.ProbeResult(
                name="Telegram",
                ok=False,
                detail=f"Bot {handle} is valid, but I need your chat.",
                fix=(
                    f"Open Telegram, send any message to {handle} (press Start), "
                    "then click Send test message again — I'll find your chat id automatically."
                ),
                optional=True,
            ).model_dump()
            result["chat_id"] = ""
            return result

        sent = TelegramNotifier(TelegramSettings(bot_token=token, chat_id=chat_id)).send(
            "Flywheel is connected. You'll get preview links and screenshots here."
        )
        result = probes.ProbeResult(
            name="Telegram",
            ok=sent,
            detail=(
                f"Test message sent (chat {chat_id})."
                if sent
                else "Bot is valid but the message could not be delivered."
            ),
            fix="" if sent else f"Press Start in @{bot} first, then test again.",
            optional=True,
        ).model_dump()
        result["chat_id"] = chat_id
        return result

    @app.get("/api/boards")
    def boards() -> list[dict[str, Any]]:
        settings = load_settings()
        if not settings.github.token or not settings.github.owner:
            return []
        return probes.list_boards(settings.github.token, settings.github.owner)

    @app.get("/api/board")
    def board_view() -> dict[str, Any]:
        settings = load_settings()
        if not settings.github.configured:
            return {"connected": False, "columns": {}, "warnings": []}
        from flywheel.github.board import BoardService
        from flywheel.github.client import GitHubClient

        client = GitHubClient(settings.github.token)
        try:
            service = BoardService(client, settings.github, default_agent=settings.default_agent)
            loaded = service.load()
            tasks = service.tasks()
            missing = service.missing_status_options()
        except Exception as error:  # noqa: BLE001
            client.close()
            return {"connected": False, "error": f"{type(error).__name__}: {error}", "columns": {}}
        finally:
            client.close()

        store = ledger()
        try:
            runs: dict[str, Run] = {}
            for candidate in store.list_runs(300):
                runs.setdefault(candidate.project_item_id, candidate)
        finally:
            store.close()

        columns: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            run = runs.get(task.project_item_id)
            columns.setdefault(task.status.value, []).append(
                {
                    "issue_number": task.issue_number,
                    "title": task.title,
                    "repository": task.repository.full_name,
                    "agent": run.agent.value if run else task.agent.value,
                    "url": task.url,
                    "cost_usd": run.cost_usd if run else 0.0,
                    "phase": run.phase.value if run else None,
                    "run_status": run.status.value if run else None,
                    "attempt": run.attempt if run else None,
                    "error": run.error if run else None,
                    "pull_request_number": run.pull_request_number if run else None,
                }
            )
        warnings = []
        if "Agent" not in loaded.fields:
            warnings.append(
                f"No 'Agent' field on the board — every card uses the default "
                f"({settings.default_agent.value})."
            )
        if missing:
            warnings.append(
                "Status is missing options: " + ", ".join(missing) + " (the factory falls back)."
            )
        return {
            "connected": True,
            "title": loaded.title,
            "columns": columns,
            "warnings": warnings,
        }

    @app.get("/api/runs")
    def runs() -> dict[str, Any]:
        store = ledger()
        try:
            grouped: dict[str, list[dict[str, Any]]] = {status.value: [] for status in RunStatus}
            for run in store.list_runs(200):
                grouped[run.status.value].append(run.model_dump(mode="json"))
            return {
                "runs": grouped,
                "active": store.active_count(),
                "waiting": len(store.awaiting_input()),
            }
        finally:
            store.close()

    @app.get("/api/questions")
    def questions() -> list[dict[str, Any]]:
        store = ledger()
        try:
            items: list[dict[str, Any]] = []
            for run in store.awaiting_input():
                events = store.events(run.id)
                asked: list[str] = next(
                    (
                        event["payload"].get("questions")
                        for event in reversed(events)
                        if event["kind"] == "needs_input"
                    ),
                    [],
                )
                data = run.model_dump(mode="json")
                data["questions"] = asked
                items.append(data)
            return items
        finally:
            store.close()

    @app.post("/api/questions/{run_id}/answer")
    def answer(run_id: str, body: AnswerBody) -> dict[str, str]:
        settings = load_settings()
        store = ledger()
        try:
            run = store.get(run_id)
        finally:
            store.close()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if not body.answer.strip():
            raise HTTPException(status_code=400, detail="answer is empty")
        container = bootstrap.build(settings)
        try:
            tasks = {task.project_item_id: task for task in container.board.tasks()}
            task = tasks.get(run.project_item_id)
            if task is None:
                raise HTTPException(status_code=409, detail="card is no longer on the board")
            container.client.post(
                f"/repos/{task.repository.full_name}/issues/{task.issue_number}/comments",
                {"body": body.answer.strip()},
            )
        finally:
            container.close()
        return {"status": "posted"}

    @app.get("/api/loop")
    def loop_status() -> dict[str, Any]:
        running = state.controller is not None and state.controller.running
        error = state.controller.error if state.controller else None
        blocking = [r.name for r in probes.run_all(load_settings()) if not r.ok and not r.optional]
        return {
            "running": running,
            "error": error,
            "last_event": state.last_event,
            "blocking": blocking,
        }

    @app.post("/api/loop/start")
    def loop_start() -> dict[str, Any]:
        settings = load_settings()
        blocking = [r.name for r in probes.run_all(settings) if not r.ok and not r.optional]
        if blocking:
            raise HTTPException(status_code=400, detail=f"not ready: {', '.join(blocking)}")
        if state.controller is None:
            state.controller = LoopController(settings, status_sink=state.sink)
        state.controller.start()
        return {"running": True}

    @app.post("/api/loop/stop")
    def loop_stop() -> dict[str, Any]:
        if state.controller:
            state.controller.stop()
        return {"running": False}

    return app


app = create_gui_app()


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
