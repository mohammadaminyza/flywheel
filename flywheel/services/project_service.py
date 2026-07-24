from flywheel.domain.project import ProjectConfig
from flywheel.domain.task import Repository
from flywheel.github.client import GitHubClient

PROJECT_CONFIG_PATHS = (
    ".flywheel/project.yml",
    ".flywheel/project.yaml",
    ".factory/project.yml",
    ".factory/project.yaml",
)


class ProjectService:
    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def load(self, repository: Repository) -> ProjectConfig:
        for path in PROJECT_CONFIG_PATHS:
            raw = self._client.file_contents(repository.owner, repository.name, path)
            if raw:
                return ProjectConfig.parse(raw)
        return ProjectConfig()

    def is_empty(self, repository: Repository) -> bool:
        return self._client.repository_is_empty(repository.owner, repository.name)
