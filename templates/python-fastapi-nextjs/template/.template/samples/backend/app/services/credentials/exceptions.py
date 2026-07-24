from uuid import UUID

from app.common.exceptions import ForbiddenException, NotFoundException, ValidationException


class CredentialNotFoundException(NotFoundException):
    def __init__(self, credential_id: UUID) -> None:
        super().__init__(f"credential {credential_id} was not found")


class CredentialAccessDeniedException(ForbiddenException):
    def __init__(self, credential_id: UUID) -> None:
        super().__init__(f"you do not have access to credential {credential_id}")


class DuplicateCredentialNameException(ValidationException):
    def __init__(self, name: str) -> None:
        super().__init__(f"a credential named '{name}' already exists")


class CredentialNameEmptyException(ValidationException):
    def __init__(self) -> None:
        super().__init__("a credential name cannot be empty")
