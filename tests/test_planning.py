from pathlib import Path
from typing import Any

import pytest

from flywheel.agents.claude_code import ClaudeCodeRunner
from flywheel.agents.codex import CodexRunner
from flywheel.agents.execution import ExecutionEnvironment
from flywheel.agents.prompt import build_backlog_refinement_prompt, build_project_plan_prompt
from flywheel.config import ProjectBriefSettings, Settings
from flywheel.domain.enums import AgentKind, ExecutionMode, TaskStatus, Transport
from flywheel.domain.project import ProjectConfig
from flywheel.domain.result import AgentResult, PlannedTask
from flywheel.domain.run import RunOutcome, RunSpec
from flywheel.domain.task import Repository, Task
from flywheel.domain.template import TemplateManifest
from flywheel.services.clarification_service import ClarificationService
from flywheel.services.dispatcher import (
    REFINED_MARKER,
    Dispatcher,
    mark_refined,
    merge_plans,
    needs_refinement,
)
from flywheel.services.exceptions import ValidationException
from flywheel.services.scaffold_service import ScaffoldService
from flywheel.storage import Ledger
from tests.test_dispatcher import (
    FakeBoard,
    FakeIssues,
    FakeProjects,
    FakePulls,
    FakeWorkspaceFactory,
)


def _planned(
    title: str, body: str = "body", branch: str = "", issue_number: int = 0
) -> PlannedTask:
    return PlannedTask(title=title, body=body, branch=branch, issue_number=issue_number)


def _board_task(
    number: int,
    title: str,
    body: str = "",
    branch: str = "",
    status: TaskStatus = TaskStatus.TODO,
) -> Task:
    return Task(
        project_item_id=f"ITEM_{number}",
        issue_number=number,
        issue_node_id=f"NODE_{number}",
        title=title,
        body=body,
        url=f"https://github.com/acme/api/issues/{number}",
        repository=Repository(owner="acme", name="api"),
        agent=AgentKind.CLAUDE_CODE,
        status=status,
        branch=branch or None,
    )


def _result(*tasks: PlannedTask, summary: str = "plan") -> AgentResult:
    return AgentResult(status="completed", summary=summary, planned_tasks=list(tasks))


class RecordingRunner:
    def __init__(self, agent: AgentKind, result: AgentResult | None) -> None:
        self._agent = agent
        self._result = result
        self.specs: list[RunSpec] = []

    def run(self, spec: RunSpec, on_event: Any) -> RunOutcome:
        self.specs.append(spec)
        if self._result is None:
            return RunOutcome(exit_code=1, error=f"{self._agent.value} failed")
        return RunOutcome(result=self._result, cost_usd=0.1)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    created = Settings(workspace_dir=str(tmp_path / "ws"))
    created.github.token = "tok"
    created.github.owner = "acme"
    created.github.project_number = 1
    return created


class RefinableIssues(FakeIssues):
    def __init__(self) -> None:
        super().__init__()
        self.updates: list[tuple[int, str, str]] = []

    def update(self, repository: Repository, issue_number: int, title: str, body: str) -> dict:
        self.updates.append((issue_number, title, body))
        return {"number": issue_number}


class RefinableBoard(FakeBoard):
    def __init__(self, tasks: list[Task]) -> None:
        super().__init__(tasks)
        self.texts: list[tuple[str, str, str]] = []

    def set_text(self, item_id: str, field_name: str, value: str, create: bool = False) -> None:
        self.texts.append((item_id, field_name, value))


def _dispatcher(
    settings: Settings,
    tmp_path: Path,
    runners: dict[AgentKind, RecordingRunner],
    board: object | None = None,
    issues: object | None = None,
) -> Dispatcher:
    issues = issues or FakeIssues()
    board = board or FakeBoard([])
    return Dispatcher(
        settings=settings,
        ledger=Ledger(tmp_path / "ledger.db"),
        board=board,  # type: ignore[arg-type]
        issues=issues,  # type: ignore[arg-type]
        pulls=FakePulls(),  # type: ignore[arg-type]
        projects=FakeProjects(),  # type: ignore[arg-type]
        workspaces=FakeWorkspaceFactory(tmp_path / "workspaces"),  # type: ignore[arg-type]
        clarification=ClarificationService(issues, board, timeout_hours=48),  # type: ignore[arg-type]
        scaffold=ScaffoldService(tmp_path / "templates"),
        runner_factory=lambda _run, task, *_rest: runners[task.agent],  # type: ignore[arg-type,return-value]
    )


