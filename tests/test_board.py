from typing import Any

import pytest

from flywheel.config import GitHubSettings
from flywheel.domain.enums import AgentKind, TaskStatus
from flywheel.github.board import BoardService


def _item(
    status: str = "Todo",
    agent: str = "claude-code",
    template: str | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    field_values: list[dict[str, Any]] = [
        {"name": status, "field": {"name": "Status"}},
        {"name": agent, "field": {"name": "Agent"}},
    ]
    if template:
        field_values.append({"text": template, "field": {"name": "Template"}})
    return {
        "id": "ITEM_1",
        "fieldValues": {"nodes": field_values},
        "content": {
            "id": "ISSUE_NODE",
            "number": 42,
            "title": "Add a health endpoint",
            "body": "Return the version.",
            "url": "https://github.com/acme/api/issues/42",
            "updatedAt": "2026-07-24T10:00:00Z",
            "repository": {"name": "api", "owner": {"login": "acme"}},
            "labels": {"nodes": [{"name": name} for name in (labels or [])]},
            "assignees": {"nodes": [{"login": "mohammad"}]},
        },
    }


def _payload(items: list[dict[str, Any]], has_next: bool = False) -> dict[str, Any]:
    return {
        "id": "PROJECT_1",
        "title": "Factory Board",
        "fields": {
            "nodes": [
                {
                    "id": "FIELD_STATUS",
                    "name": "Status",
                    "options": [
                        {"id": f"opt-{status.value}", "name": status.value} for status in TaskStatus
                    ],
                },
                {"id": "FIELD_AGENT", "name": "Agent", "options": []},
            ]
        },
        "items": {"pageInfo": {"hasNextPage": has_next, "endCursor": "CUR"}, "nodes": items},
    }


class FakeClient:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls = 0
        self.mutations: list[dict[str, Any]] = []

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        if "mutation" in query:
            self.mutations.append(variables or {})
            return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "ITEM_1"}}}
        page = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        return {"user": {"projectV2": page}, "organization": None}


@pytest.fixture()
def settings() -> GitHubSettings:
    return GitHubSettings(token="t", owner="acme", project_number=1)


def test_maps_board_item_to_task(settings: GitHubSettings) -> None:
    client = FakeClient([_payload([_item()])])
    service = BoardService(client, settings)  # type: ignore[arg-type]

    tasks = service.tasks()

    assert len(tasks) == 1
    task = tasks[0]
    assert task.issue_number == 42
    assert task.agent == AgentKind.CLAUDE_CODE
    assert task.status == TaskStatus.TODO
    assert task.repository.full_name == "acme/api"
    assert task.branch_name == "feat/42-add-a-health-endpoint"


def test_skips_items_without_a_status(settings: GitHubSettings) -> None:
    incomplete = _item()
    incomplete["fieldValues"]["nodes"] = [{"name": "claude-code", "field": {"name": "Agent"}}]
    client = FakeClient([_payload([incomplete])])
    service = BoardService(client, settings)  # type: ignore[arg-type]

    assert service.tasks() == []


def test_missing_agent_field_uses_the_default_agent(settings: GitHubSettings) -> None:
    from flywheel.domain.enums import AgentKind

    no_agent = _item()
    no_agent["fieldValues"]["nodes"] = [{"name": "Todo", "field": {"name": "Status"}}]
    client = FakeClient([_payload([no_agent])])
    service = BoardService(client, settings, default_agent=AgentKind.CODEX)  # type: ignore[arg-type]

    tasks = service.tasks()

    assert len(tasks) == 1
    assert tasks[0].agent == AgentKind.CODEX


def test_skips_draft_items_without_issue_content(settings: GitHubSettings) -> None:
    draft = _item()
    draft["content"] = {}
    client = FakeClient([_payload([draft])])
    service = BoardService(client, settings)  # type: ignore[arg-type]

    assert service.tasks() == []


def test_branch_prefix_follows_labels(settings: GitHubSettings) -> None:
    client = FakeClient([_payload([_item(labels=["bug"])])])
    service = BoardService(client, settings)  # type: ignore[arg-type]

    assert service.tasks()[0].branch_name.startswith("fix/42-")


def test_claimable_filters_to_todo(settings: GitHubSettings) -> None:
    client = FakeClient([_payload([_item(status="In Progress"), _item()])])
    service = BoardService(client, settings)  # type: ignore[arg-type]

    assert len(service.claimable()) == 1


def test_set_status_sends_option_id(settings: GitHubSettings) -> None:
    client = FakeClient([_payload([_item()])])
    service = BoardService(client, settings)  # type: ignore[arg-type]
    task = service.tasks()[0]

    service.set_status(task, TaskStatus.IN_PROGRESS)

    assert client.mutations[-1]["option"] == "opt-In Progress"
    assert client.mutations[-1]["item"] == "ITEM_1"


def test_set_status_falls_back_when_exact_option_missing(settings: GitHubSettings) -> None:
    payload = _payload([_item()])
    payload["fields"]["nodes"][0]["options"] = [
        {"id": "opt-Todo", "name": "Todo"},
        {"id": "opt-In Progress", "name": "In Progress"},
    ]
    client = FakeClient([payload])
    service = BoardService(client, settings)  # type: ignore[arg-type]
    task = service.tasks()[0]

    service.set_status(task, TaskStatus.IN_REVIEW)

    assert client.mutations[-1]["option"] == "opt-In Progress"


def test_set_status_is_a_noop_when_no_option_or_fallback_exists(settings: GitHubSettings) -> None:
    payload = _payload([_item()])
    payload["fields"]["nodes"][0]["options"] = [{"id": "opt-Todo", "name": "Todo"}]
    client = FakeClient([payload])
    service = BoardService(client, settings)  # type: ignore[arg-type]
    task = service.tasks()[0]

    service.set_status(task, TaskStatus.DONE)

    assert client.mutations == []


def test_missing_status_options_reports_gaps(settings: GitHubSettings) -> None:
    payload = _payload([_item()])
    payload["fields"]["nodes"][0]["options"] = [{"id": "opt-Todo", "name": "Todo"}]
    client = FakeClient([payload])
    service = BoardService(client, settings)  # type: ignore[arg-type]

    missing = service.missing_status_options()

    assert "Needs Info" in missing
    assert "Todo" not in missing
