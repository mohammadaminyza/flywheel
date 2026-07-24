import shutil
import subprocess
from pathlib import Path

from flywheel.domain.task import Repository
from flywheel.services.exceptions import WorkspaceException

EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _git(path: Path, *args: str, timeout: int = 300) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise WorkspaceException(f"git {' '.join(args)} failed: {result.stderr.strip()[:400]}")
    return result.stdout.strip()


def authenticated_url(repository: Repository, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{repository.full_name}.git"


class Workspace:
    def __init__(self, path: Path, repository: Repository, token: str) -> None:
        self.path = path
        self.repository = repository
        self._token = token

    @property
    def is_empty_repository(self) -> bool:
        try:
            _git(self.path, "rev-parse", "HEAD")
        except WorkspaceException:
            return True
        return False

    def default_branch(self) -> str:
        try:
            reference = _git(self.path, "symbolic-ref", "refs/remotes/origin/HEAD")
            prefix = "refs/remotes/origin/"
            return reference.removeprefix(prefix)
        except WorkspaceException:
            return "main"

    def create_branch(self, branch: str) -> None:
        _git(self.path, "checkout", "-B", branch)

    def has_changes(self) -> bool:
        return bool(_git(self.path, "status", "--porcelain"))

    def commit_all(self, message: str) -> None:
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-m", message)

    def head(self) -> str:
        return _git(self.path, "rev-parse", "HEAD")

    def commits_ahead(self, base: str) -> int:
        try:
            output = _git(self.path, "rev-list", "--count", f"origin/{base}..HEAD")
        except WorkspaceException:
            return 1 if not self.is_empty_repository else 0
        return int(output or 0)

    def diff_stat(self, base: str) -> str:
        try:
            return _git(self.path, "diff", "--stat", f"origin/{base}...HEAD")
        except WorkspaceException:
            return _git(self.path, "show", "--stat", "--oneline", "HEAD")

    def changed_files(self, base: str) -> list[str]:
        try:
            output = _git(self.path, "diff", "--name-only", f"origin/{base}...HEAD")
        except WorkspaceException:
            output = _git(self.path, "show", "--name-only", "--pretty=format:", "HEAD")
        return [line for line in output.splitlines() if line.strip()]

    def push(self, branch: str) -> None:
        _git(
            self.path,
            "push",
            "--set-upstream",
            authenticated_url(self.repository, self._token),
            branch,
            timeout=600,
        )

    def recover_missing_base(self, base: str, branch: str) -> None:
        """Attach an already-pushed feature snapshot to a valid base branch.

        This is used only to recover factory-owned branches from the historical
        empty-repository bug. The final tree is preserved exactly, while the
        feature history is replaced with a single commit whose parent is the
        newly created (or already existing) base commit.
        """
        old_head = self.head()
        try:
            _git(
                self.path,
                "fetch",
                "origin",
                f"{base}:refs/remotes/origin/{base}",
                timeout=600,
            )
            base_sha = _git(self.path, "rev-parse", f"refs/remotes/origin/{base}")
        except WorkspaceException:
            base_sha = _git(
                self.path,
                "commit-tree",
                EMPTY_TREE_SHA,
                "-m",
                "chore: initialize repository base",
            )
            _git(self.path, "branch", "-f", base, base_sha)
            self.push(base)

        final_tree = _git(self.path, "rev-parse", f"{old_head}^{{tree}}")
        recovered_head = _git(
            self.path,
            "commit-tree",
            final_tree,
            "-p",
            base_sha,
            "-m",
            f"feat: recover completed work for {branch}",
        )
        _git(self.path, "reset", "--hard", recovered_head)
        _git(
            self.path,
            "push",
            f"--force-with-lease=refs/heads/{branch}:{old_head}",
            authenticated_url(self.repository, self._token),
            f"{recovered_head}:refs/heads/{branch}",
            timeout=600,
        )

    def destroy(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class WorkspaceFactory:
    def __init__(self, root: Path, token: str) -> None:
        self._root = root
        self._token = token

    def prepare(self, repository: Repository, run_id: str) -> Workspace:
        path = self._root / run_id
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)

        url = authenticated_url(repository, self._token)
        result = subprocess.run(
            ["git", "clone", "--depth", "50", url, str(path)],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "You appear to have cloned an empty repository" not in stderr:
                safe = stderr[:300].replace(self._token, "***")
                raise WorkspaceException(f"could not clone {repository.full_name}: {safe}")
            _git(path, "init")
            _git(path, "remote", "add", "origin", url)

        _git(path, "config", "user.email", "factory@localhost")
        _git(path, "config", "user.name", "Flywheel")
        return Workspace(path, repository, self._token)
