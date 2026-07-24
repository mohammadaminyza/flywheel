from uuid import uuid4

import pytest

from app.domains.entities.credential import Credential
from app.domains.models.user import User
from app.security import Claims
from app.services.credentials.exceptions import (
    CredentialAccessDeniedException,
    CredentialNameEmptyException,
    CredentialNotFoundException,
    DuplicateCredentialNameException,
)
from app.services.credentials.service import CredentialService


class InMemoryCredentialRepository:
    def __init__(self, credentials: list[Credential] | None = None) -> None:
        self.credentials = {item.id: item for item in credentials or []}
        self.saved: list[Credential] = []

    def get(self, credential_id):
        return self.credentials.get(credential_id)

    def list_visible_to(self, owner_id, team_ids):
        return [
            item
            for item in self.credentials.values()
            if not item.is_deleted
            and (item.owner_id == owner_id or set(item.shared_team_ids) & set(team_ids))
        ]

    def exists_with_name(self, name, owner_id):
        return any(
            item.name == name and item.owner_id == owner_id and not item.is_deleted
            for item in self.credentials.values()
        )

    def add(self, credential):
        self.credentials[credential.id] = credential

    def save(self, credential):
        self.credentials[credential.id] = credential
        self.saved.append(credential)


@pytest.fixture()
def user() -> User:
    return User(id=uuid4(), team_ids=[], claims=[Claims.CREDENTIALS_WRITE])


def test_create_stores_a_credential(user: User) -> None:
    repository = InMemoryCredentialRepository()
    service = CredentialService(repository)

    credential = service.create("  Nexus  ", "docker", user)

    assert credential.name == "Nexus"
    assert credential.owner_id == user.id
    assert repository.credentials[credential.id] is credential


def test_create_rejects_an_empty_name(user: User) -> None:
    service = CredentialService(InMemoryCredentialRepository())

    with pytest.raises(CredentialNameEmptyException):
        service.create("   ", "docker", user)


def test_create_rejects_a_duplicate_name(user: User) -> None:
    existing = Credential.create("Nexus", "docker", user.id)
    service = CredentialService(InMemoryCredentialRepository([existing]))

    with pytest.raises(DuplicateCredentialNameException):
        service.create("Nexus", "docker", user)


def test_get_hides_a_soft_deleted_credential(user: User) -> None:
    credential = Credential.create("Nexus", "docker", user.id)
    credential.soft_delete()
    service = CredentialService(InMemoryCredentialRepository([credential]))

    with pytest.raises(CredentialNotFoundException):
        service.get(credential.id, user)


def test_another_user_cannot_read_a_credential(user: User) -> None:
    credential = Credential.create("Nexus", "docker", uuid4())
    service = CredentialService(InMemoryCredentialRepository([credential]))

    with pytest.raises(CredentialAccessDeniedException):
        service.get(credential.id, user)


def test_a_shared_team_grants_access() -> None:
    team_id = uuid4()
    owner = User(id=uuid4(), team_ids=[], claims=[])
    member = User(id=uuid4(), team_ids=[team_id], claims=[])
    credential = Credential.create("Nexus", "docker", owner.id)
    credential.share_with_team(team_id)
    service = CredentialService(InMemoryCredentialRepository([credential]))

    assert service.get(credential.id, member) is credential


def test_rename_persists_through_the_aggregate(user: User) -> None:
    credential = Credential.create("Nexus", "docker", user.id)
    repository = InMemoryCredentialRepository([credential])
    service = CredentialService(repository)

    renamed = service.rename(credential.id, "Nexus Prod", user)

    assert renamed.name == "Nexus Prod"
    assert repository.saved == [credential]


def test_delete_soft_deletes(user: User) -> None:
    credential = Credential.create("Nexus", "docker", user.id)
    service = CredentialService(InMemoryCredentialRepository([credential]))

    service.delete(credential.id, user)

    assert credential.is_deleted
