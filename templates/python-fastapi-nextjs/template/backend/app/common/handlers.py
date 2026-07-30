from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.common.exceptions import DomainException


async def handle_domain_exception(_request: Request, error: Exception) -> JSONResponse:
    """Map every domain exception to HTTP in one place, so services never speak HTTP."""
    domain = error if isinstance(error, DomainException) else DomainException(str(error))
    return JSONResponse(
        status_code=domain.status_code,
        content={"detail": domain.message, "type": type(domain).__name__},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainException, handle_domain_exception)
