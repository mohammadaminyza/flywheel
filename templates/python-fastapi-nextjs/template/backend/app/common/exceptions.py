class DomainException(Exception):
    """Base class for every business failure.

    Build the message inside the subclass' ``__init__`` and raise it bare. Never assemble
    a message at the call site, and never raise ``HTTPException`` for a business rule.
    """

    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundException(DomainException):
    status_code = 404


class ConflictException(DomainException):
    status_code = 409


class PermissionDeniedException(DomainException):
    status_code = 403
