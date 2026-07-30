import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx
from pydantic import BaseModel

from flywheel.config import Settings
from flywheel.domain.enums import AgentKind, ExecutionMode

try:
    import paramiko
except ModuleNotFoundError:  # pragma: no cover
    paramiko = None

GITHUB_API = "https://api.github.com"
GITHUB_MCP_IMAGE = "ghcr.io/github/github-mcp-server"


class ProbeResult(BaseModel):
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    optional: bool = False

    @property
    def icon(self) -> str:
        if self.ok:
            return "[green]OK[/green]"
        return "[yellow]--[/yellow]" if self.optional else "[red]XX[/red]"


def _run(command: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


def find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        for suffix in (".cmd", ".exe", ".ps1"):
            found = shutil.which(name + suffix)
            if found:
                return found
    return None


def _npm_global_root() -> Path | None:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "npm" if appdata else None
    result = _run(["npm", "root", "-g"])
    return Path(result.stdout.strip()) if result.returncode == 0 else None


def _npm_package_installed(package: str) -> bool:
    root = _npm_global_root()
    if root is None:
        return False
    scope, _, name = package.partition("/")
    modules = root / "node_modules"
    return (modules / scope / name).exists() if name else (modules / scope).exists()


def probe_docker() -> ProbeResult:
    executable = find_executable("docker")
    if executable is None:
        return ProbeResult(
            name="Docker",
            ok=False,
            detail="docker not found on PATH",
            fix="Install Docker Desktop from https://docs.docker.com/desktop/",
        )
    result = _run([executable, "version", "--format", "{{.Server.Version}}"])
    if result.returncode != 0:
        return ProbeResult(
            name="Docker",
            ok=False,
            detail="docker is installed but the daemon is not responding",
            fix="Start Docker Desktop and wait for the whale icon to settle",
        )
    return ProbeResult(name="Docker", ok=True, detail=f"daemon {result.stdout.strip()}")


def probe_claude_code() -> ProbeResult:
    executable = find_executable("claude")
    if executable is None:
        return ProbeResult(
            name="Claude Code",
            ok=False,
            detail="claude not found on PATH",
            fix="Install with: npm i -g @anthropic-ai/claude-code",
        )
    credentials = Path.home() / ".claude" / ".credentials.json"
    if not credentials.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
        return ProbeResult(
            name="Claude Code",
            ok=False,
            detail="installed but not logged in",
            fix="Run `claude` once and sign in, or set ANTHROPIC_API_KEY",
        )
    result = _run([executable, "--version"])
    version = result.stdout.strip() or "unknown version"
    auth = "subscription login" if credentials.exists() else "ANTHROPIC_API_KEY"
    return ProbeResult(name="Claude Code", ok=True, detail=f"{version} ({auth})")


def probe_codex() -> ProbeResult:
    executable = find_executable("codex")
    credentials = Path.home() / ".codex" / "auth.json"
    if executable is None:
        if _npm_package_installed("@openai/codex"):
            return ProbeResult(
                name="Codex",
                ok=False,
                detail="package installed but the PATH shim is missing or corrupt",
                fix="Repair with: npm i -g @openai/codex",
                optional=True,
            )
        return ProbeResult(
            name="Codex",
            ok=False,
            detail="codex not found on PATH",
            fix="Install with: npm i -g @openai/codex",
            optional=True,
        )
    if not credentials.exists() and not os.environ.get("CODEX_API_KEY"):
        return ProbeResult(
            name="Codex",
            ok=False,
            detail="installed but not logged in",
            fix="Run `codex login` to sign in with your ChatGPT account",
            optional=True,
        )
    result = _run([executable, "--version"])
    version = result.stdout.strip() or "unknown version"
    auth = "ChatGPT login" if credentials.exists() else "CODEX_API_KEY"
    return ProbeResult(name="Codex", ok=True, detail=f"{version} ({auth})", optional=True)


def probe_agent(kind: AgentKind) -> ProbeResult:
    return probe_claude_code() if kind == AgentKind.CLAUDE_CODE else probe_codex()


def probe_github_token(token: str) -> ProbeResult:
    if not token:
        return ProbeResult(
            name="GitHub token",
            ok=False,
            detail="no token configured",
            fix="Create a fine-grained or classic PAT with repo + project scopes",
        )
    try:
        response = httpx.get(
            f"{GITHUB_API}/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=15,
        )
    except httpx.HTTPError as error:
        return ProbeResult(name="GitHub token", ok=False, detail=f"network error: {error}")
    if response.status_code != 200:
        return ProbeResult(
            name="GitHub token",
            ok=False,
            detail=f"rejected by GitHub ({response.status_code})",
            fix="Check the token has not expired and was pasted in full",
        )
    login = response.json().get("login", "?")
    scopes = response.headers.get("x-oauth-scopes", "")
    if scopes and "project" not in scopes and "repo" in scopes:
        return ProbeResult(
            name="GitHub token",
            ok=False,
            detail=f"authenticated as {login} but missing the 'project' scope",
            fix="Regenerate the classic PAT with 'project' ticked (needed to read the board)",
        )
    return ProbeResult(name="GitHub token", ok=True, detail=f"authenticated as {login}")


def probe_github_mcp_image() -> ProbeResult:
    executable = find_executable("docker")
    if executable is None:
        return ProbeResult(name="GitHub MCP image", ok=False, detail="docker unavailable")
    result = _run([executable, "image", "inspect", GITHUB_MCP_IMAGE])
    if result.returncode != 0:
        return ProbeResult(
            name="GitHub MCP image",
            ok=False,
            detail="image not pulled yet",
            fix=f"Pull it with: docker pull {GITHUB_MCP_IMAGE}",
        )
    return ProbeResult(name="GitHub MCP image", ok=True, detail=GITHUB_MCP_IMAGE)


def probe_runner_image(image: str) -> ProbeResult:
    executable = find_executable("docker")
    if executable is None:
        return ProbeResult(name="Runner image", ok=False, detail="docker unavailable")
    result = _run([executable, "image", "inspect", image])
    if result.returncode != 0:
        return ProbeResult(
            name="Runner image",
            ok=False,
            detail=f"{image} has not been built",
            fix="Build it with: flywheel build-runner",
        )
    return ProbeResult(name="Runner image", ok=True, detail=image)


def probe_telegram_status(settings: Settings) -> ProbeResult:
    telegram = settings.telegram
    if not telegram.configured:
        return ProbeResult(
            name="Telegram",
            ok=False,
            detail="not configured",
            fix="Create a bot with @BotFather, then give a chat id or @username",
            optional=True,
        )
    return ProbeResult(
        name="Telegram",
        ok=True,
        detail=f"configured ({telegram.target}) — use Send test message to verify",
        optional=True,
    )


def probe_telegram(bot_token: str, target: str) -> ProbeResult:
    if not bot_token or not target:
        return ProbeResult(
            name="Telegram",
            ok=False,
            detail="not configured",
            fix="Create a bot with @BotFather, then give a chat id or @username",
            optional=True,
        )
    try:
        response = httpx.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=15)
    except httpx.HTTPError as error:
        return ProbeResult(
            name="Telegram", ok=False, detail=f"network error: {error}", optional=True
        )
    if response.status_code != 200:
        return ProbeResult(
            name="Telegram",
            ok=False,
            detail="bot token rejected",
            fix="Re-copy the token from @BotFather",
            optional=True,
        )
    username = response.json().get("result", {}).get("username", "?")
    return ProbeResult(name="Telegram", ok=True, detail=f"@{username}", optional=True)


