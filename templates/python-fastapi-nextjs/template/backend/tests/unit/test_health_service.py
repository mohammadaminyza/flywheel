from app.services.health.service import HealthService


def test_reports_the_running_version_and_environment() -> None:
    service = HealthService(version="1.2.3", environment="stage")

    status = service.status()

    assert status.status == "ok"
    assert status.version == "1.2.3"
    assert status.environment == "stage"
