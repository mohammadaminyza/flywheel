import json
from pathlib import Path

from flywheel.agents.base import extract_result_from_text
from flywheel.agents.claude_code import ClaudeCodeRunner
from flywheel.agents.codex import CodexRunner
from flywheel.agents.execution import ExecutionEnvironment
from flywheel.agents.prompt import build_system_prompt, build_task_prompt
from flywheel.domain.enums import AgentKind, ExecutionMode, TaskStatus, Transport
from flywheel.domain.project import ProjectConfig
from flywheel.domain.run import RunSpec
from flywheel.domain.task import Repository, Task
from flywheel.domain.template import TemplateManifest, load_repository_template
from flywheel.mcp.registry import McpRegistry


def _task() -> Task:
    return Task(
        project_item_id="ITEM",
        issue_number=7,
        issue_node_id="NODE",
        title="Add health endpoint",
        body="Return the running version.",
        url="https://github.com/acme/api/issues/7",
        repository=Repository(owner="acme", name="api"),
        agent=AgentKind.CLAUDE_CODE,
        status=TaskStatus.TODO,
    )


def _environment(tmp_path: Path, mode: ExecutionMode) -> ExecutionEnvironment:
    return ExecutionEnvironment(
        mode=mode,
        workspace=tmp_path / "workspace",
        run_dir=tmp_path / "run",
        env={"ANTHROPIC_API_KEY": "secret"},
        mounts={str(tmp_path / "creds.json"): "/home/agent/.claude/.credentials.json"},
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
        agent=AgentKind.CLAUDE_CODE,
        transport=Transport.CLI,
        resume_session_id=resume,
    )


def test_container_command_mounts_workspace_and_credentials(tmp_path: Path) -> None:
    runner = ClaudeCodeRunner(_environment(tmp_path, ExecutionMode.CONTAINER))

    command = runner.build_command(_spec(tmp_path))

    assert command[0] == "docker"
    assert "--rm" in command
    assert f"{tmp_path / 'workspace'}:/workspace" in command
    assert f"{tmp_path / 'creds.json'}:/home/agent/.claude/.credentials.json" in command
    assert "ANTHROPIC_API_KEY=secret" in command
    assert "--permission-mode" in command
    assert command[command.index("--permission-mode") + 1] == "bypassPermissions"


def test_container_command_uses_container_paths(tmp_path: Path) -> None:
    runner = ClaudeCodeRunner(_environment(tmp_path, ExecutionMode.CONTAINER))

    command = runner.build_command(_spec(tmp_path))

    assert command[command.index("--mcp-config") + 1] == "/run/mcp.json"
    assert command[command.index("--append-system-prompt-file") + 1] == "/run/system.md"
    assert "--strict-mcp-config" in command


def test_host_command_uses_real_paths_and_safer_permissions(tmp_path: Path) -> None:
    runner = ClaudeCodeRunner(_environment(tmp_path, ExecutionMode.HOST))

    command = runner.build_command(_spec(tmp_path))

    assert command[0] == "claude"
    assert command[command.index("--mcp-config") + 1] == str(tmp_path / "run" / "mcp.json")
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"


def test_resume_passes_session_id(tmp_path: Path) -> None:
    runner = ClaudeCodeRunner(_environment(tmp_path, ExecutionMode.HOST))

    command = runner.build_command(_spec(tmp_path, resume="sess-42"))

    assert command[command.index("--resume") + 1] == "sess-42"


def test_codex_resume_uses_resume_supported_options(tmp_path: Path) -> None:
    runner = CodexRunner(_environment(tmp_path, ExecutionMode.HOST))

    command = runner.build_command(_spec(tmp_path, resume="sess-42"))

    assert command[:3] == ["codex", "exec", "resume"]
    assert "--sandbox" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert command[-2:] == ["sess-42", "-"]


def test_stream_events_capture_session_and_cost(tmp_path: Path) -> None:
    runner = ClaudeCodeRunner(_environment(tmp_path, ExecutionMode.HOST))
    from flywheel.domain.run import RunOutcome

    outcome = RunOutcome()
    seen: list[tuple[str, dict]] = []

    runner._consume(
        {"type": "system", "subtype": "init", "session_id": "abc", "model": "opus"},
        lambda kind, payload: seen.append((kind, payload)),
        outcome,
    )
    runner._consume(
        {"type": "result", "session_id": "abc", "total_cost_usd": 0.42, "num_turns": 9},
        lambda kind, payload: seen.append((kind, payload)),
        outcome,
    )

    assert outcome.session_id == "abc"
    assert outcome.cost_usd == 0.42
    assert [kind for kind, _ in seen] == ["init", "result"]


def test_extracts_result_from_fenced_json() -> None:
    text = 'Here you go.\n```json\n{"status": "completed", "summary": "did it"}\n```'

    result = extract_result_from_text(text)

    assert result is not None
    assert result.completed
    assert result.summary == "did it"


def test_extracts_needs_input_with_questions() -> None:
    text = json.dumps(
        {
            "status": "needs_input",
            "summary": "blocked",
            "questions": [{"question": "Which auth?", "options": ["JWT", "session"]}],
        }
    )

    result = extract_result_from_text(text)

    assert result is not None
    assert result.needs_input
    assert result.questions[0].question == "Which auth?"


