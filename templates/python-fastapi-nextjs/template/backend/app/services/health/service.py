from app.domains.models.health import HealthStatus


class HealthService:
    """The smallest complete example of the layering: a use case that owns its logic."""

    def __init__(self, version: str, environment: str) -> None:
        self._version = version
        self._environment = environment

    def status(self) -> HealthStatus:
        return HealthStatus(status="ok", version=self._version, environment=self._environment)