def telegram_bot_username(bot_token: str) -> str | None:
    try:
        response = httpx.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=15)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    name: str | None = response.json().get("result", {}).get("username")
    return name


def resolve_telegram_chat_id(bot_token: str, username: str) -> str | None:
    handle = username.lstrip("@").lower()
    if not handle:
        return None
    try:
        response = httpx.get(f"https://api.telegram.org/bot{bot_token}/getUpdates", timeout=15)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    for update in reversed(response.json().get("result", [])):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat", {})
        if str(chat.get("username", "")).lower() == handle:
            return str(chat.get("id"))
    return None


# Plain `docker version` (no --format) so output is identical whether the remote
# default shell is sh, cmd.exe or PowerShell — Windows OpenSSH friendly.
DOCKER_VERSION_CMD = "docker version"
_DOCKER_MISSING = ("not recognized", "not found", "command not found")


def _parse_server_version(text: str) -> str | None:
    cleaned = text.strip()
    if re.fullmatch(r"[0-9][0-9A-Za-z.\-+]*", cleaned):
        return cleaned
    in_server = False
    for line in cleaned.splitlines():
        if re.match(r"\s*Server\b", line):
            in_server = True
        if in_server:
            match = re.search(r"Version:\s*(\S+)", line)
            if match:
                return match.group(1)
    return None


