from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from flywheel.config import Settings
from flywheel.delivery.telegram import TelegramNotifier
from flywheel.github.actions import ActionsService
from flywheel.github.board import BoardService
from flywheel.github.client import GitHubClient
from flywheel.github.issues import IssueService
from flywheel.github.pulls import PullRequestService
from flywheel.services.clarification_service import ClarificationService
from flywheel.services.delivery_service import DeliveryService
from flywheel.services.dispatcher import Dispatcher
from flywheel.services.exceptions import BoardNotConfiguredException
from flywheel.services.project_service import ProjectService
from flywheel.services.scaffold_service import ScaffoldService
from flywheel.services.task_reporter import TaskReporter
from flywheel.storage import Ledger
from flywheel.workspace import WorkspaceFactory

EventSink = Callable[[str, str, dict[str, Any]], None]


class Container(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    settings: Settings
    ledger: Ledger
    client: GitHubClient
    board: BoardService
    issues: IssueService
    pulls: PullRequestService
    projects: ProjectService
    dispatcher: Dispatcher
    delivery: DeliveryService

    def close(self) -> None:
        self.client.close()
        self.ledger.close()


def build(settings: Settings, on_event: EventSink | None = None) -> Container:
    if not settings.github.configured:
        raise BoardNotConfiguredException()

    client = GitHubClient(settings.github.token)
    ledger = Ledger(settings.database_path)
    board = BoardService(client, settings.github, default_agent=settings.default_agent)
    issues = IssueService(client)
    pulls = PullRequestService(client)
    projects = ProjectService(client)
    workspaces = WorkspaceFactory(settings.workspace_path, settings.github.token)
    clarification = ClarificationService(
        issues, board, timeout_hours=settings.loop.question_timeout_hours
    )
    scaffold = ScaffoldService(settings.template_roots)
    telegram = TelegramNotifier(settings.telegram)
    reporter = TaskReporter(issues, telegram)
    actions = ActionsService(client)
    dispatcher = Dispatcher(
        settings=settings,
        ledger=ledger,
        board=board,
        issues=issues,
        pulls=pulls,
        projects=projects,
        workspaces=workspaces,
        clarification=clarification,
        scaffold=scaffold,
        actions=actions,
        reporter=reporter,
        on_event=on_event,
    )
    delivery = DeliveryService(
        actions,
        pulls,
        board,
        telegram,
        ledger,
        settings.database_path.parent / "artifacts",
    )
    return Container(
        settings=settings,
        ledger=ledger,
        client=client,
        board=board,
        issues=issues,
        pulls=pulls,
        projects=projects,
        dispatcher=dispatcher,
        delivery=delivery,
    )
