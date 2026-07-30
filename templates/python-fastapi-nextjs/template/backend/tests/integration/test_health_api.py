from fastapi.testclient import TestClient

from app.config import settings


def test_health_endpoint_answers_through_the_real_route(client: TestClient) -> None:
    response = client.get(f"{settings.api_prefix}/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == settings.version
