from datetime import datetime
from typing import Any

from pydantic import BaseModel

from flywheel.config import GitHubSettings
from flywheel.domain.enums import AgentKind, TaskStatus
from flywheel.domain.task import Repository, Task
from flywheel.github.client import GitHubClient
from flywheel.services.exceptions import BoardNotConfiguredException, ValidationException

_PROJECT_FRAGMENT = """
fragment Project on ProjectV2 {
  id
  title
  fields(first: 50) {
    nodes {
      ... on ProjectV2FieldCommon { id name }
      ... on ProjectV2SingleSelectField { id name options { id name } }
    }
  }
  items(first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      fieldValues(first: 20) {
        nodes {
          ... on ProjectV2ItemFieldSingleSelectValue {
            name
            field { ... on ProjectV2FieldCommon { name } }
          }
          ... on ProjectV2ItemFieldTextValue {
            text
            field { ... on ProjectV2FieldCommon { name } }
          }
        }
      }
      content {
        ... on Issue {
          id
          number
          title
          body
          url
          updatedAt
          repository { name owner { login } }
          labels(first: 20) { nodes { name } }
          assignees(first: 10) { nodes { login } }
        }
      }
    }
  }
}
"""

USER_PROJECT_QUERY = (
    "query($owner: String!, $number: Int!, $cursor: String) {"
    "  user(login: $owner) { projectV2(number: $number) { ...Project } }"
    "}" + _PROJECT_FRAGMENT
)

ORG_PROJECT_QUERY = (
    "query($owner: String!, $number: Int!, $cursor: String) {"
    "  organization(login: $owner) { projectV2(number: $number) { ...Project } }"
    "}" + _PROJECT_FRAGMENT
)

SET_SELECT_FIELD = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(
    input: {projectId: $project, itemId: $item, fieldId: $field,
            value: {singleSelectOptionId: $option}}
  ) { projectV2Item { id } }
}
"""

ADD_PROJECT_ITEM = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) {
    item { id }
  }
}
"""

SET_TEXT_FIELD = """
mutation($project: ID!, $item: ID!, $field: ID!, $value: String!) {
  updateProjectV2ItemFieldValue(
    input: {projectId: $project, itemId: $item, fieldId: $field,
            value: {text: $value}}
  ) { projectV2Item { id } }
}
"""

CREATE_TEXT_FIELD = """
mutation($project: ID!, $name: String!) {
  createProjectV2Field(input: {projectId: $project, dataType: TEXT, name: $name}) {
    projectV2Field { ... on ProjectV2Field { id name } }
  }
}
"""


class BoardField(BaseModel):
    id: str
    name: str
    options: dict[str, str] = {}


class Board(BaseModel):
    id: str
    title: str
    fields: dict[str, BoardField]


STATUS_FALLBACKS: dict[TaskStatus, tuple[TaskStatus, ...]] = {
    TaskStatus.IN_REVIEW: (TaskStatus.IN_PROGRESS,),
    TaskStatus.NEEDS_INFO: (TaskStatus.IN_PROGRESS,),
    TaskStatus.BLOCKED: (TaskStatus.TODO,),
    TaskStatus.DONE: (TaskStatus.IN_PROGRESS,),
}