def test_merged_plan_keeps_the_richest_task_and_ranks_agreement_first() -> None:
    merged = merge_plans(
        [
            (
                AgentKind.CLAUDE_CODE,
                _result(_planned("Checkout flow", "short"), _planned("Only Claude saw this")),
            ),
            (
                AgentKind.CODEX,
                _result(
                    _planned("checkout   FLOW", "a much longer body with criteria", "feat/x"),
                    _planned("Only Codex saw this"),
                ),
            ),
        ]
    )

    titles = [task.title for task in merged.planned_tasks]

    assert len(merged.planned_tasks) == 3
    assert titles[0] == "checkout   FLOW"
    assert merged.planned_tasks[0].body == "a much longer body with criteria"
    assert merged.planned_tasks[0].branch == "feat/x"
    assert set(titles[1:]) == {"Only Claude saw this", "Only Codex saw this"}
    assert "claude-code" in merged.summary and "codex" in merged.summary


def test_a_single_agent_plan_is_returned_as_it_is() -> None:
    merged = merge_plans([(AgentKind.CODEX, _result(_planned("One"), summary="just codex"))])

    assert merged.planned_tasks[0].title == "One"
    assert merged.summary == "codex: just codex"


def test_planning_runs_every_selected_agent_with_research_enabled(
    settings: Settings, tmp_path: Path
) -> None:
    runners = {
        AgentKind.CLAUDE_CODE: RecordingRunner(AgentKind.CLAUDE_CODE, _result(_planned("A"))),
        AgentKind.CODEX: RecordingRunner(AgentKind.CODEX, _result(_planned("A"), _planned("B"))),
    }
    dispatcher = _dispatcher(settings, tmp_path, runners)

    result = dispatcher.plan_project(
        ProjectBriefSettings(repository="acme/api", purpose="sell things")
    )

    assert [task.title for task in result.planned_tasks] == ["A", "B"]
    for agent, runner in runners.items():
        assert len(runner.specs) == 1, agent
        assert runner.specs[0].research is True
        assert "Deep research" in runner.specs[0].prompt


def test_planning_survives_one_agent_failing(settings: Settings, tmp_path: Path) -> None:
    runners = {
        AgentKind.CLAUDE_CODE: RecordingRunner(AgentKind.CLAUDE_CODE, None),
        AgentKind.CODEX: RecordingRunner(AgentKind.CODEX, _result(_planned("Only survivor"))),
    }
    dispatcher = _dispatcher(settings, tmp_path, runners)

    result = dispatcher.plan_project(ProjectBriefSettings(repository="acme/api"))

    assert [task.title for task in result.planned_tasks] == ["Only survivor"]


def test_planning_reports_when_every_agent_fails(settings: Settings, tmp_path: Path) -> None:
    runners = {
        AgentKind.CLAUDE_CODE: RecordingRunner(AgentKind.CLAUDE_CODE, None),
        AgentKind.CODEX: RecordingRunner(AgentKind.CODEX, None),
    }
    dispatcher = _dispatcher(settings, tmp_path, runners)

    with pytest.raises(ValidationException) as error:
        dispatcher.plan_project(ProjectBriefSettings(repository="acme/api"))

    assert "claude-code failed" in str(error.value)
    assert "codex failed" in str(error.value)


