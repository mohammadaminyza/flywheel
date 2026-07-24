import json
import re
import subprocess
from collections.abc import Callable
from typing import Any, Protocol

from flywheel.domain.enums import AgentKind
from flywheel.domain.result import AgentResult
from flywheel.domain.run import RunOutcome, RunSpec

EventSink = Callable[[str, dict[str, Any]], None]

JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class AgentRunner(Protocol):
    kind: AgentKind

    def run(self, spec: RunSpec, on_event: EventSink) -> RunOutcome: ...


def parse_structured(payload: Any) -> AgentResult | None:
    if isinstance(payload, dict):
        try:
            return AgentResult(**payload)
        except (TypeError, ValueError):
            return None
    return None


def extract_result_from_text(text: str) -> AgentResult | None:
    if not text:
        return None
    match = JSON_BLOCK.search(text)
    candidates: list[str] = [match.group(1)] if match else []
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            return parse_structured(json.loads(candidate))
        except json.JSONDecodeError:
            continue
    return None


def fallback_result(text: str, ok: bool) -> AgentResult:
    return AgentResult(
        status="completed" if ok else "failed",
        summary=text.strip()[:2000] or ("Completed without a summary." if ok else "Agent failed."),
    )


def stream_process(
    command: list[str],
    stdin_text: str,
    timeout: int,
    on_line: Callable[[str], None],
) -> tuple[int, str]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        process.stdin.write(stdin_text)
        process.stdin.close()
    except (BrokenPipeError, OSError):
        pass

    for line in process.stdout:
        on_line(line.rstrip("\n"))

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        return 124, "agent exceeded its time limit"

    stderr = ""
    if process.stderr is not None:
        stderr = process.stderr.read()
    return process.returncode, stderr


def outcome_failed(detail: str, exit_code: int = 1) -> RunOutcome:
    return RunOutcome(exit_code=exit_code, error=detail)


def spec_model(spec: RunSpec, configured_model: str) -> str:
    return configured_model or ""
