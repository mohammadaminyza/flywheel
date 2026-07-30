import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from flywheel import bootstrap, probes
from flywheel.config import ProjectBriefSettings, Settings, load_settings, save_settings
from flywheel.domain.enums import AgentKind, RunStatus, TaskStatus
from flywheel.domain.project import McpServerSpec
from flywheel.domain.run import Run
from flywheel.domain.task import Repository
from flywheel.services.dispatcher import is_infrastructure_failure, needs_refinement
from flywheel.services.exceptions import BoardNotConfiguredException
from flywheel.services.loop_controller import LoopController
from flywheel.services.scaffold_service import ScaffoldService
from flywheel.storage import Ledger

STATIC = Path(__file__).parent / "static"
MCP_SECRET_MASK = "<saved>"
READINESS_TTL_SECONDS = 20.0


class ConfigPatch(BaseModel):
    token: str | None = None
    owner: str | None = None
    project_number: int | None = None
    project_title: str | None = None
    base_branch: str | None = None
    default_template: str | None = None
    default_agent: str | None = None
    templates_dir: str | None = None
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
    poll_interval_seconds: int | None = None
    max_attempts: int | None = None
    planning_agents: list[str] | None = None
    planning_deep_research: bool | None = None
    planning_auto_refine: bool | None = None
    planning_refine_batch_size: int | None = None


class McpConnectionsBody(BaseModel):
    servers: dict[str, McpServerSpec]


class ProjectPlanBody(ProjectBriefSettings):
    create_tasks: bool = True
    agents: list[str] = Field(default_factory=list)
    deep_research: bool | None = None


class TaskCreateBody(BaseModel):
    repository: str
    title: str
    body: str = ""
    branch: str = ""


class RetryBody(BaseModel):
    project_item_id: str


class RefineBody(BaseModel):
    agents: list[str] = Field(default_factory=list)
    deep_research: bool | None = None
    limit: int | None = None


class AnswerBody(BaseModel):
    answer: str


class GuiState:
    def __init__(self) -> None:
        self.controller: LoopController | None = None
        self.last_event: dict[str, Any] = {}
        self._readiness: list[dict[str, Any]] | None = None
        self._readiness_at = 0.0

    def sink(self, kind: str, payload: dict[str, Any]) -> None:
        self.last_event = {"kind": kind, **payload}

    def invalidate(self) -> None:
        self._readiness = None

    def readiness(self, settings: Settings, force: bool = False) -> list[dict[str, Any]]:
        """Doctor results, cached briefly.

        The loop panel polls every few seconds and these checks shell out to docker, the
        agent CLIs and GitHub. Re-running them on every poll is what made the panel crawl.
        """
        now = time.monotonic()
        stale = now - self._readiness_at > READINESS_TTL_SECONDS
        if force or self._readiness is None or stale:
            self._readiness = [result.model_dump() for result in probes.run_all(settings)]
            self._readiness_at = now
        return self._readiness

    def blocking(self, settings: Settings, force: bool = False) -> list[str]:
        return [
            result["name"]
            for result in self.readiness(settings, force)
            if not result["ok"] and not result["optional"]
        ]


def _redact(settings: Settings) -> dict[str, Any]:
    return {
        "token_set": bool(settings.github.token),
        "owner": settings.github.owner,
        "project_number": settings.github.project_number,
        "project_title": settings.github.project_title,
        "base_branch": settings.github.base_branch,
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
        "poll_interval_seconds": settings.loop.poll_interval_seconds,
        "max_attempts": settings.loop.max_attempts,
        "auto_start": settings.loop.auto_start,
        "templates_dir": settings.templates_dir,
        "bundled_templates_dir": str(settings.bundled_templates_path),
        "planning_agents": [agent.value for agent in settings.planning.agents],
        "planning_deep_research": settings.planning.deep_research,
        "planning_auto_refine": settings.planning.auto_refine,
        "planning_refine_batch_size": settings.planning.refine_batch_size,
        "setup_completed": settings.setup_completed,
        "mcp_servers": {
            name: {
                **spec.model_dump(mode="json", exclude={"env"}),
                "env": {key: MCP_SECRET_MASK for key in spec.env},
            }
            for name, spec in settings.mcp_servers.items()
        },
        "project_brief": settings.project_brief.model_dump(mode="json"),
    }


def _config_payload(settings: Settings) -> dict[str, Any]:
    """Settings plus the templates they resolve to, so the two can never disagree."""
    data = _redact(settings)
    catalog = ScaffoldService(settings.template_roots).catalog()
    data["templates"] = [summary.id for summary in catalog]
    data["template_catalog"] = [summary.model_dump() for summary in catalog]
    return data


