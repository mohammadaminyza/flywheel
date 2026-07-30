from flywheel.domain.enums import AgentKind, TaskStatus
from flywheel.domain.task import Repository, Task


def test_task_uses_explicit_workstream_branch() -> None:
    task = Task(
        project_item_id="item",
        issue_number=1,
        issue_node_id="node",
        title="Anything",
        url="https://github.com/acme/app/issues/1",
        repository=Repository(owner="acme", name="app"),
        agent=AgentKind.CODEX,
        status=TaskStatus.TODO,
        branch="feat/client checkout",
    )

    assert task.branch_name == "feat/client-checkout"


def test_preview_template_captures_screenshot_without_deploy_and_normalizes_images() -> None:
    from pathlib import Path

    workflow = Path(
        "templates/python-fastapi-nextjs/template/.github/workflows/preview.yml"
    ).read_text(encoding="utf-8")

    assert "jobs:\n  screenshots:" in workflow
    assert "${GITHUB_REPOSITORY,,}" in workflow
    assert "if: ${{ vars.DOMAIN != '' }}" in workflow
