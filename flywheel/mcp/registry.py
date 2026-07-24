import json
import os
import re
from pathlib import Path

import tomlkit

from flywheel.domain.project import McpServerSpec

PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")

GITHUB_TOOLSETS = "context,repos,issues,pull_requests,actions,labels"


def resolve_placeholders(value: str, secrets: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return secrets.get(key) or os.environ.get(key, "")

    return PLACEHOLDER.sub(replace, value)


def default_servers(repository: str, github_token: str) -> dict[str, McpServerSpec]:
    return {
        "github": McpServerSpec(
            command="github-mcp-server",
            args=["stdio"],
            env={
                "GITHUB_PERSONAL_ACCESS_TOKEN": github_token,
                "GITHUB_TOOLSETS": GITHUB_TOOLSETS,
                "GITHUB_REPOSITORY": repository,
            },
            required=True,
        ),
        "playwright": McpServerSpec(
            command="npx",
            args=["-y", "@playwright/mcp@latest", "--headless", "--isolated"],
        ),
    }


class McpRegistry:
    def __init__(
        self,
        repository: str,
        github_token: str,
        project_servers: dict[str, McpServerSpec] | None = None,
        secrets: dict[str, str] | None = None,
        include_defaults: bool = True,
    ) -> None:
        self._secrets = secrets or {}
        merged: dict[str, McpServerSpec] = {}
        if include_defaults:
            merged.update(default_servers(repository, github_token))
        merged.update(project_servers or {})
        self._servers = {name: self._resolve(spec) for name, spec in merged.items()}

    def _resolve(self, spec: McpServerSpec) -> McpServerSpec:
        return McpServerSpec(
            command=spec.command,
            args=[resolve_placeholders(arg, self._secrets) for arg in spec.args],
            env={
                key: resolve_placeholders(value, self._secrets) for key, value in spec.env.items()
            },
            url=resolve_placeholders(spec.url, self._secrets) if spec.url else None,
            required=spec.required,
        )

    @property
    def servers(self) -> dict[str, McpServerSpec]:
        return self._servers

    def claude_config(self) -> dict[str, object]:
        servers: dict[str, object] = {}
        for name, spec in self._servers.items():
            if spec.url:
                servers[name] = {"type": "http", "url": spec.url, "headers": spec.env}
                continue
            servers[name] = {"command": spec.command, "args": spec.args, "env": spec.env}
        return {"mcpServers": servers}

    def codex_config(self) -> str:
        document = tomlkit.document()
        table = tomlkit.table(is_super_table=True)
        for name, spec in self._servers.items():
            entry = tomlkit.table()
            if spec.url:
                entry["url"] = spec.url
            else:
                entry["command"] = spec.command or ""
                entry["args"] = spec.args
            if spec.env:
                entry["env"] = spec.env
            if spec.required:
                entry["required"] = True
            table[name] = entry
        document["mcp_servers"] = table
        return tomlkit.dumps(document)

    def write_claude(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.claude_config(), indent=2), encoding="utf-8")
        return path

    def write_codex(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.codex_config(), encoding="utf-8")
        return path
