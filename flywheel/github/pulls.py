from typing import Any

from flywheel.domain.result import AgentResult
from flywheel.domain.task import Repository
from flywheel.github.client import GitHubClient
from flywheel.github.issues import FACTORY_MARKER


class PullRequestService:
    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def open(
        self,
        repository: Repository,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = self._client.post(
            f"/repos/{repository.full_name}/pulls",
            {"title": title, "head": head, "base": base, "body": body, "draft": draft},
        )
        return payload

    def comment(self, repository: Repository, number: int, body: str) -> None:
        self._client.post(
            f"/repos/{repository.full_name}/issues/{number}/comments",
            {"body": f"{FACTORY_MARKER}\n{body}"},
        )

    def find_open(self, repository: Repository, head: str) -> dict[str, Any] | None:
        owner = repository.owner
        payload: list[dict[str, Any]] = self._client.get(
            f"/repos/{repository.full_name}/pulls",
            {"head": f"{owner}:{head}", "state": "open"},
        )
        return payload[0] if payload else None

    def get(self, repository: Repository, number: int) -> dict[str, Any]:
        payload: dict[str, Any] = self._client.get(
            f"/repos/{repository.full_name}/pulls/{number}"
        )
        return payload

    def link_issue(
        self,
        repository: Repository,
        pull: dict[str, Any],
        issue_number: int,
        summary: str,
    ) -> dict[str, Any]:
        marker = f"Closes #{issue_number}"
        body = str(pull.get("body") or "")
        if marker not in body:
            body = body.rstrip() + f"\n\n{marker}"
            pull = self._client.patch(
                f"/repos/{repository.full_name}/pulls/{pull['number']}",
                {"body": body},
            )
        self.comment(
            repository,
            int(pull["number"]),
            f"### Linked task #{issue_number}\n\n{summary or 'Additional work was added.'}",
        )
        return pull

    def body_for(
        self, issue_number: int, result: AgentResult, agent: str, model: str, cost: float
    ) -> str:
        lines = [
            result.summary or "Automated change.",
            "",
            f"Closes #{issue_number}",
            "",
            "### Tests",
        ]
        unit = result.tests_added.unit
        integration = result.tests_added.integration
        if not unit and not integration:
            lines.append("- none reported by the agent")
        for path in unit:
            lines.append(f"- unit: `{path}`")
        for path in integration:
            lines.append(f"- integration: `{path}`")
        if result.follow_ups:
            lines += ["", "### Follow-ups"]
            lines += [f"- {item}" for item in result.follow_ups]
        lines += [
            "",
            "---",
            f"Produced by **{agent}**"
            + (f" ({model})" if model else "")
            + (f" - cost ${cost:.2f}" if cost else ""),
        ]
        return "\n".join(lines)
