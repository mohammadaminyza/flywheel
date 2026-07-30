import json
from typing import Any

from flywheel.agents.base import (
    EventSink,
    extract_result_from_text,
    fallback_result,
    stream_process,
)
from flywheel.agents.execution import ExecutionEnvironment
from flywheel.domain.enums import AgentKind, ExecutionMode, Transport
from flywheel.domain.run import RunOutcome, RunSpec

DIRECTIVE = (
    "Carry out the task described in the input above, following your system prompt exactly. "
    "End with the JSON result block."
)
RESEARCH_TOOLS = "WebSearch WebFetch"


class ClaudeCodeRunner:
    kind = AgentKind.CLAUDE_CODE

    def __init__(
        self,
        environment: ExecutionEnvironment,
        executable: str = "claude",
        model: str = "",
    ) -> None:
        self._environment = environment
        self._executable = executable
        self._model = model

    def build_command(self, spec: RunSpec) -> list[str]:
        environment = self._environment
        command = [
            self._executable,
            "-p",
            DIRECTIVE,
            "--output-format",
            "stream-json",
            "--verbose",
            "--append-system-prompt-file",
            environment.run_path("system.md"),
            "--mcp-config",
            environment.run_path("mcp.json"),
            "--strict-mcp-config",
            "--max-turns",
            str(spec.max_turns),
        ]
        if environment.mode == ExecutionMode.CONTAINER:
            command += ["--permission-mode", "bypassPermissions"]
        else:
            command += ["--permission-mode", "acceptEdits"]
        if spec.research:
            # Pre-approve the research tools so planning can read the open web unattended.
            command += ["--allowed-tools", RESEARCH_TOOLS]
        if self._model:
            command += ["--model", self._model]
        if spec.resume_session_id:
            command += ["--resume", spec.resume_session_id]
        return environment.wrap(command)

    def run(self, spec: RunSpec, on_event: EventSink) -> RunOutcome:
        outcome = RunOutcome(transport_used=Transport.CLI)
        final_text = ""
        structured: dict[str, Any] | None = None

        def handle(line: str) -> None:
            nonlocal final_text, structured
            if not line.strip():
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                on_event("log", {"text": line})
                return
            self._consume(event, on_event, outcome)
            if event.get("type") == "result":
                final_text = str(event.get("result") or "")
                candidate = event.get("structured_output")
                if isinstance(candidate, dict):
                    structured = candidate

        exit_code, stderr = stream_process(
            self.build_command(spec), spec.prompt, spec.timeout_seconds, handle
        )
        outcome.exit_code = exit_code

        if exit_code == 124:
            outcome.error = "agent exceeded its time limit"
            return outcome

        result = None
        if structured is not None:
            from flywheel.agents.base import parse_structured

            result = parse_structured(structured)
        if result is None:
            result = extract_result_from_text(final_text)
        if result is None:
            result = fallback_result(final_text or stderr, exit_code == 0)
        outcome.result = result

        if exit_code != 0 and not outcome.error:
            outcome.error = stderr.strip()[:1000] or f"claude exited with code {exit_code}"
        return outcome

    def _consume(self, event: dict[str, Any], on_event: EventSink, outcome: RunOutcome) -> None:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            outcome.session_id = event.get("session_id")
            on_event(
                "init",
                {
                    "session_id": event.get("session_id"),
                    "model": event.get("model"),
                    "mcp_servers": event.get("mcp_servers"),
                },
            )
            return
        if kind == "system" and event.get("subtype") == "api_retry":
            on_event("retry", {"attempt": event.get("attempt"), "error": event.get("error")})
            return
        if kind == "assistant":
            for block in (event.get("message") or {}).get("content", []):
                if block.get("type") == "tool_use":
                    on_event("tool", {"name": block.get("name"), "input": block.get("input")})
                elif block.get("type") == "text" and block.get("text", "").strip():
                    on_event("text", {"text": block["text"][:2000]})
            return
        if kind == "result":
            outcome.session_id = event.get("session_id") or outcome.session_id
            cost = event.get("total_cost_usd")
            if isinstance(cost, int | float):
                outcome.cost_usd = float(cost)
            on_event(
                "result",
                {
                    "cost_usd": outcome.cost_usd,
                    "duration_ms": event.get("duration_ms"),
                    "num_turns": event.get("num_turns"),
                    "is_error": event.get("is_error"),
                },
            )
