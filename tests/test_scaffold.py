from pathlib import Path

import pytest

from flywheel.domain.project import ProjectConfig
from flywheel.domain.template import load_repository_template
from flywheel.services.exceptions import TemplateNotFoundException
from flywheel.services.scaffold_service import ScaffoldService

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def test_bundled_template_is_discoverable() -> None:
    service = ScaffoldService(TEMPLATES)

    assert "python-fastapi-nextjs" in service.available()


def test_unknown_template_raises(tmp_path: Path) -> None:
    service = ScaffoldService(TEMPLATES)

    with pytest.raises(TemplateNotFoundException):
        service.apply(tmp_path, "does-not-exist")


def test_scaffolding_an_empty_repository_produces_a_usable_template_folder(
    tmp_path: Path,
) -> None:
    service = ScaffoldService(TEMPLATES)

    service.apply(tmp_path, "python-fastapi-nextjs")

    assert (tmp_path / ".template" / "README.md").exists()
    assert (tmp_path / ".github" / "workflows" / "ci.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "preview.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "release.yml").exists()
    assert (tmp_path / "docker-compose.deploy.yml").exists()

    manifest = load_repository_template(tmp_path)

    assert manifest.found
    assert manifest.has_architecture_tests
    assert "Routes are the single layer" in manifest.rules
    assert "credential.py" in manifest.sample_tree
    assert manifest.commands.test_unit.backend == "uv run pytest tests/unit -q"
    assert manifest.commands.lint.frontend == "npm run lint"


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


def test_variables_are_substituted(tmp_path: Path) -> None:
    template_root = tmp_path / "templates" / "demo"
    (template_root / "template").mkdir(parents=True)
    (template_root / "template.yml").write_text("id: demo\n", encoding="utf-8")
    (template_root / "template" / "README.md").write_text("# {{project_name}}", encoding="utf-8")
    target = tmp_path / "out"
    target.mkdir()

    ScaffoldService(tmp_path / "templates").apply(target, "demo", {"project_name": "Acme"})

    assert (target / "README.md").read_text(encoding="utf-8") == "# Acme"
