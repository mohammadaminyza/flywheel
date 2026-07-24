from pathlib import Path

from pydantic import BaseModel, Field

from flywheel.domain.enums import ExecutionMode


class ExecutionEnvironment(BaseModel):
    mode: ExecutionMode
    image: str = "flywheel-runner:latest"
    workspace: Path
    run_dir: Path
    env: dict[str, str] = Field(default_factory=dict)
    mounts: dict[str, str] = Field(default_factory=dict)
    container_workspace: str = "/workspace"
    container_run_dir: str = "/run"

    def path_in_sandbox(self, host_path: Path, root: Path, container_root: str) -> str:
        if self.mode == ExecutionMode.HOST:
            return str(host_path)
        relative = host_path.relative_to(root).as_posix()
        return f"{container_root}/{relative}" if relative != "." else container_root

    def run_path(self, name: str) -> str:
        if self.mode == ExecutionMode.HOST:
            return str(self.run_dir / name)
        return f"{self.container_run_dir}/{name}"

    def workspace_path(self) -> str:
        if self.mode == ExecutionMode.HOST:
            return str(self.workspace)
        return self.container_workspace

    def wrap(self, command: list[str]) -> list[str]:
        if self.mode == ExecutionMode.HOST:
            return command
        docker: list[str] = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--workdir",
            self.container_workspace,
            "-v",
            f"{self.workspace}:{self.container_workspace}",
            "-v",
            f"{self.run_dir}:{self.container_run_dir}",
        ]
        for host_path, container_path in self.mounts.items():
            docker += ["-v", f"{host_path}:{container_path}"]
        for key, value in self.env.items():
            if value:
                docker += ["-e", f"{key}={value}"]
        docker.append(self.image)
        return docker + command
