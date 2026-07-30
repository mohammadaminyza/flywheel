import subprocess
from pathlib import Path

import pytest

from flywheel.domain.project import ProjectConfig
from flywheel.domain.template import load_repository_template
from flywheel.services.exceptions import TemplateNotFoundException
from flywheel.services.scaffold_service import ScaffoldService

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def _git_repository(path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    return path


def test_bundled_template_is_discoverable() -> None:
    service = ScaffoldService(TEMPLATES)

    assert "python-fastapi-nextjs" in service.available()


def test_unknown_template_raises(tmp_path: Path) -> None:
    service = ScaffoldService(TEMPLATES)

    with pytest.raises(TemplateNotFoundException):
        service.apply(tmp_path, "does-not-exist")


def test_scaffolding_ships_the_project_and_not_the_template(tmp_path: Path) -> None:
    service = ScaffoldService(TEMPLATES)

    service.apply(tmp_path, "python-fastapi-nextjs")

    assert (tmp_path / "backend" / "app" / "main.py").exists()
    assert (tmp_path / "backend" / "tests" / "unit" / "test_health_service.py").exists()
    assert (tmp_path / "frontend" / "app" / "page.tsx").exists()
    assert (tmp_path / "frontend" / "package.json").exists()
    assert (tmp_path / "README.md").exists()
    assert (tmp_path / ".github" / "workflows" / "ci.yml").exists()
    assert (tmp_path / "docker-compose.yml").exists()
    assert (tmp_path / "docker-compose.deploy.yml").exists()
    # Guidance is staged for the agent, never shipped: nothing from it lands in the product
    # and the committed .gitignore keeps it out even without the local git exclude.
    assert not (tmp_path / "samples").exists()
    assert not (tmp_path / "architecture-tests").exists()
    assert "/.template/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_guidance_is_staged_for_the_agent_and_excluded_from_git(tmp_path: Path) -> None:
    workspace = _git_repository(tmp_path)

    ScaffoldService(TEMPLATES).apply(workspace, "python-fastapi-nextjs")

    assert (workspace / ".template" / "README.md").exists()
    assert (workspace / ".template" / "architecture-tests").is_dir()
    excluded = (workspace / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.template/" in excluded
    tracked = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ".template/" not in tracked
    assert "backend/app/main.py" in tracked


def test_guidance_is_restaged_on_an_existing_checkout(tmp_path: Path) -> None:
    workspace = _git_repository(tmp_path)

    assert ScaffoldService(TEMPLATES).apply_guidance(workspace, "python-fastapi-nextjs")

    manifest = load_repository_template(workspace)

    assert manifest.found
    assert manifest.has_architecture_tests
    assert "Routes are the single layer" in manifest.rules
    assert "credential.py" in manifest.sample_tree
    assert manifest.commands.test_unit.backend == "uv run pytest tests/unit -q"
    assert manifest.commands.lint.frontend == "npm run lint"


def test_a_repository_that_owns_its_rules_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / ".template").mkdir()
    (tmp_path / ".template" / "README.md").write_text("Our own rules.", encoding="utf-8")

    applied = ScaffoldService(TEMPLATES).apply_guidance(tmp_path, "python-fastapi-nextjs")

    assert applied is False
    assert (tmp_path / ".template" / "README.md").read_text(encoding="utf-8") == "Our own rules."


def test_scaffolded_prompt_carries_rules_and_samples(tmp_path: Path) -> None:
    from flywheel.agents.prompt import build_system_prompt

    ScaffoldService(TEMPLATES).apply(tmp_path, "python-fastapi-nextjs")
    manifest = load_repository_template(tmp_path)

    prompt = build_system_prompt(manifest, ProjectConfig())

    assert "Architectural rules for this repository" in prompt
    assert "Reference structure" in prompt
    assert "Reference code" in prompt
    assert "Architectural tests" in prompt
    assert "class Credential" in prompt


def test_your_own_templates_folder_wins_over_the_bundled_one(tmp_path: Path) -> None:
    mine = tmp_path / "mine" / "python-fastapi-nextjs"
    (mine / "template").mkdir(parents=True)
    (mine / "template.yml").write_text("id: python-fastapi-nextjs\nname: Mine\n", encoding="utf-8")
    (mine / "template" / "README.md").write_text("# Mine", encoding="utf-8")
    target = tmp_path / "out"
    target.mkdir()

    service = ScaffoldService([tmp_path / "mine", TEMPLATES])
    catalog = {summary.id: summary for summary in service.catalog()}
    service.apply(target, "python-fastapi-nextjs")

    assert catalog["python-fastapi-nextjs"].custom is True
    assert catalog["python-fastapi-nextjs"].name == "Mine"
    assert (target / "README.md").read_text(encoding="utf-8") == "# Mine"
    assert not (target / "backend").exists()


def test_pointing_at_one_template_folder_works_like_pointing_at_a_folder_of_them(
    tmp_path: Path,
) -> None:
    single = tmp_path / "acme-stack"
    (single / "template").mkdir(parents=True)
    (single / "template.yml").write_text("id: acme-stack\nname: Acme\n", encoding="utf-8")
    (single / "template" / "README.md").write_text("# Acme", encoding="utf-8")
    target = tmp_path / "out"
    target.mkdir()

    service = ScaffoldService([single, TEMPLATES])

    assert [summary.id for summary in service.catalog()] == ["acme-stack", "python-fastapi-nextjs"]
    assert service.resolve("acme-stack") == single
    service.apply(target, "acme-stack")
    assert (target / "README.md").read_text(encoding="utf-8") == "# Acme"


def test_the_catalog_says_where_each_template_came_from(tmp_path: Path) -> None:
    mine = tmp_path / "mine" / "acme-stack"
    (mine / "template").mkdir(parents=True)
    (mine / "template.yml").write_text("id: acme-stack\n", encoding="utf-8")

    catalog = {s.id: s for s in ScaffoldService([tmp_path / "mine", TEMPLATES]).catalog()}

    assert catalog["acme-stack"].path == str(mine)
    assert catalog["acme-stack"].custom is True
    assert catalog["acme-stack"].has_guidance is False
    assert catalog["python-fastapi-nextjs"].path == str(TEMPLATES / "python-fastapi-nextjs")
    assert catalog["python-fastapi-nextjs"].custom is False
    assert catalog["python-fastapi-nextjs"].has_guidance is True


def test_variables_are_substituted(tmp_path: Path) -> None:
    template_root = tmp_path / "templates" / "demo"
    (template_root / "template").mkdir(parents=True)
    (template_root / "template.yml").write_text("id: demo\n", encoding="utf-8")
    (template_root / "template" / "README.md").write_text("# {{project_name}}", encoding="utf-8")
    target = tmp_path / "out"
    target.mkdir()

    ScaffoldService(tmp_path / "templates").apply(target, "demo", {"project_name": "Acme"})

    assert (target / "README.md").read_text(encoding="utf-8") == "# Acme"
