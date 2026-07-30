import json
from pathlib import Path

import pytest

from flywheel.agents.prompt import build_system_prompt
from flywheel.config import Settings
from flywheel.domain.enums import TaskStatus
from flywheel.domain.project import ProjectConfig
from flywheel.domain.template import describe_repository, has_code
from flywheel.services.dispatcher import PROJECT_CONFIG_PATH
from flywheel.services.scaffold_service import ScaffoldService
from tests.test_dispatcher import _build, _outcome, _task

BUNDLED = Path(__file__).resolve().parents[1] / "templates"


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    created = Settings(workspace_dir=str(tmp_path / "ws"))
    created.github.token = "tok"
    created.github.owner = "acme"
    created.github.project_number = 1
    created.planning.auto_refine = False
    return created


def _repository(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_a_readme_is_not_a_project(tmp_path: Path) -> None:
    root = _repository(
        tmp_path, {"README.md": "# hi", "LICENSE": "MIT", ".github/ci.yml": "on: push"}
    )

    assert has_code(root) is False


def test_source_files_and_manifests_count_as_a_project(tmp_path: Path) -> None:
    assert has_code(_repository(tmp_path, {"src/app.ts": "export {}"})) is True
    assert has_code(_repository(tmp_path, {"pyproject.toml": "[project]"})) is True
    assert has_code(_repository(tmp_path, {"api/Api.csproj": "<Project/>"})) is True


def test_the_factorys_own_guidance_is_not_mistaken_for_code(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    ScaffoldService(BUNDLED).apply_guidance(root, "python-fastapi-nextjs")

    # The staged .template/ folder is full of sample .py and .ts files.
    assert has_code(root) is False


def test_an_existing_project_describes_its_own_checks(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "backend/pyproject.toml": "[project]\nname='x'\n[tool.ruff]\n[tool.mypy]\n"
            "[tool.pytest.ini_options]\n",
            "backend/uv.lock": "",
            "backend/tests/unit/test_x.py": "def test_x(): pass",
            "backend/tests/integration/test_y.py": "def test_y(): pass",
            "web/package.json": json.dumps(
                {"scripts": {"lint": "eslint .", "test": "vitest", "build": "vite build"}}
            ),
            "web/package-lock.json": "{}",
            "web/tsconfig.json": "{}",
        },
    )

    manifest = describe_repository(root, "acme-api")

    assert manifest.discovered is True
    assert manifest.name == "acme-api"
    assert manifest.commands.install.backend == "cd backend && uv sync --all-extras"
    assert manifest.commands.lint.backend == "cd backend && uv run ruff check ."
    assert manifest.commands.typecheck.backend == "cd backend && uv run mypy ."
    assert manifest.commands.test_unit.backend == "cd backend && uv run pytest tests/unit -q"
    assert manifest.commands.test_integration.backend == (
        "cd backend && uv run pytest tests/integration -q"
    )
    assert manifest.commands.install.frontend == "cd web && npm ci"
    assert manifest.commands.lint.frontend == "cd web && npm run lint"
    assert manifest.commands.typecheck.frontend == "cd web && npx tsc --noEmit"
    assert manifest.commands.build.frontend == "cd web && npm run build"
    assert "backend/" in manifest.sample_tree or "backend" in manifest.sample_tree


def test_commands_at_the_repository_root_are_not_prefixed(tmp_path: Path) -> None:
    root = _repository(
        tmp_path, {"pyproject.toml": "[project]\n[tool.ruff]\n", "app/main.py": "x = 1"}
    )

    manifest = describe_repository(root)

    assert manifest.commands.lint.backend == "ruff check ."


def test_the_prompt_tells_the_agent_to_follow_the_existing_code(tmp_path: Path) -> None:
    root = _repository(tmp_path, {"src/app.ts": "export {}", "package.json": "{}"})

    prompt = build_system_prompt(describe_repository(root), ProjectConfig())

    assert "This repository is its own authority" in prompt
    assert "Do not restructure" in prompt
    assert "The structure you are extending" in prompt
    # Nothing from the bundled template leaks in.
    assert "Reference structure" not in prompt
    assert "credential" not in prompt.casefold()


def test_a_repository_with_code_is_never_scaffolded(settings: Settings, tmp_path: Path) -> None:
    dispatcher, _, _, _, _, runner, workspaces = _build(
        settings, tmp_path, [_task()], [_outcome()]
    )

    dispatcher.tick()

    workspace = workspaces.created[0]
    assert "chore: initialize project scaffold" not in workspace.committed
    # No bundled guidance was staged over somebody else's project.
    assert not (workspace.path / ".template").exists()
    assert not (workspace.path / PROJECT_CONFIG_PATH).exists()
    assert "This repository is its own authority" in runner.specs[0].system_prompt
    assert "existing repository" in runner.specs[0].prompt


def test_a_repository_with_no_code_gets_the_template(settings: Settings, tmp_path: Path) -> None:
    settings.templates_dir = str(BUNDLED)
    dispatcher, _, _, _, _, runner, workspaces = _build(
        settings, tmp_path, [_task()], [_outcome()], empty=True
    )
    dispatcher._scaffold = ScaffoldService(BUNDLED)

    dispatcher.tick()

    workspace = workspaces.created[0]
    assert (workspace.path / "backend" / "app" / "main.py").exists()
    assert (workspace.path / PROJECT_CONFIG_PATH).read_text(encoding="utf-8").endswith(
        "template: python-fastapi-nextjs\n"
    )
    # The template's own rules apply, not a description of the skeleton.
    assert "Architectural rules for this repository" in runner.specs[0].system_prompt
    assert "This repository is its own authority" not in runner.specs[0].system_prompt


def test_a_declared_template_keeps_applying_after_the_scaffold_has_code(
    settings: Settings, tmp_path: Path
) -> None:
    dispatcher, _, _, _, _, runner, workspaces = _build(
        settings,
        tmp_path,
        [_task()],
        [_outcome()],
        project=ProjectConfig(template="python-fastapi-nextjs"),
    )
    dispatcher._scaffold = ScaffoldService(BUNDLED)

    dispatcher.tick()

    workspace = workspaces.created[0]
    assert "chore: initialize project scaffold" not in workspace.committed
    assert (workspace.path / ".template" / "README.md").exists()
    assert "Architectural rules for this repository" in runner.specs[0].system_prompt


def test_feature_branches_are_cut_from_the_configured_base(
    settings: Settings, tmp_path: Path
) -> None:
    settings.github.base_branch = "dev"
    dispatcher, _, _, pulls, _, _, workspaces = _build(
        settings, tmp_path, [_task()], [_outcome()]
    )

    dispatcher.tick()

    assert workspaces.created[0].branched_from == [("feat/7-add-health-endpoint", "dev")]
    assert pulls.opened[0]["base"] == "dev"


def test_without_a_configured_base_the_repository_decides(
    settings: Settings, tmp_path: Path
) -> None:
    dispatcher, _, _, pulls, _, _, workspaces = _build(
        settings, tmp_path, [_task()], [_outcome()]
    )

    dispatcher.tick()

    assert workspaces.created[0].branched_from == [("feat/7-add-health-endpoint", "main")]
    assert pulls.opened[0]["base"] == "main"


def test_an_empty_repository_is_initialised_on_the_configured_base(
    settings: Settings, tmp_path: Path
) -> None:
    settings.github.base_branch = "master"
    templates = tmp_path / "templates" / "python-fastapi-nextjs" / "template"
    templates.mkdir(parents=True)
    (templates / "README.md").write_text("# new", encoding="utf-8")
    (templates.parent / "template.yml").write_text("id: python-fastapi-nextjs\n", encoding="utf-8")
    dispatcher, board, _, _, _, _, workspaces = _build(
        settings, tmp_path, [_task(TaskStatus.TODO)], [_outcome()], empty=True
    )

    dispatcher.tick()

    workspace = workspaces.created[0]
    assert workspace.pushed[0] == "master"
    assert workspace.committed[0] == "chore: initialize project scaffold"
    assert workspace.branched_from[0] == ("master", "")
