from fastapi import APIRouter

from app.api.deps import HealthServiceDep
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def read_health(service: HealthServiceDep) -> HealthResponse:
    return HealthResponse.from_model(service.status())
