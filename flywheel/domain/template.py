import json
from collections.abc import Iterator
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
    discovered: bool = False
    """True when this was read off an existing codebase rather than a bundled template."""
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


CODE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cs",
    ".go",
    ".java",
    ".kt",
    ".rb",
    ".rs",
    ".php",
    ".swift",
    ".vue",
    ".svelte",
    ".scala",
    ".ex",
    ".dart",
}
IGNORED_DIRNAMES = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    TEMPLATE_DIRNAME,
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".next",
    "dist",
    "build",
    "target",
    "bin",
    "obj",
}
PROJECT_MANIFESTS = (
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
    "requirements.txt",
)


def _walk(root: Path) -> Iterator[tuple[Path, Path]]:
    """Every file in the repository that belongs to the product."""
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRNAMES for part in relative.parts):
            continue
        if path.is_file():
            yield relative, path


def has_code(workspace: Path) -> bool:
    """Does this repository already contain an application?

    A README, a licence and a CI workflow are not a project. Source files, or a package
    manifest such as `pyproject.toml` or `package.json`, are.
    """
    for relative, path in _walk(workspace):
        if path.suffix in CODE_SUFFIXES:
            return True
        if relative.name in PROJECT_MANIFESTS or relative.suffix in {".csproj", ".sln"}:
            return True
    return False


def _repository_tree(root: Path, limit: int = 160) -> str:
    entries: list[str] = []
    seen_directories: set[Path] = set()
    for relative, _path in sorted(_walk(root), key=lambda item: item[0].as_posix()):
        for depth in range(len(relative.parts) - 1):
            directory = Path(*relative.parts[: depth + 1])
            if directory not in seen_directories:
                seen_directories.add(directory)
                entries.append(f"{'  ' * depth}{directory.name}/")
        entries.append(f"{'  ' * (len(relative.parts) - 1)}{relative.name}")
        if len(entries) >= limit:
            entries.append("... (truncated)")
            break
    return "\n".join(entries)


def _find_manifest(root: Path, name: str) -> Path | None:
    """The nearest copy of a manifest: repository root first, then one level down."""
    candidate = root / name
    if candidate.is_file():
        return candidate
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        if child.is_dir() and child.name not in IGNORED_DIRNAMES and (child / name).is_file():
            return child / name
    return None


def _prefixed(manifest: Path, root: Path, command: str) -> str:
    directory = manifest.parent
    if directory == root:
        return command
    return f"cd {directory.relative_to(root).as_posix()} && {command}"


def _python_commands(manifest: Path, root: Path, commands: TemplateCommands) -> None:
    directory = manifest.parent
    text = manifest.read_text(encoding="utf-8", errors="replace")
    runner = ""
    if (directory / "uv.lock").exists() or "[tool.uv]" in text:
        runner = "uv run "
        commands.install.backend = _prefixed(manifest, root, "uv sync --all-extras")
    elif (directory / "poetry.lock").exists():
        runner = "poetry run "
        commands.install.backend = _prefixed(manifest, root, "poetry install")
    elif (directory / "requirements.txt").exists():
        commands.install.backend = _prefixed(manifest, root, "pip install -r requirements.txt")

    def add(command: str) -> str:
        return _prefixed(manifest, root, f"{runner}{command}")

    if "ruff" in text:
        commands.lint.backend = add("ruff check .")
    if "mypy" in text:
        commands.typecheck.backend = add("mypy .")
    if "pytest" in text or (directory / "tests").is_dir():
        unit = directory / "tests" / "unit"
        integration = directory / "tests" / "integration"
        commands.test_unit.backend = add(f"pytest {'tests/unit' if unit.is_dir() else 'tests'} -q")
        if integration.is_dir():
            commands.test_integration.backend = add("pytest tests/integration -q")


def _node_commands(manifest: Path, root: Path, commands: TemplateCommands) -> None:
    directory = manifest.parent
    try:
        data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return
    scripts = data.get("scripts") if isinstance(data, dict) else None
    scripts = scripts if isinstance(scripts, dict) else {}
    runner = "npm run "
    if (directory / "pnpm-lock.yaml").exists():
        runner = "pnpm run "
        commands.install.frontend = _prefixed(manifest, root, "pnpm install")
    elif (directory / "yarn.lock").exists():
        runner = "yarn "
        commands.install.frontend = _prefixed(manifest, root, "yarn install")
    elif (directory / "package-lock.json").exists():
        commands.install.frontend = _prefixed(manifest, root, "npm ci")
    else:
        commands.install.frontend = _prefixed(manifest, root, "npm install")

    def add(script: str) -> str:
        return _prefixed(manifest, root, f"{runner}{script}")

    if "lint" in scripts:
        commands.lint.frontend = add("lint")
    if "typecheck" in scripts:
        commands.typecheck.frontend = add("typecheck")
    elif (directory / "tsconfig.json").exists():
        commands.typecheck.frontend = _prefixed(manifest, root, "npx tsc --noEmit")
    if "test" in scripts:
        commands.test_unit.frontend = add("test")
    if "build" in scripts:
        commands.build.frontend = add("build")


def describe_repository(workspace: Path, name: str = "") -> TemplateManifest:
    """Read the conventions of a repository that already has code.

    The existing project is its own template: its layout is the structure to follow and its
    own scripts are the checks that must pass. Nothing is imposed on it from outside.
    """
    commands = TemplateCommands()
    python = _find_manifest(workspace, "pyproject.toml")
    node = _find_manifest(workspace, "package.json")
    if python:
        _python_commands(python, workspace, commands)
    if node:
        _node_commands(node, workspace, commands)
    stack = TemplateStack(
        backend="python" if python else None,
        frontend="node" if node else None,
    )
    return TemplateManifest(
        id="existing-project",
        name=name or workspace.name,
        description="Conventions read from the repository itself.",
        discovered=True,
        root=workspace,
        stack=stack,
        commands=commands,
        sample_tree=_repository_tree(workspace),
    )


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
