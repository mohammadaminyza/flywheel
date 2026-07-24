import io
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from flywheel.domain.task import Repository
from flywheel.github.client import GitHubClient

SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg"}
PREVIEW_ARTIFACT_NAMES = ("screenshots", "preview", "playwright-report")


class WorkflowRun(BaseModel):
    id: int
    name: str
    status: str
    conclusion: str | None = None
    html_url: str
    head_sha: str

    @property
    def finished(self) -> bool:
        return self.status == "completed"

    @property
    def succeeded(self) -> bool:
        return self.conclusion == "success"


class ActionsService:
    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def runs_for_sha(self, repository: Repository, head_sha: str) -> list[WorkflowRun]:
        payload: dict[str, Any] = self._client.get(
            f"/repos/{repository.full_name}/actions/runs",
            {"head_sha": head_sha, "per_page": 30},
        )
        return [
            WorkflowRun(
                id=item["id"],
                name=item["name"],
                status=item["status"],
                conclusion=item.get("conclusion"),
                html_url=item["html_url"],
                head_sha=item["head_sha"],
            )
            for item in payload.get("workflow_runs", [])
        ]

    def all_finished(self, runs: list[WorkflowRun]) -> bool:
        return bool(runs) and all(run.finished for run in runs)

    def failures(self, runs: list[WorkflowRun]) -> list[WorkflowRun]:
        return [run for run in runs if run.finished and not run.succeeded]

    def artifacts(self, repository: Repository, run_id: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = self._client.get(
            f"/repos/{repository.full_name}/actions/runs/{run_id}/artifacts"
        )
        artifacts: list[dict[str, Any]] = payload.get("artifacts", [])
        return artifacts

    def download_screenshots(
        self, repository: Repository, run_id: int, destination: Path
    ) -> list[Path]:
        destination.mkdir(parents=True, exist_ok=True)
        collected: list[Path] = []
        for artifact in self.artifacts(repository, run_id):
            name = str(artifact.get("name", "")).lower()
            if not any(candidate in name for candidate in PREVIEW_ARTIFACT_NAMES):
                continue
            archive = self._client.get_bytes(
                f"/repos/{repository.full_name}/actions/artifacts/{artifact['id']}/zip"
            )
            if archive is None:
                continue
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                for entry in bundle.namelist():
                    if Path(entry).suffix.lower() not in SCREENSHOT_SUFFIXES:
                        continue
                    target = destination / Path(entry).name
                    target.write_bytes(bundle.read(entry))
                    collected.append(target)
        return collected

    def preview_url_from_deployments(self, repository: Repository, branch: str) -> str | None:
        payload: list[dict[str, Any]] = self._client.get(
            f"/repos/{repository.full_name}/deployments", {"ref": branch, "per_page": 1}
        )
        if not payload:
            return None
        statuses: list[dict[str, Any]] = self._client.get(
            f"/repos/{repository.full_name}/deployments/{payload[0]['id']}/statuses",
            {"per_page": 1},
        )
        if not statuses:
            return None
        url: str | None = statuses[0].get("environment_url")
        return url
