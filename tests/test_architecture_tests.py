import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ARCH_TESTS = (
    Path(__file__).resolve().parents[1]
    / "templates"
    / "python-fastapi-nextjs"
    / "guidance"
    / "architecture-tests"
)


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    template_dir = tmp_path / ".template"
    shutil.copytree(ARCH_TESTS, template_dir / "architecture-tests")
    for relative, content in files.items():
        path = tmp_path / "backend" / "app" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return template_dir / "architecture-tests"


def _run(directory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(directory), "-q", "--no-header"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_backend_passes(tmp_path: Path) -> None:
    directory = _project(
        tmp_path,
        {
            "api/routes/users.py": (
                "from app.services.users.service import UserService\n\n"
                "def list_users(service: UserService):\n"
                "    return service.list()\n"
            ),
            "services/users/service.py": (
                "from app.repositories.users import UserRepository\n\n"
                "class UserService:\n"
                "    def list(self):\n"
                "        return []\n"
            ),
            "repositories/users.py": (
                "class UserRepository:\n    def list(self):\n        return []\n"
            ),
        },
    )

    result = _run(directory)

    assert result.returncode == 0, result.stdout


def test_route_importing_repository_is_rejected(tmp_path: Path) -> None:
    directory = _project(
        tmp_path,
        {
            "api/routes/users.py": (
                "from app.repositories.users import UserRepository\n\n"
                "def list_users():\n    return UserRepository().list()\n"
            )
        },
    )

    result = _run(directory)

    assert result.returncode != 0
    assert "never a repository" in result.stdout


def test_branching_in_a_route_is_rejected(tmp_path: Path) -> None:
    directory = _project(
        tmp_path,
        {
            "api/routes/users.py": (
                "def list_users(admin: bool):\n    if admin:\n        return []\n    return None\n"
            )
        },
    )

    result = _run(directory)

    assert result.returncode != 0
    assert "move the decision into the service" in result.stdout


def test_service_raising_http_exception_is_rejected(tmp_path: Path) -> None:
    directory = _project(
        tmp_path,
        {
            "services/users/service.py": (
                "from fastapi import HTTPException\n\n"
                "class UserService:\n"
                "    def get(self):\n"
                "        raise HTTPException(status_code=404)\n"
            )
        },
    )

    result = _run(directory)

    assert result.returncode != 0
    assert "raise a domain exception instead" in result.stdout


def test_repository_raising_is_rejected(tmp_path: Path) -> None:
    directory = _project(
        tmp_path,
        {
            "repositories/users.py": (
                "class UserRepository:\n    def get(self):\n        raise ValueError('missing')\n"
            )
        },
    )

    result = _run(directory)

    assert result.returncode != 0
    assert "validation belongs in the service" in result.stdout


def test_dataclass_in_domain_is_rejected(tmp_path: Path) -> None:
    directory = _project(
        tmp_path,
        {
            "domains/entities/user.py": (
                "import dataclasses\n\n@dataclasses.dataclass\nclass User:\n    name: str\n"
            )
        },
    )

    result = _run(directory)

    assert result.returncode != 0
    assert "pydantic BaseModel" in result.stdout


def test_lazy_import_is_rejected(tmp_path: Path) -> None:
    directory = _project(
        tmp_path,
        {
            "services/users/service.py": (
                "class UserService:\n"
                "    def get(self):\n"
                "        from app.repositories.users import UserRepository\n"
                "        return UserRepository()\n"
            )
        },
    )

    result = _run(directory)

    assert result.returncode != 0
    assert "imports belong at module top" in result.stdout


@pytest.mark.parametrize("layer", ["services", "repositories", "domains"])
def test_schema_imports_are_rejected_outside_routes(tmp_path: Path, layer: str) -> None:
    directory = _project(
        tmp_path,
        {f"{layer}/thing.py": "from app.schemas.user import UserResponse\n"},
    )

    result = _run(directory)

    assert result.returncode != 0
    assert "router schemas" in result.stdout
