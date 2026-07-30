import json
from pathlib import Path

from flywheel.agents.codex import CodexRunner
from flywheel.agents.execution import ExecutionEnvironment
from flywheel.domain.enums import AgentKind, ExecutionMode, TaskStatus, Transport
from flywheel.domain.project import ProjectConfig
from flywheel.domain.run import RunOutcome, RunSpec
from flywheel.domain.task import Repository, Task
from flywheel.domain.template import TemplateManifest


def _task() -> Task:
    return Task(
        project_item_id="ITEM",
        issue_number=7,
        issue_node_id="NODE",
        title="Add health endpoint",
        body="Return the running version.",
        url="https://github.com/acme/api/issues/7",
        repository=Repository(owner="acme", name="api"),
        agent=AgentKind.CODEX,
        status=TaskStatus.TODO,
    )


def _environment(tmp_path: Path, mode: ExecutionMode) -> ExecutionEnvironment:
    return ExecutionEnvironment(
        mode=mode,
        workspace=tmp_path / "workspace",
        run_dir=tmp_path / "run",
        env={"CODEX_API_KEY": "secret"},
        mounts={str(tmp_path / "auth.json"): "/home/agent/.codex/auth.json"},
    )


def _spec(tmp_path: Path, resume: str | None = None) -> RunSpec:
    return RunSpec(
        run_id="run-1",
        task=_task(),
        project=ProjectConfig(),
        template=TemplateManifest(),
        workspace=tmp_path / "workspace",
        prompt="do the thing",
        system_prompt="system",
        agent=AgentKind.CODEX,
        transport=Transport.CLI,
        resume_session_id=resume,
    )


def test_container_command_uses_danger_sandbox_and_config(tmp_path: Path) -> None:
    runner = CodexRunner(_environment(tmp_path, ExecutionMode.CONTAINER))

    command = runner.build_command(_spec(tmp_path))

    assert command[0] == "docker"
    assert "exec" in command
    assert command[command.index("--sandbox") + 1] == "danger-full-access"
    assert "--skip-git-repo-check" in command
    assert "auth.json:/home/agent/.codex/auth.json" in " ".join(command)


def test_mcp_servers_are_passed_as_overrides_codex_actually_reads(tmp_path: Path) -> None:
    from flywheel.mcp.registry import McpRegistry

    overrides = McpRegistry(repository="acme/api", github_token="tok").codex_overrides()
    runner = CodexRunner(_environment(tmp_path, ExecutionMode.HOST), config_overrides=overrides)

    command = runner.build_command(_spec(tmp_path))
    resumed = runner.build_command(_spec(tmp_path, resume="thread-9"))

    github = next(part for part in command if part.startswith("mcp_servers.github="))
    assert command[command.index(github) - 1] == "-c"
    assert 'command = "github-mcp-server"' in github
    assert 'GITHUB_PERSONAL_ACCESS_TOKEN = "tok"' in github
    assert any(part.startswith("mcp_servers.playwright=") for part in command)
    # A config file passed by path is ignored by codex, so it must never be relied on.
    assert not any("config_file=" in part for part in command)
    # Resuming an answered question keeps the same servers.
    assert any(part.startswith("mcp_servers.github=") for part in resumed)


def test_resume_uses_resume_subcommand(tmp_path: Path) -> None:
    runner = CodexRunner(_environment(tmp_path, ExecutionMode.HOST), executable="codex")

    command = runner.build_command(_spec(tmp_path, resume="thread-9"))

    assert command[:3] == ["codex", "exec", "resume"]
    assert "thread-9" in command


def test_stream_captures_thread_and_cost(tmp_path: Path) -> None:
    runner = CodexRunner(_environment(tmp_path, ExecutionMode.HOST))
    outcome = RunOutcome()
    seen: list[str] = []

    runner._consume(
        {"type": "thread.started", "thread_id": "th-1"},
        lambda kind, payload: seen.append(kind),
        outcome,
    )
    text = runner._consume(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        lambda kind, payload: seen.append(kind),
        outcome,
    )
    runner._consume(
        {"type": "turn.completed", "usage": {"cost_usd": 0.3}},
        lambda kind, payload: seen.append(kind),
        outcome,
    )

    assert outcome.session_id == "th-1"
    assert text == "done"
    assert outcome.cost_usd == 0.3
    assert "init" in seen and "result" in seen


def test_result_file_is_read_when_stream_lacks_json(tmp_path: Path) -> None:
    environment = _environment(tmp_path, ExecutionMode.HOST)
    environment.run_dir.mkdir(parents=True, exist_ok=True)
    (environment.run_dir / "result.txt").write_text(
        json.dumps({"status": "completed", "summary": "from file"}), encoding="utf-8"
    )
    runner = CodexRunner(environment)

    result = runner._read_result_file(_spec(tmp_path))

    assert result is not None
    assert result.summary == "from file"


def test_error_event_sets_outcome_error(tmp_path: Path) -> None:
    runner = CodexRunner(_environment(tmp_path, ExecutionMode.HOST))
    outcome = RunOutcome()

    runner._consume({"type": "error", "message": "sandbox denied"}, lambda k, p: None, outcome)

    assert outcome.error == "sandbox denied"
