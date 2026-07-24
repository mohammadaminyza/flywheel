import os
from pathlib import Path
from typing import Any

import tomlkit
from pydantic import BaseModel, Field

from flywheel.domain.enums import AgentKind, AuthMode, ExecutionMode, Transport


def config_dir() -> Path:
    return Path(os.environ.get("FLYWHEEL_HOME", Path.home() / ".flywheel"))


def config_path() -> Path:
    return config_dir() / "config.toml"


class GitHubSettings(BaseModel):
    token: str = ""
    owner: str = ""
    project_number: int = 0
    project_title: str = ""
    status_field: str = "Status"
    agent_field: str = "Agent"
    template_field: str = "Template"
    repository_field: str = "Repository"

    @property
    def configured(self) -> bool:
        return bool(self.token and self.owner and self.project_number)


class AgentSettings(BaseModel):
    enabled: bool = True
    transport: Transport = Transport.CLI
    model: str = ""
    max_turns: int = 120
    api_key: str = ""


class RunnerSettings(BaseModel):
    image: str = "flywheel-runner:latest"
    execution_mode: ExecutionMode = ExecutionMode.CONTAINER
    auth_mode: AuthMode = AuthMode.SUBSCRIPTION
    max_parallel: int = 2
    max_parallel_per_agent: int = 1
    timeout_seconds: int = 5400


class DeploySettings(BaseModel):
    host: str = ""
    user: str = "deploy"
    auth_method: str = "key"
    ssh_key_path: str = ""
    ssh_password: str = ""
    domain: str = ""
    registry: str = "ghcr.io"

    @property
    def configured(self) -> bool:
        return bool(self.host and self.domain)

    @property
    def has_credentials(self) -> bool:
        if self.auth_method == "password":
            return bool(self.ssh_password)
        return bool(self.ssh_key_path)


class TelegramSettings(BaseModel):
    bot_token: str = ""
    chat_id: str = ""
    username: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.bot_token) and bool(self.chat_id or self.username)

    @property
    def target(self) -> str:
        if self.chat_id:
            return self.chat_id
        if self.username:
            return self.username if self.username.startswith("@") else f"@{self.username}"
        return ""


class LoopSettings(BaseModel):
    poll_interval_seconds: int = 60
    max_attempts: int = 3
    question_timeout_hours: int = 48
    auto_start: bool = False


class Settings(BaseModel):
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    agents: dict[AgentKind, AgentSettings] = Field(
        default_factory=lambda: {
            AgentKind.CLAUDE_CODE: AgentSettings(transport=Transport.CLI),
            AgentKind.CODEX: AgentSettings(transport=Transport.MCP),
        }
    )
    runner: RunnerSettings = Field(default_factory=RunnerSettings)
    deploy: DeploySettings = Field(default_factory=DeploySettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    loop: LoopSettings = Field(default_factory=LoopSettings)
    default_template: str = "python-fastapi-nextjs"
    default_agent: AgentKind = AgentKind.CLAUDE_CODE
    templates_dir: str = ""
    workspace_dir: str = ""
    setup_completed: bool = False

    @property
    def templates_path(self) -> Path:
        if self.templates_dir:
            return Path(self.templates_dir)
        return Path(__file__).resolve().parent.parent / "templates"

    @property
    def workspace_path(self) -> Path:
        if self.workspace_dir:
            return Path(self.workspace_dir)
        return config_dir() / "workspaces"

    @property
    def database_path(self) -> Path:
        return config_dir() / "flywheel.db"

    def agent(self, kind: AgentKind) -> AgentSettings:
        return self.agents.get(kind, AgentSettings())


def _apply_env_overrides(settings: Settings) -> Settings:
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        settings.github.token = token
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        settings.agents[AgentKind.CLAUDE_CODE].api_key = anthropic_key
    codex_key = os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if codex_key:
        settings.agents[AgentKind.CODEX].api_key = codex_key
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        settings.telegram.bot_token = telegram_token
    return settings


def load_settings(path: Path | None = None) -> Settings:
    target = path or config_path()
    if not target.exists():
        return _apply_env_overrides(Settings())
    raw: dict[str, Any] = tomlkit.parse(target.read_text(encoding="utf-8")).unwrap()
    return _apply_env_overrides(Settings(**raw))


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump(mode="json", exclude={"templates_path", "workspace_path"})
    target.write_text(tomlkit.dumps(payload), encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o600)
    return target