def create_gui_app() -> FastAPI:
    state = GuiState()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        settings = load_settings()
        if settings.loop.auto_start and settings.github.configured:
            state.controller = LoopController(settings, status_sink=state.sink)
            state.controller.start()
        yield
        if state.controller:
            state.controller.stop()

    app = FastAPI(title="Flywheel", version="0.1.0", lifespan=lifespan)

    def ledger() -> Ledger:
        return Ledger(load_settings().database_path)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/doctor")
    def doctor(refresh: bool = False) -> list[dict[str, Any]]:
        return state.readiness(load_settings(), force=refresh)

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return _config_payload(load_settings())

    @app.post("/api/config")
    def set_config(patch: ConfigPatch) -> dict[str, Any]:
        settings = load_settings()
        if patch.templates_dir is not None:
            folder = patch.templates_dir.strip()
            if folder and not Path(folder).expanduser().is_dir():
                raise HTTPException(status_code=400, detail=f"{folder} is not a folder")
            settings.templates_dir = folder
        if patch.poll_interval_seconds is not None:
            settings.loop.poll_interval_seconds = max(5, patch.poll_interval_seconds)
        if patch.max_attempts is not None:
            settings.loop.max_attempts = max(1, patch.max_attempts)
        if patch.planning_agents is not None:
            settings.planning.agents = _agents(patch.planning_agents) or list(
                settings.planning.agents
            )
        if patch.planning_deep_research is not None:
            settings.planning.deep_research = patch.planning_deep_research
        if patch.planning_auto_refine is not None:
            settings.planning.auto_refine = patch.planning_auto_refine
        if patch.planning_refine_batch_size is not None:
            settings.planning.refine_batch_size = max(1, patch.planning_refine_batch_size)
        if patch.token is not None:
            settings.github.token = patch.token.strip()
        if patch.owner is not None:
            settings.github.owner = patch.owner.strip()
        if patch.project_number is not None:
            settings.github.project_number = patch.project_number
        if patch.project_title is not None:
            settings.github.project_title = patch.project_title
        if patch.base_branch is not None:
            settings.github.base_branch = patch.base_branch.strip()
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
        state.invalidate()
        if state.controller and state.controller.running:
            state.controller.reconfigure(settings)
        return _config_payload(settings)

    @app.get("/api/mcp")
    def get_mcp_connections() -> dict[str, Any]:
        return {"servers": _redact(load_settings())["mcp_servers"]}

    @app.post("/api/mcp")
    def set_mcp_connections(body: McpConnectionsBody) -> dict[str, Any]:
        settings = load_settings()
        saved = settings.mcp_servers
        merged: dict[str, McpServerSpec] = {}
        for raw_name, incoming in body.servers.items():
            name = raw_name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="connection name is required")
            if incoming.enabled and not incoming.url and not incoming.command:
                raise HTTPException(
                    status_code=400,
                    detail=f"{name}: enter an HTTP URL or local command",
                )
            existing = saved.get(name)
            env = dict(incoming.env)
            if existing:
                env = {
                    key: existing.env.get(key, "") if value == MCP_SECRET_MASK else value
                    for key, value in env.items()
                }
            merged[name] = incoming.model_copy(update={"env": env})
        settings.mcp_servers = merged
        save_settings(settings)
        return {"servers": _redact(settings)["mcp_servers"]}

    @app.get("/api/project/context")
    def project_context() -> dict[str, Any]:
        """The project picked in Setup, with every repository you can actually target.

        A freshly created board has no issues yet, so its repository list is empty. The
        token and owner from Setup already say which repositories exist, so they are used
        instead of asking for the same credentials a second time.
        """
        settings = load_settings()
        if not settings.github.configured:
            return {
                "connected": False,
                "title": settings.github.project_title,
                "owner": settings.github.owner,
                "repositories": [],
                "error": "Finish Setup: a GitHub token, owner and board are required.",
            }

        from flywheel.github.board import BoardService
        from flywheel.github.client import GitHubClient

        client = GitHubClient(settings.github.token)
        title = settings.github.project_title
        board_repositories: list[str] = []
        warning = ""
        try:
            board = BoardService(client, settings.github, default_agent=settings.default_agent)
            title = board.load().title or title
            board_repositories = [
                repository.full_name for repository in board.repositories()
            ]
        except Exception as error:  # noqa: BLE001
            warning = f"{type(error).__name__}: {error}"
        try:
            owned = _owner_repositories(client, settings.github.owner)
        except Exception as error:  # noqa: BLE001
            owned = []
            warning = warning or f"{type(error).__name__}: {error}"
        finally:
            client.close()

        repositories = board_repositories + [
            name for name in owned if name not in board_repositories
        ]
        return {
            "connected": True,
            "title": title,
            "owner": settings.github.owner,
            "number": settings.github.project_number,
            "repositories": repositories,
            "board_repositories": board_repositories,
            "selected": settings.project_brief.repository,
            "warning": warning,
        }

    @app.post("/api/project/plan")
    def plan_project(body: ProjectPlanBody) -> dict[str, Any]:
        settings = load_settings()
        brief = ProjectBriefSettings(
            **body.model_dump(exclude={"create_tasks", "agents", "deep_research"})
        )
        settings.project_brief = brief
        save_settings(settings)
        agents = _agents(body.agents)
        container = _container(settings)
        try:
            result = container.dispatcher.plan_project(brief, agents, body.deep_research)
            created: list[dict[str, Any]] = []
            if body.create_tasks:
                repository = _repository(brief.repository)
                for planned in result.planned_tasks:
                    issue_body = planned.body.rstrip()
                    if planned.priority:
                        issue_body += f"\n\nPriority: **{planned.priority}**"
                    if planned.branch:
                        issue_body += f"\n\nWorkstream branch: `{planned.branch}`"
                    issue = container.issues.create(repository, planned.title, issue_body)
                    item_id = container.board.add_issue(issue["node_id"], planned.branch)
                    container.board.set_item_status(item_id, TaskStatus.TODO)
                    created.append(
                        {
                            "number": issue["number"],
                            "title": issue["title"],
                            "url": issue["html_url"],
                            "branch": planned.branch,
                        }
                    )
            return {
                "summary": result.summary,
                "agents": [agent.value for agent in agents or settings.planning.agents],
                "tasks": [item.model_dump(mode="json") for item in result.planned_tasks],
                "created": created,
            }
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"planning failed: {error}") from error
        finally:
            container.close()

    @app.get("/api/project/backlog")
    def backlog() -> dict[str, Any]:
        """What the loop would groom on its next cycle, and what it already improved."""
        settings = load_settings()
        if not settings.github.configured:
            return {"connected": False, "pending": [], "refined": []}
        container = _container(settings)
        try:
            tasks = container.board.tasks()
        except Exception as error:  # noqa: BLE001
            container.close()
            return {"connected": False, "error": f"{type(error).__name__}: {error}"}
        try:
            store = ledger()
            try:
                events = [
                    {**event["payload"], "at": event["ts"]}
                    for repository in {task.repository.full_name for task in tasks}
                    for event in store.events(f"backlog-{repository}", limit=40)
                    if event["kind"] == "refined"
                ]
            finally:
                store.close()
            return {
                "connected": True,
                "auto_refine": settings.planning.auto_refine,
                "batch_size": settings.planning.refine_batch_size,
                "pending": [
                    {
                        "issue_number": task.issue_number,
                        "title": task.title,
                        "repository": task.repository.full_name,
                        "url": task.url,
                    }
                    for task in tasks
                    if needs_refinement(task)
                ],
                "refined": sorted(events, key=lambda item: str(item.get("at")), reverse=True)[:20],
            }
        finally:
            container.close()

    @app.post("/api/project/backlog/refine")
    def refine_backlog(body: RefineBody) -> dict[str, Any]:
        """Run the grooming pass now instead of waiting for the next loop cycle."""
        settings = load_settings()
        agents = _agents(body.agents)
        container = _container(settings)
        try:
            refined = container.dispatcher.refine_backlog(agents, body.deep_research, body.limit)
            return {
                "refined": refined,
                "agents": [agent.value for agent in agents or settings.planning.agents],
            }
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"refinement failed: {error}") from error
        finally:
            container.close()

    @app.post("/api/project/tasks")
    def create_project_task(body: TaskCreateBody) -> dict[str, Any]:
        settings = load_settings()
        if not body.title.strip():
            raise HTTPException(status_code=400, detail="task title is required")
        container = _container(settings)
        try:
            repository = _repository(body.repository)
            issue = container.issues.create(repository, body.title.strip(), body.body.strip())
            item_id = container.board.add_issue(issue["node_id"], body.branch.strip())
            container.board.set_item_status(item_id, TaskStatus.TODO)
            return {
                "number": issue["number"],
                "title": issue["title"],
                "url": issue["html_url"],
                "branch": body.branch.strip(),
            }
        finally:
            container.close()

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
            failed = run is not None and run.status == RunStatus.FAILED
            columns.setdefault(task.status.value, []).append(
                {
                    "issue_number": task.issue_number,
                    "title": task.title,
                    "repository": task.repository.full_name,
                    "project_item_id": task.project_item_id,
                    "agent": run.agent.value if run else task.agent.value,
                    "url": task.url,
                    "cost_usd": run.cost_usd if run else 0.0,
                    "phase": run.phase.value if run else None,
                    "run_status": run.status.value if run else None,
                    "attempt": run.attempt if run else None,
                    "error": run.error if run else None,
                    "infrastructure": bool(run and is_infrastructure_failure(run.error or "")),
                    "retryable": failed or task.status == TaskStatus.BLOCKED,
                    "pull_request_number": run.pull_request_number if run else None,
                }
            )
        warnings = []
        if "Agent" not in loaded.fields:
            warnings.append(
                f"No 'Agent' field on the board — every card uses the default "
                f"({settings.default_agent.value})."
            )
        if settings.github.branch_field not in loaded.fields:
            warnings.append(
                f"No '{settings.github.branch_field}' text field on the board. "
                "Flywheel creates it automatically when you add a linked task."
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

    @app.post("/api/board/retry")
    def retry_task(body: RetryBody) -> dict[str, Any]:
        """Put a failed or blocked card back in the queue with a fresh set of attempts."""
        settings = load_settings()
        container = _container(settings)
        try:
            tasks = {task.project_item_id: task for task in container.board.tasks()}
            task = tasks.get(body.project_item_id)
            if task is None:
                raise HTTPException(status_code=404, detail="card is no longer on the board")
            container.dispatcher.retry(task)
        finally:
            container.close()
        if state.controller and state.controller.running:
            state.controller.poke()
        return {"issue_number": task.issue_number, "status": TaskStatus.TODO.value}

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

    @app.get("/api/activity")
    def activity() -> list[dict[str, Any]]:
        store = ledger()
        try:
            items: list[dict[str, Any]] = []
            for run in store.list_runs(50):
                data = run.model_dump(mode="json")
                data["events"] = store.events(run.id, limit=120)
                items.append(data)
            return items
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
        settings = load_settings()
        status: dict[str, Any] = (
            state.controller.status()
            if state.controller
            else {
                "running": False,
                "stopping": False,
                "busy": False,
                "error": None,
                "ticks": 0,
                "started_at": None,
                "last_tick_at": None,
                "last_tick": {},
                "poll_interval_seconds": settings.loop.poll_interval_seconds,
                "events": [],
            }
        )
        status["last_event"] = state.last_event
        status["blocking"] = state.blocking(settings)
        status["auto_start"] = settings.loop.auto_start
        status["max_parallel"] = settings.runner.max_parallel
        status["max_attempts"] = settings.loop.max_attempts
        return status

    @app.post("/api/loop/start")
    def loop_start() -> dict[str, Any]:
        settings = load_settings()
        blocking = state.blocking(settings, force=True)
        if blocking:
            raise HTTPException(status_code=400, detail=f"not ready: {', '.join(blocking)}")
        if state.controller is None:
            state.controller = LoopController(settings, status_sink=state.sink)
        elif not state.controller.running:
            state.controller.reconfigure(settings)
        settings.loop.auto_start = True
        save_settings(settings)
        state.controller.start()
        return {"running": True}

    @app.post("/api/loop/stop")
    def loop_stop() -> dict[str, Any]:
        stopped = True
        if state.controller:
            # The controller is kept even while a long cycle finishes, so a second start
            # can never leave two loops dispatching the same board.
            stopped = state.controller.stop()
        settings = load_settings()
        settings.loop.auto_start = False
        save_settings(settings)
        return {"running": not stopped, "stopping": not stopped}

    @app.post("/api/loop/poke")
    def loop_poke() -> dict[str, Any]:
        if state.controller is None or not state.controller.running:
            raise HTTPException(status_code=400, detail="the loop is not running")
        state.controller.poke()
        return {"running": True, "poked": True}

    return app


def _agents(values: list[str] | None) -> list[AgentKind]:
    agents: list[AgentKind] = []
    for value in values or []:
        try:
            agents.append(AgentKind(value))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=f"unknown agent '{value}'") from error
    return list(dict.fromkeys(agents))


def _container(settings: Settings) -> bootstrap.Container:
    try:
        return bootstrap.build(settings)
    except BoardNotConfiguredException as error:
        raise HTTPException(
            status_code=400,
            detail="connect a GitHub token, owner and board in Setup first",
        ) from error


def _owner_repositories(client: Any, owner: str, limit: int = 200) -> list[str]:
    """Repositories the configured token can push to, newest first."""
    names: list[str] = []
    for path, params in (
        ("/user/repos", {"affiliation": "owner,collaborator,organization_member"}),
        (f"/users/{owner}/repos", {"type": "owner"}),
    ):
        try:
            page = client.get(path, {**params, "per_page": 100, "sort": "updated"})
        except Exception:  # noqa: BLE001
            continue
        for entry in page or []:
            name = entry.get("full_name")
            if name and name not in names:
                names.append(name)
        if names:
            break
    return names[:limit]


def _repository(value: str) -> Repository:
    parts = [part.strip() for part in value.split("/", 1)]
    if len(parts) != 2 or not all(parts):
        raise HTTPException(status_code=400, detail="repository must be in owner/name format")
    return Repository(owner=parts[0], name=parts[1])


app = create_gui_app()


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