def test_grooming_rewrites_the_github_issues_it_read_from_the_board(
    settings: Settings, tmp_path: Path
) -> None:
    settings.project_brief.purpose = "sell things"
    board = RefinableBoard(
        [_board_task(7, "checkout"), _board_task(8, "shipping"), _board_task(9, "done already")]
    )
    board._tasks[2].body = f"already sharp\n\n{REFINED_MARKER}\n"
    issues = RefinableIssues()
    improved = _result(
        _planned("Checkout validation", "scope + criteria", "feat/checkout", issue_number=7),
        _planned("Shipping rules", "scope + criteria", "feat/checkout", issue_number=8),
    )
    runners = {
        AgentKind.CLAUDE_CODE: RecordingRunner(AgentKind.CLAUDE_CODE, improved),
        AgentKind.CODEX: RecordingRunner(AgentKind.CODEX, improved),
    }
    dispatcher = _dispatcher(settings, tmp_path, runners, board=board, issues=issues)

    refined = dispatcher.refine_backlog()

    assert [item["issue_number"] for item in refined] == [7, 8]
    assert [update[0] for update in issues.updates] == [7, 8]
    assert issues.updates[0][1] == "Checkout validation"
    assert "scope + criteria" in issues.updates[0][2]
    assert REFINED_MARKER in issues.updates[0][2]
    # Tasks that must ship together get the same workstream on the board.
    assert board.texts == [
        ("ITEM_7", "Branch", "feat/checkout"),
        ("ITEM_8", "Branch", "feat/checkout"),
    ]
    # The already-groomed card was never sent to the agents.
    prompt = runners[AgentKind.CODEX].specs[0].prompt
    assert "Issue #9" not in prompt
    assert "sell things" in prompt


def test_a_card_the_agents_left_alone_is_still_marked_so_it_is_not_reread(
    settings: Settings, tmp_path: Path
) -> None:
    board = RefinableBoard([_board_task(7, "already precise", "nothing to add")])
    issues = RefinableIssues()
    runners = {
        AgentKind.CLAUDE_CODE: RecordingRunner(AgentKind.CLAUDE_CODE, _result()),
        AgentKind.CODEX: RecordingRunner(AgentKind.CODEX, _result()),
    }
    dispatcher = _dispatcher(settings, tmp_path, runners, board=board, issues=issues)

    refined = dispatcher.refine_backlog()

    assert refined[0]["changed"] is False
    assert issues.updates[0][1] == "already precise"
    assert issues.updates[0][2] == f"nothing to add\n\n{REFINED_MARKER}\n"


def test_grooming_runs_before_a_card_is_claimed(settings: Settings, tmp_path: Path) -> None:
    board = RefinableBoard([_board_task(7, "checkout")])
    issues = RefinableIssues()
    improved = _result(_planned("Checkout validation", "criteria", issue_number=7))
    runners = {
        AgentKind.CLAUDE_CODE: RecordingRunner(AgentKind.CLAUDE_CODE, improved),
        AgentKind.CODEX: RecordingRunner(AgentKind.CODEX, improved),
    }
    dispatcher = _dispatcher(settings, tmp_path, runners, board=board, issues=issues)

    dispatcher._auto_refine()

    assert issues.updates[0][0] == 7


def test_grooming_can_be_switched_off(settings: Settings, tmp_path: Path) -> None:
    settings.planning.auto_refine = False
    board = RefinableBoard([_board_task(7, "checkout")])
    issues = RefinableIssues()
    dispatcher = _dispatcher(settings, tmp_path, {}, board=board, issues=issues)

    dispatcher._auto_refine()

    assert issues.updates == []


def test_a_failing_grooming_pass_never_stops_the_factory(
    settings: Settings, tmp_path: Path
) -> None:
    board = RefinableBoard([_board_task(7, "checkout")])
    runners = {
        AgentKind.CLAUDE_CODE: RecordingRunner(AgentKind.CLAUDE_CODE, None),
        AgentKind.CODEX: RecordingRunner(AgentKind.CODEX, None),
    }
    dispatcher = _dispatcher(settings, tmp_path, runners, board=board, issues=RefinableIssues())

    dispatcher._auto_refine()  # must not raise


def test_only_a_batch_of_cards_is_groomed_per_cycle(settings: Settings, tmp_path: Path) -> None:
    settings.planning.refine_batch_size = 2
    board = RefinableBoard([_board_task(number, f"task {number}") for number in range(1, 6)])
    issues = RefinableIssues()
    improved = _result(
        _planned("One", "criteria", issue_number=1), _planned("Two", "criteria", issue_number=2)
    )
    runners = {
        AgentKind.CLAUDE_CODE: RecordingRunner(AgentKind.CLAUDE_CODE, improved),
        AgentKind.CODEX: RecordingRunner(AgentKind.CODEX, improved),
    }
    dispatcher = _dispatcher(settings, tmp_path, runners, board=board, issues=issues)

    refined = dispatcher.refine_backlog()

    assert [item["issue_number"] for item in refined] == [1, 2]


