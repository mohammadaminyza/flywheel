from datetime import datetime

from pydantic import BaseModel, Field

from flywheel.domain.enums import AgentKind, TaskStatus


class Repository(BaseModel):
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.full_name}.git"


class Comment(BaseModel):
    id: str
    author: str
    body: str
    created_at: datetime
    is_bot: bool = False


class Task(BaseModel):
    project_item_id: str
    issue_number: int
    issue_node_id: str
    title: str
    body: str = ""
    url: str
    repository: Repository
    agent: AgentKind
    status: TaskStatus
    template_id: str | None = None
    branch: str | None = None
    labels: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None

    @property
    def slug(self) -> str:
        cleaned = "".join(c if (c.isascii() and c.isalnum()) else "-" for c in self.title.lower())
        collapsed = "-".join(part for part in cleaned.split("-") if part)
        return collapsed[:50].strip("-")

    @property
    def branch_prefix(self) -> str:
        lowered = {label.lower() for label in self.labels}
        if lowered & {"bug", "fix", "defect"}:
            return "fix"
        if lowered & {"chore", "refactor", "docs"}:
            return "chore"
        return "feat"

    @property
    def branch_name(self) -> str:
        if self.branch:
            normalized = "".join(
                character
                if character.isascii() and (character.isalnum() or character in "._/-")
                else "-"
                for character in self.branch.strip()
            )
            normalized = "/".join(part.strip(".-") for part in normalized.split("/") if part)
            if normalized:
                return normalized[:120]
        if self.slug:
            return f"{self.branch_prefix}/{self.issue_number}-{self.slug}"
        return f"{self.branch_prefix}/issue-{self.issue_number}"
