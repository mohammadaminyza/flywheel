from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CredentialServiceDep, CurrentUserDep, require_claims
from app.schemas.credential import (
    CreateCredentialRequest,
    CredentialResponse,
    RenameCredentialRequest,
    ShareCredentialRequest,
)
from app.security import Claims

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.get("", response_model=list[CredentialResponse])
def list_credentials(service: CredentialServiceDep, user: CurrentUserDep):
    return CredentialResponse.from_list_model(service.list_for(user))


@router.get("/{credential_id}", response_model=CredentialResponse)
def get_credential(credential_id: UUID, service: CredentialServiceDep, user: CurrentUserDep):
    return CredentialResponse.from_model(service.get(credential_id, user))


@router.post("", response_model=CredentialResponse, status_code=status.HTTP_201_CREATED)
def create_credential(
    request: CreateCredentialRequest,
    service: CredentialServiceDep,
    user: CurrentUserDep,
    _: None = require_claims(Claims.CREDENTIALS_WRITE),
):
    return CredentialResponse.from_model(service.create(request.name, request.provider, user))


@router.patch("/{credential_id}", response_model=CredentialResponse)
def rename_credential(
    credential_id: UUID,
    request: RenameCredentialRequest,
    service: CredentialServiceDep,
    user: CurrentUserDep,
):
    return CredentialResponse.from_model(service.rename(credential_id, request.name, user))


@router.post("/{credential_id}/share", response_model=CredentialResponse)
def share_credential(
    credential_id: UUID,
    request: ShareCredentialRequest,
    service: CredentialServiceDep,
    user: CurrentUserDep,
):
    return CredentialResponse.from_model(service.share(credential_id, request.team_id, user))


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(credential_id: UUID, service: CredentialServiceDep, user: CurrentUserDep):
    service.delete(credential_id, user)
