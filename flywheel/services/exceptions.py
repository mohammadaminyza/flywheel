class DomainException(Exception):
    pass


class NotFoundException(DomainException):
    pass


class ValidationException(DomainException):
    pass


class ConfigurationException(DomainException):
    pass


class TaskNotClaimableException(DomainException):
    def __init__(self, project_item_id: str) -> None:
        super().__init__(f"task {project_item_id} is already claimed by another run")


class RunNotFoundException(NotFoundException):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id} was not found")


class TemplateNotFoundException(NotFoundException):
    def __init__(self, template_id: str) -> None:
        super().__init__(f"template '{template_id}' was not found in the templates directory")


class BoardNotConfiguredException(ConfigurationException):
    def __init__(self) -> None:
        super().__init__(
            "no GitHub project board is configured; run the setup wizard to connect one"
        )


class AgentNotAvailableException(ConfigurationException):
    def __init__(self, agent: str, reason: str) -> None:
        super().__init__(f"agent '{agent}' is not available: {reason}")


class AgentRunFailedException(DomainException):
    def __init__(self, agent: str, exit_code: int, detail: str) -> None:
        super().__init__(f"agent '{agent}' failed with exit code {exit_code}: {detail}")


class AgentResultUnparsableException(DomainException):
    def __init__(self, agent: str) -> None:
        super().__init__(f"agent '{agent}' did not return a parsable structured result")


class MaxAttemptsReachedException(DomainException):
    def __init__(self, issue_number: int, attempts: int) -> None:
        super().__init__(f"issue #{issue_number} failed after {attempts} attempts")


class WorkspaceException(DomainException):
    def __init__(self, detail: str) -> None:
        super().__init__(f"workspace error: {detail}")


class McpConnectionException(DomainException):
    def __init__(self, server: str, detail: str) -> None:
        super().__init__(f"could not connect to MCP server '{server}': {detail}")
