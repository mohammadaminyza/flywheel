from typing import Any

import yaml
from pydantic import BaseModel, Field

from flywheel.domain.enums import AgentKind


class McpServerSpec(BaseModel):
    enabled: bool = True
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    required: bool = False


class DeploySpec(BaseModel):
    domain: str | None = None
    host: str | None = None
    compose_file: str = "docker-compose.yml"
    registry: str = "ghcr.io"


class ReviewSpec(BaseModel):
    enabled: bool = True
    require_tests: bool = True
    max_attempts: int = 3


class ProjectConfig(BaseModel):
    template: str | None = None
    instructions: str = ""
    rules: list[str] = Field(default_factory=list)
    default_agent: AgentKind | None = None
    mcp: dict[str, McpServerSpec] = Field(default_factory=dict)
    deploy: DeploySpec = Field(default_factory=DeploySpec)
    review: ReviewSpec = Field(default_factory=ReviewSpec)

    @classmethod
    def parse(cls, raw: str) -> "ProjectConfig":
        data: dict[str, Any] = yaml.safe_load(raw) or {}
        return cls(**data)

    def rules_text(self) -> str:
        if not self.rules:
            return ""
        return "\n".join(f"- {rule}" for rule in self.rules)