def test_marker_detection_is_idempotent() -> None:
    once = mark_refined("body")
    twice = mark_refined(once)

    assert once == twice
    assert needs_refinement(_board_task(1, "t", "body")) is True
    assert needs_refinement(_board_task(1, "t", once)) is False
    assert needs_refinement(_board_task(1, "t", "body", status=TaskStatus.IN_PROGRESS)) is False


def test_research_prompt_carries_the_backlog_and_the_brief() -> None:
    prompt = build_backlog_refinement_prompt(
        "acme/api",
        [_board_task(7, "Checkout", "reject empty carts", branch="feat/checkout")],
        purpose="sell things",
        constraints="no php",
        research=True,
    )

    assert "Issue #7: Checkout" in prompt
    assert "reject empty carts" in prompt
    assert "feat/checkout" in prompt
    assert "sell things" in prompt and "no php" in prompt
    assert "Deep research" in prompt


def test_only_the_requested_agents_are_used(settings: Settings, tmp_path: Path) -> None:
    runners = {
        AgentKind.CLAUDE_CODE: RecordingRunner(AgentKind.CLAUDE_CODE, _result(_planned("A"))),
        AgentKind.CODEX: RecordingRunner(AgentKind.CODEX, _result(_planned("B"))),
    }
    dispatcher = _dispatcher(settings, tmp_path, runners)

    dispatcher.plan_project(
        ProjectBriefSettings(repository="acme/api"), agents=[AgentKind.CODEX], research=False
    )

    assert runners[AgentKind.CLAUDE_CODE].specs == []
    assert runners[AgentKind.CODEX].specs[0].research is False
    assert "Deep research" not in runners[AgentKind.CODEX].specs[0].prompt


def test_a_bad_repository_name_is_rejected_before_any_agent_runs(
    settings: Settings, tmp_path: Path
) -> None:
    dispatcher = _dispatcher(settings, tmp_path, {})

    with pytest.raises(ValidationException):
        dispatcher.plan_project(ProjectBriefSettings(repository="not-a-repository"))


def _spec(tmp_path: Path, research: bool) -> RunSpec:
    return RunSpec(
        run_id="plan-1",
        task=Task(
            project_item_id="ITEM",
            issue_number=0,
            issue_node_id="NODE",
            title="Plan",
            body="",
            url="https://github.com/acme/api",
            repository=Repository(owner="acme", name="api"),
            agent=AgentKind.CLAUDE_CODE,
            status=TaskStatus.TODO,
        ),
        project=ProjectConfig(),
        template=TemplateManifest(),
        workspace=tmp_path / "workspace",
        prompt="plan it",
        system_prompt="system",
        agent=AgentKind.CLAUDE_CODE,
        transport=Transport.CLI,
        research=research,
    )


def _environment(tmp_path: Path) -> ExecutionEnvironment:
    return ExecutionEnvironment(
        mode=ExecutionMode.HOST, workspace=tmp_path / "workspace", run_dir=tmp_path / "run"
    )


def test_claude_only_opens_the_web_when_researching(tmp_path: Path) -> None:
    runner = ClaudeCodeRunner(_environment(tmp_path))

    researching = runner.build_command(_spec(tmp_path, True))
    delivering = runner.build_command(_spec(tmp_path, False))

    assert researching[researching.index("--allowed-tools") + 1] == "WebSearch WebFetch"
    assert "--allowed-tools" not in delivering


def test_codex_only_opens_the_web_when_researching(tmp_path: Path) -> None:
    runner = CodexRunner(_environment(tmp_path))

    researching = runner.build_command(_spec(tmp_path, True))
    delivering = runner.build_command(_spec(tmp_path, False))

    assert "tools.web_search=true" in researching
    assert researching[-1] == "-"
    assert "tools.web_search=true" not in delivering


def test_planning_prompt_carries_the_brief_and_the_research_rules() -> None:
    plan = build_project_plan_prompt("acme/api", "sell", "grow", "fast", "no php", True)
    without = build_project_plan_prompt("acme/api", "sell", "grow", "fast", "no php", False)

    assert "sell" in plan and "no php" in plan
    assert "Deep research" in plan and "web search" in plan
    assert "Deep research" not in without
