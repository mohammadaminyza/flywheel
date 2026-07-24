import subprocess
from pathlib import Path

import pytest

from flywheel.domain.task import Repository
from flywheel.workspace import WorkspaceFactory


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def test_recovers_feature_only_repository_with_valid_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")

    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init")
    git(seed, "config", "user.email", "factory@localhost")
    git(seed, "config", "user.name", "Flywheel")
    git(seed, "checkout", "-b", "feat/7-page")
    (seed / "page.txt").write_text("landing page", encoding="utf-8")
    git(seed, "add", "page.txt")
    git(seed, "commit", "-m", "feat: build page")
    (seed / "test.txt").write_text("covered", encoding="utf-8")
    git(seed, "add", "test.txt")
    git(seed, "commit", "-m", "test: cover page")
    expected_tree = git(seed, "rev-parse", "HEAD^{tree}")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "origin", "HEAD:refs/heads/feat/7-page")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/feat/7-page")

    monkeypatch.setattr(
        "flywheel.workspace.authenticated_url",
        lambda repository, token: str(remote),
    )
    workspace = WorkspaceFactory(tmp_path / "workspaces", "token").prepare(
        Repository(owner="acme", name="app"),
        "run-1",
    )

    assert workspace.default_branch() == "feat/7-page"
    workspace.recover_missing_base("main", "feat/7-page")

    main = git(remote, "rev-parse", "refs/heads/main")
    feature = git(remote, "rev-parse", "refs/heads/feat/7-page")
    assert git(remote, "rev-parse", f"{feature}^{{tree}}") == expected_tree
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", main, feature],
        cwd=remote,
        check=True,
    )
