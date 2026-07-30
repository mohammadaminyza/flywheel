import shutil
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel

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

SHIPPED_DIRNAME = "template"
GUIDANCE_DIRNAMES = ("guidance", "agent-guidance")
WORKSPACE_GUIDANCE_DIRNAME = ".template"
GIT_EXCLUDE_MARKER = "# flywheel: agent guidance, never committed"


class TemplateSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    path: str
    custom: bool = False
    has_guidance: bool = False


class ScaffoldService:
    """Materialises a template into a workspace.

    A template has two halves that must never be confused:

    * ``template/`` — the project skeleton. These files belong to the product and are
      committed to the repository.
    * ``guidance/`` — architectural rules, reference samples and architecture tests. The
      agent reads them from ``.template/`` inside the workspace, but they are the factory's
      instructions rather than the client's source code, so they stay out of every commit.
    """

    def __init__(self, roots: Path | Sequence[Path]) -> None:
        candidates = [roots] if isinstance(roots, Path) else list(roots)
        seen: set[Path] = set()
        self._roots: list[Path] = []
        for root in candidates:
            resolved = Path(root).expanduser()
            if resolved in seen:
                continue
            seen.add(resolved)
            self._roots.append(resolved)

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def available(self) -> list[str]:
        return sorted({summary.id for summary in self.catalog()})

    def catalog(self) -> list[TemplateSummary]:
        """Every template found across the configured roots, custom roots winning ties.

        Roots are searched in order and the bundled root is always last, so anything found
        before it came from a folder the user pointed the factory at.
        """
        found: dict[str, TemplateSummary] = {}
        for index, root in enumerate(self._roots):
            custom = index < len(self._roots) - 1
            for directory in self._template_directories(root):
                if directory.name in found:
                    continue
                found[directory.name] = self._summary(directory, custom)
        return sorted(found.values(), key=lambda summary: summary.id)

    def _template_directories(self, root: Path) -> list[Path]:
        """The templates a folder holds — or the single template the folder *is*.

        Pointing at `D:\\templates` (a folder of templates) and pointing at
        `D:\\templates\\my-stack` (one template) both do what they look like they do.
        """
        if not root.is_dir():
            return []
        if (root / "template.yml").exists():
            return [root]
        return [child for child in sorted(root.iterdir()) if (child / "template.yml").exists()]

    def _summary(self, directory: Path, custom: bool) -> TemplateSummary:
        manifest = directory / "template.yml"
        return TemplateSummary(
            id=directory.name,
            name=_manifest_value(manifest, "name") or directory.name,
            description=_manifest_value(manifest, "description"),
            path=str(directory),
            custom=custom,
            has_guidance=self._guidance_root(directory) is not None,
        )

    def resolve(self, template_id: str) -> Path:
        for root in self._roots:
            for directory in self._template_directories(root):
                if directory.name == template_id:
                    return directory
        raise TemplateNotFoundException(template_id)

    def apply(
        self, workspace: Path, template_id: str, variables: dict[str, str] | None = None
    ) -> None:
        """Copy the project skeleton into the workspace and stage the agent guidance."""
        directory = self.resolve(template_id)
        source = directory / SHIPPED_DIRNAME
        if not source.is_dir():
            raise TemplateNotFoundException(f"{template_id} (missing its template/ folder)")
        self._copy_tree(source, workspace, variables or {})
        self.apply_guidance(workspace, template_id)

    def apply_guidance(self, workspace: Path, template_id: str) -> bool:
        """Place the agent guidance in the workspace without ever committing it.

        Returns ``False`` when the repository carries its own ``.template/`` folder — a
        project that maintains its own rules always outranks the bundled template.
        """
        target = workspace / WORKSPACE_GUIDANCE_DIRNAME
        if target.exists():
            return False
        try:
            directory = self.resolve(template_id)
        except TemplateNotFoundException:
            return False
        guidance = self._guidance_root(directory)
        if guidance is None:
            return False
        self._copy_tree(guidance, target, {})
        exclude_from_git(workspace, WORKSPACE_GUIDANCE_DIRNAME)
        return True

    def _guidance_root(self, directory: Path) -> Path | None:
        for name in (*GUIDANCE_DIRNAMES, f"{SHIPPED_DIRNAME}/{WORKSPACE_GUIDANCE_DIRNAME}"):
            candidate = directory / name
            if candidate.is_dir():
                return candidate
        return None

    def _copy_tree(self, source: Path, target_root: Path, values: dict[str, str]) -> None:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if _is_skipped(relative.parts):
                continue
            target = target_root / self._render(str(relative), values)
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


def _is_skipped(parts: Iterable[str]) -> bool:
    return any(part in SKIP_DIRECTORIES or part == WORKSPACE_GUIDANCE_DIRNAME for part in parts)


def _manifest_value(path: Path, key: str) -> str:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ""
    if not isinstance(loaded, dict):
        return ""
    value = loaded.get(key)
    return str(value).strip() if value else ""


def exclude_from_git(workspace: Path, name: str) -> None:
    """Ignore a path in this clone only, without adding anything to the repository."""
    info = workspace / ".git" / "info"
    if not (workspace / ".git").is_dir():
        return
    info.mkdir(parents=True, exist_ok=True)
    exclude = info / "exclude"
    entry = f"/{name.strip('/')}/"
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if entry in existing.splitlines():
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    exclude.write_text(
        f"{existing}{prefix}{GIT_EXCLUDE_MARKER}\n{entry}\n",
        encoding="utf-8",
    )