def _ssh_docker_version(
    host: str, user: str, key_path: str, password: str, auth_method: str, port: int = 22
) -> tuple[bool, str]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect: dict[str, object] = {
        "hostname": host,
        "port": port,
        "username": user,
        "timeout": 12,
        "banner_timeout": 12,
        "auth_timeout": 12,
    }
    if auth_method == "password" and password:
        connect.update(password=password, look_for_keys=False, allow_agent=False)
    elif key_path:
        connect.update(key_filename=key_path)
    try:
        client.connect(**connect)
        _, stdout, stderr = client.exec_command(DOCKER_VERSION_CMD, timeout=20)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
    except Exception as error:  # noqa: BLE001
        return False, f"{type(error).__name__}: {error}"
    finally:
        client.close()

    version = _parse_server_version(out)
    if version:
        return True, version
    combined = (out + err).lower()
    if any(marker in combined for marker in _DOCKER_MISSING):
        return False, "connected, but 'docker' was not found on PATH for that user"
    if "cannot connect to the docker daemon" in combined or "error during connect" in combined:
        return False, "connected, but the Docker daemon is not running on the host"
    detail = err.strip() or out.strip() or "connected, but could not read the Docker version"
    return False, detail[:160]


def probe_deploy_host(
    host: str, user: str, key_path: str = "", password: str = "", auth_method: str = "key"
) -> ProbeResult:
    if not host:
        return ProbeResult(
            name="Deploy host",
            ok=False,
            detail="not configured (preview environments disabled)",
            fix="Add a Docker host reachable over SSH to enable preview URLs",
            optional=True,
        )
    if paramiko is None:
        return ProbeResult(
            name="Deploy host",
            ok=False,
            detail="the paramiko SSH library is not installed",
            fix="Install it with: uv sync",
            optional=True,
        )
    ok, detail = _ssh_docker_version(host, user, key_path, password, auth_method)
    if ok:
        return ProbeResult(name="Deploy host", ok=True, detail=f"{user}@{host} docker {detail}")
    return ProbeResult(
        name="Deploy host",
        ok=False,
        detail=detail[:160],
        fix="Check the host, port 22, the user, and your key or password.",
        optional=True,
    )


def probe_deploy_status(settings: Settings) -> ProbeResult:
    deploy = settings.deploy
    if not deploy.host:
        return ProbeResult(
            name="Deploy host",
            ok=False,
            detail="not configured (preview environments disabled)",
            fix="Add a Docker host reachable over SSH to enable preview URLs",
            optional=True,
        )
    return ProbeResult(
        name="Deploy host",
        ok=True,
        detail=f"{deploy.user}@{deploy.host} (use Test connection to verify)",
        optional=True,
    )


def probe_board(settings: Settings) -> ProbeResult:
    github = settings.github
    if not github.configured:
        return ProbeResult(
            name="Project board",
            ok=False,
            detail="no board selected",
            fix="Run the setup wizard and pick a GitHub Projects board",
        )
    query = """
    query($owner: String!, $number: Int!) {
      user(login: $owner) { projectV2(number: $number) { title } }
      organization(login: $owner) { projectV2(number: $number) { title } }
    }
    """
    try:
        response = httpx.post(
            f"{GITHUB_API}/graphql",
            headers={"Authorization": f"Bearer {github.token}"},
            json={
                "query": query,
                "variables": {"owner": github.owner, "number": github.project_number},
            },
            timeout=20,
        )
    except httpx.HTTPError as error:
        return ProbeResult(name="Project board", ok=False, detail=f"network error: {error}")
    payload = response.json().get("data") or {}
    for holder in ("user", "organization"):
        project = (payload.get(holder) or {}).get("projectV2")
        if project:
            return ProbeResult(name="Project board", ok=True, detail=project["title"])
    return ProbeResult(
        name="Project board",
        ok=False,
        detail=f"board #{github.project_number} not visible to this token",
        fix="Check the owner and number, and that the token has the 'project' scope",
    )


