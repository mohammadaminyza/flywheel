from pydantic import BaseModel


class HealthStatus(BaseModel):
    """What the service reports about the running application."""

    status: str
    version: str
    environment: str
