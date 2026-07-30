from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def created(client: TestClient, auth_headers: dict[str, str]) -> dict:
    response = client.post(
        "/api/v1/credentials",
        json={"name": "Nexus", "provider": "docker"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    return response.json()


def test_create_returns_the_credential(created: dict) -> None:
    assert created["name"] == "Nexus"
    assert created["provider"] == "docker"
    assert created["shared_team_ids"] == []


def test_list_includes_the_created_credential(
    client: TestClient, auth_headers: dict[str, str], created: dict
) -> None:
    response = client.get("/api/v1/credentials", headers=auth_headers)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [created["id"]]


def test_duplicate_name_is_rejected(
    client: TestClient, auth_headers: dict[str, str], created: dict
) -> None:
    response = client.post(
        "/api/v1/credentials",
        json={"name": "Nexus", "provider": "docker"},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_rename_updates_the_credential(
    client: TestClient, auth_headers: dict[str, str], created: dict
) -> None:
    response = client.patch(
        f"/api/v1/credentials/{created['id']}",
        json={"name": "Nexus Prod"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Nexus Prod"


def test_unknown_credential_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(f"/api/v1/credentials/{uuid4()}", headers=auth_headers)

    assert response.status_code == 404


def test_delete_removes_it_from_the_list(
    client: TestClient, auth_headers: dict[str, str], created: dict
) -> None:
    assert (
        client.delete(f"/api/v1/credentials/{created['id']}", headers=auth_headers).status_code
        == 204
    )

    remaining = client.get("/api/v1/credentials", headers=auth_headers).json()
    assert remaining == []


def test_anonymous_access_is_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/credentials").status_code == 401