def probe_templates(settings: Settings) -> ProbeResult:
    custom = settings.templates_dir.strip()
    if custom and not Path(custom).expanduser().is_dir():
        return ProbeResult(
            name="Templates",
            ok=False,
            detail=f"your templates folder {custom} does not exist",
            fix="Point Setup at an existing folder, or clear it to use the bundled templates",
        )
    found: list[str] = []
    for root in settings.template_roots:
        if not root.is_dir():
            continue
        found += [
            child.name
            for child in sorted(root.iterdir())
            if (child / "template.yml").exists() and child.name not in found
        ]
    if not found:
        return ProbeResult(
            name="Templates",
            ok=False,
            detail="no templates with a template.yml",
            fix="Reinstall the factory, or point Setup at a folder that holds your templates",
        )
    return ProbeResult(name="Templates", ok=True, detail=", ".join(found))


def run_all(settings: Settings) -> list[ProbeResult]:
    """Every check, with the ones that cannot block this configuration marked optional."""
    containerised = settings.runner.execution_mode == ExecutionMode.CONTAINER
    docker = probe_docker()
    mcp_image = probe_github_mcp_image()
    runner_image = probe_runner_image(settings.runner.image)
    for result in (docker, mcp_image, runner_image):
        # Nothing Docker-shaped can block a factory that runs the agents on the host.
        result.optional = result.optional or not containerised
    agents = {
        AgentKind.CLAUDE_CODE: probe_claude_code(),
        AgentKind.CODEX: probe_codex(),
    }
    for kind, result in agents.items():
        # An agent only has to work when the board can actually route a card to it.
        needed = kind == settings.default_agent or kind in settings.planning.agents
        result.optional = result.optional or not needed
    if not any(result.ok for result in agents.values()):
        agents[settings.default_agent].optional = False
    return [
        docker,
        agents[AgentKind.CLAUDE_CODE],
        agents[AgentKind.CODEX],
        probe_github_token(settings.github.token),
        probe_board(settings),
        mcp_image,
        runner_image,
        probe_templates(settings),
        probe_deploy_status(settings),
        probe_telegram_status(settings),
    ]


def blocking(settings: Settings) -> list[str]:
    """Names of the checks that must pass before the loop may start."""
    return [result.name for result in run_all(settings) if not result.ok and not result.optional]


def credential_mounts() -> dict[str, str]:
    mounts: dict[str, str] = {}
    claude_credentials = Path.home() / ".claude" / ".credentials.json"
    if claude_credentials.exists():
        mounts[str(claude_credentials)] = "/home/agent/.claude/.credentials.json"
    codex_auth = Path.home() / ".codex" / "auth.json"
    if codex_auth.exists():
        mounts[str(codex_auth)] = "/home/agent/.codex/auth.json"
    return mounts


def detect_github_login(token: str) -> str | None:
    try:
        response = httpx.get(
            f"{GITHUB_API}/user", headers={"Authorization": f"Bearer {token}"}, timeout=15
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    login: str | None = response.json().get("login")
    return login


def list_boards(token: str, owner: str) -> list[dict[str, object]]:
    query = """
    query($owner: String!) {
      user(login: $owner) {
        projectsV2(first: 50) { nodes { number title closed } }
      }
      organization(login: $owner) {
        projectsV2(first: 50) { nodes { number title closed } }
      }
    }
    """
    try:
        response = httpx.post(
            f"{GITHUB_API}/graphql",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query, "variables": {"owner": owner}},
            timeout=20,
        )
    except httpx.HTTPError:
        return []
    payload = response.json().get("data") or {}
    for holder in ("user", "organization"):
        container = payload.get(holder) or {}
        nodes = (container.get("projectsV2") or {}).get("nodes") or []
        if nodes:
            return [node for node in nodes if not node.get("closed")]
    return []
