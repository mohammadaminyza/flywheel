from app.common.responses import BaseResponse


class HealthResponse(BaseResponse):
    status: str
    version: str
    environment: str
