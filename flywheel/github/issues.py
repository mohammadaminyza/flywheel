from datetime import datetime
from typing import Any

from flywheel.domain.result import AgentQuestion
from flywheel.domain.task import Comment, Repository
from flywheel.github.client import GitHubClient

FACTORY_MARKER = "<!-- flywheel -->"
QUESTION_MARKER = "<!-- factory:question -->"


class IssueService:
    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def comment(self, repository: Repository, issue_number: int, body: str) -> str:
        payload: dict[str, Any] = self._client.post(
            f"/repos/{repository.full_name}/issues/{issue_number}/comments",
            {"body": f"{FACTORY_MARKER}\n{body}"},
        )
        return str(payload["id"])

    def ask(
        self,
        repository: Repository,
        issue_number: int,
        questions: list[AgentQuestion],
        agent: str,
        mention: list[str],
    ) -> str:
        mentions = " ".join(f"@{name}" for name in mention)
        lines = [
            QUESTION_MARKER,
            f"**{agent} needs a decision before it can continue.** {mentions}".strip(),
            "",
        ]
        for index, question in enumerate(questions, start=1):
            lines.append(f"**{index}. {question.question}**")
            if question.why:
                lines.append(f"> {question.why}")
            for option in question.options:
                lines.append(f"- {option}")
            lines.append("")
        lines.append("_Reply in a comment below and the agent will resume with your answer._")
        payload: dict[str, Any] = self._client.post(
            f"/repos/{repository.full_name}/issues/{issue_number}/comments",
            {"body": f"{FACTORY_MARKER}\n" + "\n".join(lines)},
        )
        return str(payload["id"])

    def comments(self, repository: Repository, issue_number: int) -> list[Comment]:
        payload: list[dict[str, Any]] = self._client.get(
            f"/repos/{repository.full_name}/issues/{issue_number}/comments",
            {"per_page": 100},
        )
        collected: list[Comment] = []
        for item in payload:
            body = item.get("body") or ""
            collected.append(
                Comment(
                    id=str(item["id"]),
                    author=item["user"]["login"],
                    body=body,
                    created_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")),
                    is_bot=FACTORY_MARKER in body or item["user"].get("type") == "Bot",
                )
            )
        return collected

    def human_reply_after(
        self, repository: Repository, issue_number: int, comment_id: str
    ) -> Comment | None:
        comments = self.comments(repository, issue_number)
        seen_question = False
        for comment in comments:
            if comment.id == comment_id:
                seen_question = True
                continue
            if seen_question and not comment.is_bot:
                return comment
        return None
