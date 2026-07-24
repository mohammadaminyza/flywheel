from uuid import UUID

from app.domains.entities.credential import Credential
from app.domains.models.user import User
from app.repositories.credentials import CredentialRepository
from app.security import Claims
from app.services.credentials.exceptions import (
    CredentialAccessDeniedException,
    CredentialNameEmptyException,
    CredentialNotFoundException,
    DuplicateCredentialNameException,
)


class CredentialService:
    def __init__(self, repository: CredentialRepository) -> None:
        self._repository = repository

    def list_for(self, user: User) -> list[Credential]:
        return self._repository.list_visible_to(user.id, user.team_ids)

    def get(self, credential_id: UUID, user: User) -> Credential:
        credential = self._repository.get(credential_id)
        if credential is None or credential.is_deleted:
            raise CredentialNotFoundException(credential_id)
        self._guard_access(credential, user)
        return credential

    def create(self, name: str, provider: str, user: User) -> Credential:
        cleaned = name.strip()
        if not cleaned:
            raise CredentialNameEmptyException()
        if self._repository.exists_with_name(cleaned, user.id):
            raise DuplicateCredentialNameException(cleaned)
        credential = Credential.create(cleaned, provider, user.id)
        self._repository.add(credential)
        return credential

    def rename(self, credential_id: UUID, name: str, user: User) -> Credential:
        cleaned = name.strip()
        if not cleaned:
            raise CredentialNameEmptyException()
        credential = self.get(credential_id, user)
        if self._repository.exists_with_name(cleaned, credential.owner_id):
            raise DuplicateCredentialNameException(cleaned)
        credential.rename(cleaned)
        self._repository.save(credential)
        return credential

    def share(self, credential_id: UUID, team_id: UUID, user: User) -> Credential:
        credential = self.get(credential_id, user)
        credential.share_with_team(team_id)
        self._repository.save(credential)
        return credential

    def delete(self, credential_id: UUID, user: User) -> None:
        credential = self.get(credential_id, user)
        credential.soft_delete()
        self._repository.save(credential)

    def _guard_access(self, credential: Credential, user: User) -> None:
        if credential.owner_id == user.id:
            return
        if user.has_claim(Claims.CREDENTIALS_MANAGE_ALL):
            return
        if set(credential.shared_team_ids) & set(user.team_ids):
            return
        raise CredentialAccessDeniedException(credential.id)
