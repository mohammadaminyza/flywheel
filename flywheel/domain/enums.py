from enum import StrEnum


class AgentKind(StrEnum):
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"


class Transport(StrEnum):
    CLI = "cli"
    MCP = "mcp"


class AuthMode(StrEnum):
    SUBSCRIPTION = "subscription"
    API_KEY = "api_key"


class ExecutionMode(StrEnum):
    CONTAINER = "container"
    HOST = "host"


class TaskStatus(StrEnum):
    TODO = "Todo"
    IN_PROGRESS = "In Progress"
    IN_REVIEW = "In Review"
    NEEDS_INFO = "Needs Info"
    BLOCKED = "Blocked"
    DONE = "Done"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    REVIEWING = "reviewing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunPhase(StrEnum):
    CLAIMING = "claiming"
    PREPARING = "preparing"
    SCAFFOLDING = "scaffolding"
    IMPLEMENTING = "implementing"
    REVIEWING = "reviewing"
    PUSHING = "pushing"
    OPENING_PR = "opening_pr"
    AWAITING_CI = "awaiting_ci"
    DELIVERING = "delivering"
    FINISHED = "finished"


class Environment(StrEnum):
    DEV = "dev"
    STAGE = "stage"
    PROD = "prod"
