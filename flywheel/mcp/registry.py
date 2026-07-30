import json
import os
import re
from pathlib import Path
from typing import Any

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


def _codex_entry(spec: McpServerSpec) -> dict[str, Any]:
    """One server in Codex's own vocabulary.

    A remote server carries its secrets in `http_headers`; Codex refuses to load a
    streamable-HTTP server that was given `env`, and silently ignores `headers`.
    """
    if spec.url:
        entry: dict[str, Any] = {"url": spec.url}
        if spec.env:
            entry["http_headers"] = dict(spec.env)
        return entry
    entry = {"command": spec.command or "", "args": list(spec.args)}
    if spec.env:
        entry["env"] = dict(spec.env)
    return entry


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
        self._servers = {
            name: self._resolve(spec) for name, spec in merged.items() if spec.enabled
        }

    def _resolve(self, spec: McpServerSpec) -> McpServerSpec:
        return McpServerSpec(
            enabled=spec.enabled,
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

    def codex_overrides(self) -> list[str]:
        """The `-c` arguments that actually register these servers with Codex.

        Codex only ever reads `$CODEX_HOME/config.toml`; a config file handed to it by path
        is accepted and then ignored, which silently left Codex with no MCP servers at all.
        Passing each server as a `mcp_servers.<name>` override is what Codex honours, and it
        keeps the agent's own home directory — its login and its resumable sessions —
        untouched.
        """
        overrides: list[str] = []
        for name, spec in self._servers.items():
            entry = tomlkit.inline_table()
            for key, value in _codex_entry(spec).items():
                if isinstance(value, dict):
                    nested = tomlkit.inline_table()
                    nested.update(value)
                    entry[key] = nested
                else:
                    entry[key] = value
            overrides.append(f"mcp_servers.{name}={entry.as_string()}")
        return overrides

    def codex_config(self) -> str:
        document = tomlkit.document()
        table = tomlkit.table(is_super_table=True)
        for name, spec in self._servers.items():
            entry = tomlkit.table()
            for key, value in _codex_entry(spec).items():
                entry[key] = value
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
