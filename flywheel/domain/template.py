from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from flywheel.domain.enums import Environment

TEMPLATE_DIRNAME = ".template"
SAMPLE_DIRNAMES = ("samples", "sample", "structure")
ARCHITECTURE_TEST_DIRNAMES = ("architecture-tests", "arch-tests", "architecture")
RULES_FILENAMES = ("README.md", "ARCHITECTURE.md", "RULES.md", "CLAUDE.md", "AGENTS.md")
SAMPLE_EXCERPT_SUFFIXES = {
    ".py",
    ".cs",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".java",
    ".rb",
    ".rs",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".sql",
    ".md",
}


class CommandSet(BaseModel):
    backend: str | None = None
    frontend: str | None = None

    def all(self) -> list[str]:
        return [command for command in (self.backend, self.frontend) if command]


class TemplateCommands(BaseModel):
    install: CommandSet = Field(default_factory=CommandSet)
    lint: CommandSet = Field(default_factory=CommandSet)
    typecheck: CommandSet = Field(default_factory=CommandSet)
    test_unit: CommandSet = Field(default_factory=CommandSet)
    test_integration: CommandSet = Field(default_factory=CommandSet)
    test_architecture: CommandSet = Field(default_factory=CommandSet)
    build: CommandSet = Field(default_factory=CommandSet)


class TemplateStack(BaseModel):
    backend: str | None = None
    frontend: str | None = None


class TemplatePorts(BaseModel):
    backend: int = 80
    frontend: int = 3000


class EnvironmentPorts(BaseModel):
    backend: int
    frontend: int


class TemplateManifest(BaseModel):
    id: str = "repository"
    name: str = "Repository template"
    description: str = ""
    stack: TemplateStack = Field(default_factory=TemplateStack)
    commands: TemplateCommands = Field(default_factory=TemplateCommands)
    ports: TemplatePorts = Field(default_factory=TemplatePorts)
    health: str = "/health"
    envs: dict[Environment, EnvironmentPorts] = Field(default_factory=dict)
    root: Path | None = None
    rules: str = ""
    sample_tree: str = ""
    sample_excerpts: str = ""

    @property
    def has_architecture_tests(self) -> bool:
        return bool(self.commands.test_architecture.all())

    @property
    def found(self) -> bool:
        return self.root is not None


def _read_manifest(directory: Path) -> dict[str, Any]:
    for name in ("template.yml", "template.yaml"):
        path = directory / name
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
    return {}


def _read_rules(directory: Path) -> str:
    sections: list[str] = []
    for name in RULES_FILENAMES:
        path = directory / name
        if path.exists():
            sections.append(f"### {TEMPLATE_DIRNAME}/{name}\n\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(sections)


def _sample_roots(directory: Path) -> list[Path]:
    roots = [directory / name for name in SAMPLE_DIRNAMES]
    found = [path for path in roots if path.is_dir()]
    return found


def _render_tree(root: Path, limit: int = 300) -> str:
    entries: list[str] = []
    for path in sorted(root.rglob("*")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        relative = path.relative_to(root)
        depth = len(relative.parts) - 1
        marker = "/" if path.is_dir() else ""
        entries.append(f"{'  ' * depth}{relative.name}{marker}")
        if len(entries) >= limit:
            entries.append("... (truncated)")
            break
    return "\n".join(entries)


def _render_excerpts(root: Path, max_files: int = 12, max_bytes: int = 4000) -> str:
    blocks: list[str] = []
    for path in sorted(root.rglob("*")):
        if len(blocks) >= max_files:
            break
        if not path.is_file() or path.suffix not in SAMPLE_EXCERPT_SUFFIXES:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
        blocks.append(f"#### {path.relative_to(root).as_posix()}\n\n```\n{text}\n```")
    return "\n\n".join(blocks)


def load_repository_template(workspace: Path) -> TemplateManifest:
    directory = workspace / TEMPLATE_DIRNAME
    if not directory.is_dir():
        return TemplateManifest()

    data = _read_manifest(directory)
    manifest = TemplateManifest(**data)
    manifest.root = directory
    manifest.rules = _read_rules(directory)

    samples = _sample_roots(directory)
    manifest.sample_tree = "\n\n".join(
        f"{sample.name}/\n{_render_tree(sample)}" for sample in samples
    )
    manifest.sample_excerpts = "\n\n".join(_render_excerpts(sample) for sample in samples)

    if not manifest.commands.test_architecture.all():
        for name in ARCHITECTURE_TEST_DIRNAMES:
            if (directory / name).is_dir():
                manifest.commands.test_architecture = CommandSet(
                    backend=f"pytest {TEMPLATE_DIRNAME}/{name}"
                    if manifest.stack.backend in (None, "fastapi", "python")
                    else None
                )
                break
    return manifest


def load_bootstrap_template(templates_dir: Path, template_id: str) -> TemplateManifest:
    directory = templates_dir / template_id
    if not directory.is_dir():
        return TemplateManifest(id=template_id)
    data = _read_manifest(directory)
    manifest = TemplateManifest(**data)
    manifest.root = directory
    manifest.rules = _read_rules(directory)
    content = directory / "template"
    if content.is_dir():
        manifest.sample_tree = _render_tree(content)
    return manifest
