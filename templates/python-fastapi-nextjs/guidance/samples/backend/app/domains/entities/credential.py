from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field

from app.common.entity import AggregateRoot


class Credential(AggregateRoot):
    id: UUID = Field(default_factory=uuid4)
    name: str
    provider: str
    owner_id: UUID
    shared_team_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deleted_at: datetime | None = None

    @classmethod
    def create(cls, name: str, provider: str, owner_id: UUID) -> "Credential":
        return cls(name=name, provider=provider, owner_id=owner_id)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def rename(self, name: str) -> None:
        self.name = name
        self._touch()

    def share_with_team(self, team_id: UUID) -> None:
        if team_id not in self.shared_team_ids:
            self.shared_team_ids.append(team_id)
            self._touch()

    def revoke_team(self, team_id: UUID) -> None:
        if team_id in self.shared_team_ids:
            self.shared_team_ids.remove(team_id)
            self._touch()

    def soft_delete(self) -> None:
        self.deleted_at = datetime.now(UTC)
        self._touch()

    def _touch(self) -> None:
        self.updated_at = datetime.now(UTC)