def test_mcp_registry_writes_both_formats(tmp_path: Path) -> None:
    registry = McpRegistry(repository="acme/api", github_token="tok")

    claude = registry.claude_config()
    codex = registry.codex_config()

    assert claude["mcpServers"]["github"]["command"] == "github-mcp-server"
    assert claude["mcpServers"]["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "tok"
    assert "[mcp_servers.github]" in codex
    assert "required = true" in codex
    written = registry.write_claude(tmp_path / "mcp.json")
    assert json.loads(written.read_text())["mcpServers"]["playwright"]["command"] == "npx"


def test_remote_mcp_secrets_use_the_key_codex_accepts() -> None:
    from flywheel.domain.project import McpServerSpec

    registry = McpRegistry(
        repository="acme/api",
        github_token="tok",
        project_servers={
            "notion": McpServerSpec(
                url="https://mcp.example.test", env={"Authorization": "Bearer s"}
            )
        },
        include_defaults=False,
    )

    overrides = registry.codex_overrides()
    config = registry.codex_config()

    # Codex refuses to load a streamable-HTTP server that was handed `env`.
    assert 'http_headers = {Authorization = "Bearer s"}' in overrides[0]
    assert "env" not in overrides[0]
    assert "http_headers" in config
    # Claude keeps its own vocabulary.
    assert registry.claude_config()["mcpServers"]["notion"] == {
        "type": "http",
        "url": "https://mcp.example.test",
        "headers": {"Authorization": "Bearer s"},
    }


def test_disabled_mcp_connection_is_not_written(tmp_path: Path) -> None:
    from flywheel.domain.project import McpServerSpec

    registry = McpRegistry(
        repository="acme/app",
        github_token="token",
        project_servers={
            "disabled": McpServerSpec(enabled=False, command="never-run")
        },
        include_defaults=False,
    )

    assert "disabled" not in registry.codex_config()
    assert "disabled" not in registry.claude_config()["mcpServers"]

def test_mcp_registry_resolves_placeholders() -> None:
    from flywheel.domain.project import McpServerSpec

    registry = McpRegistry(
        repository="acme/api",
        github_token="tok",
        project_servers={"db": McpServerSpec(command="psql-mcp", env={"DSN": "${DB_DSN}"})},
        secrets={"DB_DSN": "postgres://localhost"},
    )

    assert registry.servers["db"].env["DSN"] == "postgres://localhost"


def test_loads_dynamic_template_folder_from_repository(tmp_path: Path) -> None:
    template_dir = tmp_path / ".template"
    (template_dir / "samples" / "app" / "services").mkdir(parents=True)
    (template_dir / "README.md").write_text("Routes must not contain logic.", encoding="utf-8")
    (template_dir / "samples" / "app" / "services" / "user_service.py").write_text(
        "class UserService:\n    pass\n", encoding="utf-8"
    )
    (template_dir / "template.yml").write_text(
        "id: acme\nname: Acme\ncommands:\n  test_unit:\n    backend: pytest tests/unit\n",
        encoding="utf-8",
    )

    manifest = load_repository_template(tmp_path)

    assert manifest.found
    assert manifest.id == "acme"
    assert "Routes must not contain logic." in manifest.rules
    assert "user_service.py" in manifest.sample_tree
    assert "class UserService" in manifest.sample_excerpts
    assert manifest.commands.test_unit.backend == "pytest tests/unit"


def test_missing_template_folder_is_not_an_error(tmp_path: Path) -> None:
    manifest = load_repository_template(tmp_path)

    assert not manifest.found
    assert manifest.rules == ""


def test_architecture_tests_detected_from_folder(tmp_path: Path) -> None:
    template_dir = tmp_path / ".template"
    (template_dir / "architecture-tests").mkdir(parents=True)
    (template_dir / "README.md").write_text("rules", encoding="utf-8")

    manifest = load_repository_template(tmp_path)

    assert manifest.has_architecture_tests


def test_system_prompt_carries_rules_and_result_contract(tmp_path: Path) -> None:
    template_dir = tmp_path / ".template"
    template_dir.mkdir()
    (template_dir / "README.md").write_text("Repositories never validate.", encoding="utf-8")
    manifest = load_repository_template(tmp_path)

    prompt = build_system_prompt(manifest, ProjectConfig(instructions="Ship carefully."))

    assert "Repositories never validate." in prompt
    assert "Ship carefully." in prompt
    assert "needs_input" in prompt
    assert "```json" in prompt


def test_task_prompt_differs_for_empty_repository() -> None:
    manifest = TemplateManifest(id="python-fastapi-nextjs")

    empty = build_task_prompt(_task(), manifest, ProjectConfig(), True, "feat/7-x")
    existing = build_task_prompt(_task(), manifest, ProjectConfig(), False, "feat/7-x")

    assert "Scaffold a new project" in empty
    assert "existing repository" in existing
    assert "Add health endpoint" in empty and "Add health endpoint" in existing


def test_task_prompt_includes_answer_and_previous_failure() -> None:
    prompt = build_task_prompt(
        _task(),
        TemplateManifest(),
        ProjectConfig(),
        False,
        "feat/7-x",
        previous_failure="pytest exited 1",
        answer="Use JWT.",
    )

    assert "Use JWT." in prompt
    assert "pytest exited 1" in prompt