class BoardService:
    def __init__(
        self,
        client: GitHubClient,
        settings: GitHubSettings,
        default_agent: AgentKind = AgentKind.CLAUDE_CODE,
    ) -> None:
        self._client = client
        self._settings = settings
        self._default_agent = default_agent
        self._board: Board | None = None

    def _project_payload(self, cursor: str | None = None) -> dict[str, Any]:
        if not self._settings.configured:
            raise BoardNotConfiguredException()
        variables = {
            "owner": self._settings.owner,
            "number": self._settings.project_number,
            "cursor": cursor,
        }
        user_data = self._client.graphql(USER_PROJECT_QUERY, variables)
        project = (user_data.get("user") or {}).get("projectV2")
        if project:
            return dict(project)
        org_data = self._client.graphql(ORG_PROJECT_QUERY, variables)
        project = (org_data.get("organization") or {}).get("projectV2")
        if project:
            return dict(project)
        raise BoardNotConfiguredException()

    def load(self) -> Board:
        payload = self._project_payload()
        fields: dict[str, BoardField] = {}
        for node in payload["fields"]["nodes"]:
            if not node:
                continue
            options = {option["name"]: option["id"] for option in node.get("options", [])}
            fields[node["name"]] = BoardField(id=node["id"], name=node["name"], options=options)
        self._board = Board(id=payload["id"], title=payload["title"], fields=fields)
        return self._board

    @property
    def board(self) -> Board:
        if self._board is None:
            return self.load()
        return self._board

    def _field_values(self, item: dict[str, Any]) -> dict[str, str]:
        values: dict[str, str] = {}
        for node in item["fieldValues"]["nodes"]:
            if not node or "field" not in node:
                continue
            name = (node.get("field") or {}).get("name")
            if not name:
                continue
            values[name] = node.get("name") or node.get("text") or ""
        return values

    def _to_task(self, item: dict[str, Any]) -> Task | None:
        content = item.get("content")
        if not content or "number" not in content:
            return None
        values = self._field_values(item)
        status_value = values.get(self._settings.status_field)
        if not status_value:
            return None
        agent_value = values.get(self._settings.agent_field)
        try:
            status = TaskStatus(status_value)
            agent = AgentKind(agent_value.strip().lower()) if agent_value else self._default_agent
        except ValueError:
            return None
        repository = Repository(
            owner=content["repository"]["owner"]["login"], name=content["repository"]["name"]
        )
        return Task(
            project_item_id=item["id"],
            issue_number=content["number"],
            issue_node_id=content["id"],
            title=content["title"],
            body=content.get("body") or "",
            url=content["url"],
            repository=repository,
            agent=agent,
            status=status,
            template_id=values.get(self._settings.template_field) or None,
            branch=values.get(self._settings.branch_field) or None,
            labels=[label["name"] for label in content["labels"]["nodes"]],
            assignees=[user["login"] for user in content["assignees"]["nodes"]],
            updated_at=datetime.fromisoformat(content["updatedAt"].replace("Z", "+00:00")),
        )

    def tasks(self) -> list[Task]:
        collected: list[Task] = []
        cursor: str | None = None
        while True:
            payload = self._project_payload(cursor)
            if self._board is None:
                self.load()
            for item in payload["items"]["nodes"]:
                task = self._to_task(item)
                if task:
                    collected.append(task)
            page = payload["items"]["pageInfo"]
            if not page["hasNextPage"]:
                return collected
            cursor = page["endCursor"]

    def repositories(self) -> list[Repository]:
        """Return the repositories represented by issues on the connected Project."""
        collected: dict[str, Repository] = {}
        cursor: str | None = None
        while True:
            payload = self._project_payload(cursor)
            for item in payload["items"]["nodes"]:
                content = item.get("content") or {}
                repository = content.get("repository") or {}
                owner = (repository.get("owner") or {}).get("login")
                name = repository.get("name")
                if owner and name:
                    value = Repository(owner=owner, name=name)
                    collected[value.full_name.casefold()] = value
            page = payload["items"]["pageInfo"]
            if not page["hasNextPage"]:
                return sorted(collected.values(), key=lambda value: value.full_name.casefold())
            cursor = page["endCursor"]

    def claimable(self) -> list[Task]:
        return [task for task in self.tasks() if task.status == TaskStatus.TODO]

    def _resolve_status_option(self, field: BoardField, status: TaskStatus) -> str | None:
        candidate = field.options.get(status.value)
        if candidate:
            return candidate
        for fallback in STATUS_FALLBACKS.get(status, ()):
            option = field.options.get(fallback.value)
            if option:
                return option
        return None

    def set_status(self, task: Task, status: TaskStatus) -> None:
        self.set_item_status(task.project_item_id, status)

    def set_item_status(self, item_id: str, status: TaskStatus) -> None:
        board = self.board
        field = board.fields.get(self._settings.status_field)
        if field is None:
            raise ValidationException(
                f"board has no '{self._settings.status_field}' field to update"
            )
        option_id = self._resolve_status_option(field, status)
        if option_id is None:
            return
        self._client.graphql(
            SET_SELECT_FIELD,
            {
                "project": board.id,
                "item": item_id,
                "field": field.id,
                "option": option_id,
            },
        )

    def missing_status_options(self) -> list[str]:
        field = self.board.fields.get(self._settings.status_field)
        if field is None:
            return [status.value for status in TaskStatus]
        return [status.value for status in TaskStatus if status.value not in field.options]

    def add_issue(self, issue_node_id: str, branch: str = "") -> str:
        payload = self._client.graphql(
            ADD_PROJECT_ITEM,
            {"project": self.board.id, "content": issue_node_id},
        )
        item_id = str(payload["addProjectV2ItemById"]["item"]["id"])
        if branch.strip():
            self.set_text(item_id, self._settings.branch_field, branch.strip(), create=True)
        return item_id

    def set_text(self, item_id: str, field_name: str, value: str, create: bool = False) -> None:
        field = self.board.fields.get(field_name)
        if field is None and create:
            payload = self._client.graphql(
                CREATE_TEXT_FIELD,
                {"project": self.board.id, "name": field_name},
            )
            created = payload["createProjectV2Field"]["projectV2Field"]
            field = BoardField(id=created["id"], name=created["name"])
            self.board.fields[field_name] = field
        if field is None:
            raise ValidationException(f"board has no '{field_name}' field to update")
        self._client.graphql(
            SET_TEXT_FIELD,
            {
                "project": self.board.id,
                "item": item_id,
                "field": field.id,
                "value": value,
            },
        )
