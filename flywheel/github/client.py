from typing import Any

import httpx

from flywheel.services.exceptions import ValidationException

API_ROOT = "https://api.github.com"


class GitHubError(ValidationException):
    def __init__(self, detail: str) -> None:
        super().__init__(f"GitHub API error: {detail}")


class GitHubClient:
    def __init__(self, token: str, timeout: int = 30) -> None:
        self._client = httpx.Client(
            base_url=API_ROOT,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self) -> None:
        self._client.close()

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client.post(
            "/graphql", json={"query": query, "variables": variables or {}}
        )
        if response.status_code != 200:
            raise GitHubError(f"graphql returned {response.status_code}: {response.text[:200]}")
        payload = response.json()
        if payload.get("errors"):
            messages = "; ".join(error.get("message", "?") for error in payload["errors"])
            raise GitHubError(messages)
        data: dict[str, Any] = payload.get("data") or {}
        return data

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._client.get(path, params=params)
        if response.status_code >= 400:
            raise GitHubError(f"GET {path} returned {response.status_code}: {response.text[:200]}")
        return response.json()

    def post(self, path: str, body: dict[str, Any]) -> Any:
        response = self._client.post(path, json=body)
        if response.status_code >= 400:
            raise GitHubError(f"POST {path} returned {response.status_code}: {response.text[:200]}")
        return response.json()

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        response = self._client.patch(path, json=body)
        if response.status_code >= 400:
            raise GitHubError(
                f"PATCH {path} returned {response.status_code}: {response.text[:200]}"
            )
        return response.json()

    def get_bytes(self, path: str) -> bytes | None:
        response = self._client.get(path, follow_redirects=True)
        if response.status_code >= 400:
            return None
        return response.content

    def repository_is_empty(self, owner: str, name: str) -> bool:
        response = self._client.get(f"/repos/{owner}/{name}/commits", params={"per_page": 1})
        if response.status_code == 409:
            return True
        if response.status_code >= 400:
            raise GitHubError(
                f"could not inspect {owner}/{name}: {response.status_code} {response.text[:120]}"
            )
        return len(response.json()) == 0

    def file_contents(self, owner: str, name: str, path: str, ref: str | None = None) -> str | None:
        params = {"ref": ref} if ref else None
        response = self._client.get(
            f"/repos/{owner}/{name}/contents/{path}",
            params=params,
            headers={"Accept": "application/vnd.github.raw"},
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GitHubError(f"could not read {path}: {response.status_code}")
        return response.text

    def default_branch(self, owner: str, name: str) -> str:
        data = self.get(f"/repos/{owner}/{name}")
        branch: str = data.get("default_branch") or "main"
        return branch
