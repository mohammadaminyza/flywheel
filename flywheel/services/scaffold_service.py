import shutil
from pathlib import Path

from flywheel.services.exceptions import TemplateNotFoundException

SUBSTITUTABLE_SUFFIXES = {
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".cs",
    ".csproj",
    ".sln",
    ".txt",
    ".env",
    ".sample",
    ".sh",
    ".ps1",
    ".cfg",
    ".ini",
}
SKIP_DIRECTORIES = {".git", "node_modules", "__pycache__", ".venv", ".ruff_cache"}


class ScaffoldService:
    def __init__(self, templates_dir: Path) -> None:
        self._templates_dir = templates_dir

    def available(self) -> list[str]:
        if not self._templates_dir.is_dir():
            return []
        return sorted(
            child.name
            for child in self._templates_dir.iterdir()
            if (child / "template.yml").exists()
        )

    def resolve(self, template_id: str) -> Path:
        directory = self._templates_dir / template_id
        if not (directory / "template.yml").exists():
            raise TemplateNotFoundException(template_id)
        return directory

    def apply(
        self, workspace: Path, template_id: str, variables: dict[str, str] | None = None
    ) -> None:
        source = self.resolve(template_id) / "template"
        if not source.is_dir():
            raise TemplateNotFoundException(f"{template_id} (missing its template/ folder)")
        values = variables or {}
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if any(part in SKIP_DIRECTORIES for part in relative.parts):
                continue
            target = workspace / self._render(str(relative), values)
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix in SUBSTITUTABLE_SUFFIXES:
                text = path.read_text(encoding="utf-8", errors="replace")
                target.write_text(self._render(text, values), encoding="utf-8")
            else:
                shutil.copy2(path, target)

    def _render(self, text: str, values: dict[str, str]) -> str:
        for key, value in values.items():
            text = text.replace(f"{{{{{key}}}}}", value)
        return text
