from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from flywheel.domain.enums import AgentKind, RunPhase, RunStatus, Transport
from flywheel.domain.project import ProjectConfig
from flywheel.domain.result import AgentResult
from flywheel.domain.task import Task
from flywheel.domain.template import TemplateManifest


class RunSpec(BaseModel):
    run_id: str
    task: Task
    project: ProjectConfig
    template: TemplateManifest
    workspace: Path
    prompt: str
    system_prompt: str
    agent: AgentKind
    transport: Transport
    resume_session_id: str | None = None
    max_turns: int = 120
    timeout_seconds: int = 5400


class RunOutcome(BaseModel):
    result: AgentResult | None = None
    session_id: str | None = None
    cost_usd: float = 0.0
    transport_used: Transport = Transport.CLI
    exit_code: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error is None


class Run(BaseModel):
    id: str
    project_item_id: str
    issue_number: int
    repository: str
    agent: AgentKind
    status: RunStatus = RunStatus.PENDING
    phase: RunPhase = RunPhase.CLAIMING
    attempt: int = 1
    branch: str | None = None
    session_id: str | None = None
    pull_request_number: int | None = None
    preview_url: str | None = None
    cost_usd: float = 0.0
    error: str | None = None
    pending_question_comment_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)

    def advance(self, phase: RunPhase) -> None:
        self.phase = phase
        self.touch()

    def mark_running(self) -> None:
        self.status = RunStatus.RUNNING
        self.touch()

    def mark_awaiting_answer(self, comment_id: str) -> None:
        self.status = RunStatus.NEEDS_INPUT
        self.pending_question_comment_id = comment_id
        self.touch()

    def mark_succeeded(self, pull_request_number: int) -> None:
        self.status = RunStatus.SUCCEEDED
        self.phase = RunPhase.FINISHED
        self.pull_request_number = pull_request_number
        self.touch()
        self.error = None

    def mark_failed(self, error: str) -> None:
        self.status = RunStatus.FAILED
        self.phase = RunPhase.FINISHED
        self.error = error
        self.touch()

    def record_cost(self, amount: float) -> None:
        self.cost_usd += amount
        self.touch()
