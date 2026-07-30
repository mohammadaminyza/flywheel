from typing import Annotated

from fastapi import Depends

from app.config import settings
from app.services.health.service import HealthService


def health_service() -> HealthService:
    return HealthService(version=settings.version, environment=settings.environment)


HealthServiceDep = Annotated[HealthService, Depends(health_service)]
