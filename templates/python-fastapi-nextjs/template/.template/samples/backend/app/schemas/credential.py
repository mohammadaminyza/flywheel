from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.common.serializers import BaseResponse, BaseSerializer


class CreateCredentialRequest(BaseSerializer):
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=60)


class RenameCredentialRequest(BaseSerializer):
    name: str = Field(min_length=1, max_length=120)


class ShareCredentialRequest(BaseSerializer):
    team_id: UUID


class CredentialResponse(BaseResponse):
    id: UUID
    name: str
    provider: str
    owner_id: UUID
    shared_team_ids: list[UUID]
    created_at: datetime
    updated_at: datetime
