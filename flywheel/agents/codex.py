import json
from typing import Any

from flywheel.agents.base import (
    EventSink,
    extract_result_from_text,
    fallback_result,
    parse_structured,
    stream_process,
)
from flywheel.agents.execution import ExecutionEnvironment
from flywheel.domain.enums import AgentKind, Transport
from flywheel.domain.run import RunOutcome, RunSpec


class CodexRunner:
    kind = AgentKind.CODEX

    def __init__(
        self,
        environment: ExecutionEnvironment,
        executable: str = "codex",
        model: str = "",
    ) -> None:
        self._environment = environment
        self._executable = executable
        self._model = model

    def build_command(self, spec: RunSpec) -> list[str]:
        environment = self._environment
        config = environment.run_path("codex/config.toml")
        command = [
            self._executable,
            "exec",
            "--json",
            "--sandbox",
            "danger-full-access",
            "--skip-git-repo-check",
            "-c",
            f"config_file={config}",
            "-o",
            environment.run_path("result.txt"),
        ]
        if self._model:
            command += ["--model", self._model]
        if spec.resume_session_id:
            command = [
                self._executable,
                "exec",
                "resume",
                spec.resume_session_id,
                "--json",
                "--sandbox",
                "danger-full-access",
                "--skip-git-repo-check",
                "-o",
                environment.run_path("result.txt"),
            ]
        command.append("-")
        return environment.wrap(command)

    def run(self, spec: RunSpec, on_event: EventSink) -> RunOutcome:
        outcome = RunOutcome(transport_used=Transport.CLI)
        final_text = ""

        def handle(line: str) -> None:
            nonlocal final_text
            if not line.strip():
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                on_event("log", {"text": line})
                return
            text = self._consume(event, on_event, outcome)
            if text:
                final_text = text

        stdin = f"{spec.system_prompt}\n\n{spec.prompt}"
        exit_code, stderr = stream_process(
            self.build_command(spec), stdin, spec.timeout_seconds, handle
        )
        outcome.exit_code = exit_code

        if exit_code == 124:
            outcome.error = "agent exceeded its time limit"
            return outcome

        result = extract_result_from_text(final_text) or self._read_result_file(spec)
        if result is None:
            result = fallback_result(final_text or stderr, exit_code == 0)
        outcome.result = result

        if exit_code != 0 and not outcome.error:
            outcome.error = stderr.strip()[:1000] or f"codex exited with code {exit_code}"
        return outcome

    def _read_result_file(self, spec: RunSpec) -> Any:
        result_path = self._environment.run_dir / "result.txt"
        if not result_path.exists():
            return None
        text = result_path.read_text(encoding="utf-8", errors="replace")
        try:
            return parse_structured(json.loads(text))
        except json.JSONDecodeError:
            return extract_result_from_text(text)

    def _consume(self, event: dict[str, Any], on_event: EventSink, outcome: RunOutcome) -> str:
        kind = event.get("type") or event.get("msg", {}).get("type")
        if kind == "thread.started":
            outcome.session_id = event.get("thread_id") or event.get("session_id")
            on_event("init", {"session_id": outcome.session_id})
            return ""
        if kind in {"item.completed", "item.updated"}:
            item = event.get("item", {})
            if item.get("type") == "command_execution":
                on_event("tool", {"name": item.get("command", "")[:120]})
            elif item.get("type") == "agent_message":
                return str(item.get("text") or "")
            return ""
        if kind == "turn.completed":
            usage = event.get("usage", {})
            cost = usage.get("cost_usd") or event.get("total_cost_usd")
            if isinstance(cost, int | float):
                outcome.cost_usd = float(cost)
            on_event("result", {"cost_usd": outcome.cost_usd})
            return ""
        if kind == "error":
            outcome.error = str(event.get("message") or "codex reported an error")[:1000]
            on_event("error", {"error": outcome.error})
        return ""
