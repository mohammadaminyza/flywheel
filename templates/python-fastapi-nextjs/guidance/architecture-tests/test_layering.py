import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend" / "app"

LAYER_BANS = {
    "api/routes": {
        "app.repositories": "routes must go through a service, never a repository",
        "app.domains.orm": "routes must never touch ORM models",
    },
    "services": {
        "app.schemas": "services must not import router schemas",
        "app.domains.orm": "services must go through repositories, never ORM models",
    },
    "repositories": {
        "app.schemas": "repositories must not import router schemas",
        "app.services": "repositories must not depend on services",
    },
    "domains": {
        "app.schemas": "domain models must not import router schemas",
        "app.services": "domain models must not depend on services",
        "app.repositories": "domain models must not depend on repositories",
    },
}


def _python_files(relative: str) -> list[Path]:
    directory = BACKEND / relative
    if not directory.is_dir():
        return []
    return [path for path in directory.rglob("*.py") if path.name != "__init__.py"]


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
    return found


def _all_layer_files() -> list[tuple[str, Path]]:
    return [(layer, path) for layer in LAYER_BANS for path in _python_files(layer)]


@pytest.mark.skipif(not BACKEND.is_dir(), reason="backend/app has not been created yet")
@pytest.mark.parametrize("layer,path", _all_layer_files(), ids=lambda value: str(value))
def test_layer_does_not_import_forbidden_modules(layer: str, path: Path) -> None:
    banned = LAYER_BANS[layer]
    for imported in _imports(path):
        for forbidden, reason in banned.items():
            assert not imported.startswith(forbidden), (
                f"{path.relative_to(BACKEND)} imports {imported}: {reason}"
            )


@pytest.mark.skipif(not BACKEND.is_dir(), reason="backend/app has not been created yet")
@pytest.mark.parametrize("path", _python_files("api/routes"), ids=lambda value: str(value))
def test_routes_contain_no_branching(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for inner in ast.walk(node):
                assert not isinstance(inner, ast.If), (
                    f"{path.relative_to(BACKEND)}::{node.name} branches; "
                    f"move the decision into the service"
                )


@pytest.mark.skipif(not BACKEND.is_dir(), reason="backend/app has not been created yet")
@pytest.mark.parametrize("path", _python_files("services"), ids=lambda value: str(value))
def test_services_do_not_raise_http_exception(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            name = node.exc.func
            raised = name.id if isinstance(name, ast.Name) else getattr(name, "attr", "")
            assert raised != "HTTPException", (
                f"{path.relative_to(BACKEND)} raises HTTPException; "
                f"raise a domain exception instead"
            )


@pytest.mark.skipif(not BACKEND.is_dir(), reason="backend/app has not been created yet")
@pytest.mark.parametrize("path", _python_files("repositories"), ids=lambda value: str(value))
def test_repositories_do_not_raise(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Raise), (
            f"{path.relative_to(BACKEND)} raises; validation belongs in the service"
        )


@pytest.mark.skipif(not BACKEND.is_dir(), reason="backend/app has not been created yet")
def test_no_dataclasses_in_domain() -> None:
    for path in _python_files("domains"):
        for imported in _imports(path):
            assert imported != "dataclasses", (
                f"{path.relative_to(BACKEND)} uses a dataclass; "
                f"domain containers are pydantic BaseModel"
            )


@pytest.mark.skipif(not BACKEND.is_dir(), reason="backend/app has not been created yet")
def test_no_lazy_imports() -> None:
    for _, path in _all_layer_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for inner in ast.walk(node):
                assert not isinstance(inner, ast.Import | ast.ImportFrom), (
                    f"{path.relative_to(BACKEND)}::{node.name} imports inside a function; "
                    f"imports belong at module top"
                )
