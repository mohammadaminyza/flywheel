from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.entities.credential import Credential
from app.domains.orm.credential import CredentialRow


class CredentialRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, credential_id: UUID) -> Credential | None:
        row = self._session.get(CredentialRow, credential_id)
        return row.to_entity() if row else None

    def list_visible_to(self, owner_id: UUID, team_ids: list[UUID]) -> list[Credential]:
        statement = select(CredentialRow).where(
            CredentialRow.deleted_at.is_(None),
            (CredentialRow.owner_id == owner_id)
            | (CredentialRow.shared_team_ids.overlap(team_ids)),
        )
        return [row.to_entity() for row in self._session.scalars(statement)]

    def exists_with_name(self, name: str, owner_id: UUID) -> bool:
        statement = select(CredentialRow.id).where(
            CredentialRow.name == name,
            CredentialRow.owner_id == owner_id,
            CredentialRow.deleted_at.is_(None),
        )
        return self._session.scalar(statement) is not None

    def add(self, credential: Credential) -> None:
        self._session.add(CredentialRow.from_entity(credential))
        self._session.flush()

    def save(self, credential: Credential) -> None:
        row = self._session.get(CredentialRow, credential.id)
        row.apply(credential)
        self._session.flush()

    def remove(self, credential: Credential) -> None:
        row = self._session.get(CredentialRow, credential.id)
        self._session.delete(row)
        self._session.flush()
