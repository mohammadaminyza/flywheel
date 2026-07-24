import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flywheel.agents.base import AgentRunner
from flywheel.agents.claude_code import ClaudeCodeRunner
from flywheel.agents.codex import CodexRunner
from flywheel.agents.execution import ExecutionEnvironment
from flywheel.agents.prompt import build_review_prompt, build_system_prompt, build_task_prompt
from flywheel.config import Settings
from flywheel.delivery.telegram import TelegramNotifier
from flywheel.domain.enums import (
    AgentKind,
    ExecutionMode,
    RunPhase,
    RunStatus,
    TaskStatus,
)
from flywheel.domain.project import ProjectConfig
from flywheel.domain.result import AgentResult
from flywheel.domain.run import Run, RunOutcome, RunSpec
from flywheel.domain.task import Task
from flywheel.domain.template import TemplateManifest, load_repository_template
from flywheel.github.board import BoardService
from flywheel.github.issues import IssueService
from flywheel.github.pulls import PullRequestService
from flywheel.mcp.registry import McpRegistry
from flywheel.probes import credential_mounts, find_executable
from flywheel.services.clarification_service import ClarificationService
from flywheel.services.project_service import ProjectService
from flywheel.services.scaffold_service import ScaffoldService
from flywheel.services.task_reporter import TaskReporter
from flywheel.storage import Ledger
from flywheel.workspace import Workspace, WorkspaceFactory

EventSink = Callable[[str, str, dict[str, Any]], None]


class Dispatcher:
    def __init__(
        self,
        settings: Settings,
        ledger: Ledger,
        board: BoardService,
        issues: IssueService,
        pulls: PullRequestService,
        projects: ProjectService,
        workspaces: WorkspaceFactory,
        clarification: ClarificationService,
        scaffold: ScaffoldService,
        reporter: TaskReporter | None = None,
        on_event: EventSink | None = None,
        runner_factory: Callable[..., AgentRunner] | None = None,
    ) -> None:
        self._runner_factory = runner_factory
        self._settings = settings
        self._ledger = ledger
        self._board = board
        self._issues = issues
        self._pulls = pulls
        self._projects = projects
        self._workspaces = workspaces
        self._clarification = clarification
        self._scaffold = scaffold
        self._reporter = reporter or TaskReporter(issues, TelegramNotifier(settings.telegram))
        self._on_event = on_event or (lambda run_id, kind, payload: None)

    def tick(self) -> list[Run]:
        touched = self._resume_answered()
        touched += self._recover_invalid_base()
        touched += self._start_new()
        return touched

    def _emit(self, run: Run, kind: str, payload: dict[str, Any]) -> None:
        self._ledger.append_event(run.id, kind, payload)
        self._on_event(run.id, kind, payload)

    def _capacity(self) -> int:
        return max(0, self._settings.runner.max_parallel - self._ledger.active_count())

    def _agent_capacity(self, agent: AgentKind) -> int:
        limit = self._settings.runner.max_parallel_per_agent
        return max(0, limit - self._ledger.active_count(agent))

    def _tasks_by_item(self) -> dict[str, Task]:
        return {task.project_item_id: task for task in self._board.tasks()}

    def _resume_answered(self) -> list[Run]:
        parked = self._ledger.awaiting_input()
        if not parked:
            return []
        tasks = self._tasks_by_item()
        resumed: list[Run] = []
        for run in parked:
            task = tasks.get(run.project_item_id)
            if task is None:
                continue
            answer = self._clarification.pending_answer(run, task)
            if answer:
                self._emit(run, "answered", {"answer": answer[:500]})
                self._reporter.resumed(task, run)
                resumed.append(self.execute(task, run=run, answer=answer))
            elif self._clarification.has_timed_out(run):
                self._clarification.abandon(run, task)
                self._ledger.save(run)
                self._ledger.release(run.project_item_id)
                resumed.append(run)
        return resumed

    @staticmethod
    def _is_invalid_pull_request_base(error: str | None) -> bool:
        detail = (error or "").lower()
        return all(token in detail for token in ("pull", "422", "base", "invalid"))

    def _recover_invalid_base(self) -> list[Run]:
        latest: dict[str, Run] = {}
        for candidate in self._ledger.list_runs(300):
            latest.setdefault(candidate.project_item_id, candidate)
        recoverable = [
            run
            for run in latest.values()
            if run.status == RunStatus.FAILED
            and run.branch
            and self._is_invalid_pull_request_base(run.error)
        ]
        if not recoverable:
            return []

        tasks = self._tasks_by_item()
        recovered: list[Run] = []
        for run in recoverable:
            task = tasks.get(run.project_item_id)
            if task is None or run.branch is None:
                continue
            workspace: Workspace | None = None
            try:
                workspace = self._workspaces.prepare(task.repository, f"{run.id}-delivery")
                base = workspace.default_branch()
                self._reporter.progress(
                    task,
                    "Repairing pull request delivery",
                    "The implementation is already pushed. Flywheel is repairing the missing "
                    "base branch and will retry the pull request without rerunning the agent.",
                )
                if base == run.branch:
                    base = "main"
                    workspace.recover_missing_base(base, run.branch)

                result = AgentResult(
                    status="completed",
                    summary=(
                        "Recovered the completed implementation after repairing the repository's "
                        "missing pull-request base branch."
                    ),
                    branch=run.branch,
                )
                pull = self._open_pull_request(task, result, run, run.branch, base)
                run.mark_succeeded(pull["number"])
                self._board.set_status(task, TaskStatus.IN_REVIEW)
                self._ledger.save(run)
                self._emit(
                    run,
                    "pull_request_recovered",
                    {"number": pull["number"], "url": pull["html_url"], "base": base},
                )
                self._reporter.succeeded(task, run, result, str(pull["html_url"]))
                recovered.append(run)
            except Exception as error:  # noqa: BLE001
                run.error = f"delivery recovery failed: {type(error).__name__}: {error}"
                self._ledger.save(run)
                self._emit(run, "delivery_recovery_failed", {"error": run.error})
            finally:
                if workspace is not None:
                    workspace.destroy()
        return recovered

    def _start_new(self) -> list[Run]:
        if self._capacity() <= 0:
            return []
        started: list[Run] = []
        for task in self._board.claimable():
            if self._capacity() <= 0:
                break
            if self._agent_capacity(task.agent) <= 0:
                continue
            if self._ledger.is_claimed(task.project_item_id):
                continue
            attempts = self._ledger.attempts_for(task.project_item_id)
            if attempts >= self._settings.loop.max_attempts:
                continue
            run = Run(
                id=uuid.uuid4().hex,
                project_item_id=task.project_item_id,
                issue_number=task.issue_number,
                repository=task.repository.full_name,
                agent=task.agent,
                attempt=attempts + 1,
            )
            if not self._ledger.claim(task.project_item_id, run.id):
                continue
            self._ledger.save(run)
            self._reporter.started(task, run, self._settings.loop.max_attempts)
            started.append(self.execute(task, run=run))
        return started

    def execute(
        self,
        task: Task,
        run: Run,
        answer: str | None = None,
        previous_failure: str | None = None,
    ) -> Run:
        run.mark_running()
        run.advance(RunPhase.PREPARING)
        self._ledger.save(run)
        workspace: Workspace | None = None
        try:
            self._board.set_status(task, TaskStatus.IN_PROGRESS)
            workspace = self._workspaces.prepare(task.repository, run.id)
            project = self._projects.load(task.repository)
            is_empty = workspace.is_empty_repository
            base = workspace.default_branch() if not is_empty else "main"

            if is_empty:
                run.advance(RunPhase.SCAFFOLDING)
                self._ledger.save(run)
                self._scaffold.apply(
                    workspace.path,
                    project.template or task.template_id or self._settings.default_template,
                )
                self._emit(run, "scaffolded", {"template": self._settings.default_template})
                selected_template = (
                    project.template or task.template_id or self._settings.default_template
                )
                self._reporter.progress(
                    task,
                    "Project scaffolded",
                    f"Applied the `{selected_template}` template. "
                    "The agent is now implementing the issue.",
                )
                workspace.create_branch(base)
                workspace.commit_all("chore: initialize project scaffold")
                workspace.push(base)
                self._reporter.progress(
                    task,
                    "Base branch initialized",
                    f"Created and pushed `{base}`. Feature implementation is starting now.",
                )

            template = load_repository_template(workspace.path)
            branch = task.branch_name
            workspace.create_branch(branch)
            run.branch = branch
            run.advance(RunPhase.IMPLEMENTING)
            self._ledger.save(run)

            outcome = self._invoke(
                run, task, project, template, workspace, is_empty, branch, answer, previous_failure
            )
            run.record_cost(outcome.cost_usd)
            run.session_id = outcome.session_id or run.session_id
            self._ledger.save(run)

            result = outcome.result
            if result is None or not outcome.ok:
                return self._fail(run, task, outcome.error or "the agent produced no result")

            if result.needs_input and result.questions:
                self._clarification.park(run, task, result)
                self._ledger.save(run)
                self._reporter.question(task, result)
                self._emit(
                    run, "needs_input", {"questions": [q.question for q in result.questions]}
                )
                return run

            if result.status == "failed":
                return self._fail(run, task, result.summary or "the agent reported failure")

            if project.review.enabled:
                run.advance(RunPhase.REVIEWING)
                run.status = RunStatus.REVIEWING
                self._ledger.save(run)
                self._reporter.progress(
                    task,
                    "Self-review started",
                    "Implementation finished. A second adversarial pass is reviewing the diff.",
                )
                review = self._invoke_review(run, task, project, template, workspace, base)
                run.record_cost(review.cost_usd)
                self._ledger.save(run)

            if workspace.has_changes():
                workspace.commit_all(f"chore: finalise work for #{task.issue_number}")

            if workspace.commits_ahead(base) == 0:
                return self._fail(run, task, "the agent committed nothing")

            run.advance(RunPhase.PUSHING)
            self._ledger.save(run)
            self._reporter.progress(
                task,
                "Publishing the change",
                f"The implementation is complete. Branch `{branch}` is being pushed.",
            )
            workspace.push(branch)

            run.advance(RunPhase.OPENING_PR)
            pull = self._open_pull_request(task, result, run, branch, base)
            run.mark_succeeded(pull["number"])
            self._board.set_status(task, TaskStatus.IN_REVIEW)
            self._ledger.save(run)
            self._emit(run, "pull_request", {"number": pull["number"], "url": pull["html_url"]})
            self._reporter.succeeded(task, run, result, str(pull["html_url"]))
            return run
        except Exception as error:  # noqa: BLE001
            return self._fail(run, task, f"{type(error).__name__}: {error}")
        finally:
            if workspace is not None and run.status != RunStatus.NEEDS_INPUT:
                workspace.destroy()

    def _invoke(
        self,
        run: Run,
        task: Task,
        project: ProjectConfig,
        template: TemplateManifest,
        workspace: Workspace,
        is_empty: bool,
        branch: str,
        answer: str | None,
        previous_failure: str | None,
    ) -> RunOutcome:
        system_prompt = build_system_prompt(template, project)
        prompt = build_task_prompt(
            task, template, project, is_empty, branch, previous_failure, answer
        )
        spec = self._spec(run, task, project, template, workspace, prompt, system_prompt)
        if answer and run.session_id:
            spec.resume_session_id = run.session_id
        runner = self._runner_for(run, task, project, workspace, spec)
        self._emit(run, "agent_start", {"agent": task.agent.value, "attempt": run.attempt})
        return runner.run(spec, lambda kind, payload: self._emit(run, kind, payload))

    def _invoke_review(
        self,
        run: Run,
        task: Task,
        project: ProjectConfig,
        template: TemplateManifest,
        workspace: Workspace,
        base: str,
    ) -> RunOutcome:
        system_prompt = build_system_prompt(template, project)
        prompt = build_review_prompt(task, template, base)
        spec = self._spec(run, task, project, template, workspace, prompt, system_prompt)
        spec.resume_session_id = run.session_id
        runner = self._runner_for(run, task, project, workspace, spec)
        self._emit(run, "review_start", {})
        return runner.run(spec, lambda kind, payload: self._emit(run, kind, payload))

    def _spec(
        self,
        run: Run,
        task: Task,
        project: ProjectConfig,
        template: TemplateManifest,
        workspace: Workspace,
        prompt: str,
        system_prompt: str,
    ) -> RunSpec:
        agent_settings = self._settings.agent(task.agent)
        return RunSpec(
            run_id=run.id,
            task=task,
            project=project,
            template=template,
            workspace=workspace.path,
            prompt=prompt,
            system_prompt=system_prompt,
            agent=task.agent,
            transport=agent_settings.transport,
            max_turns=agent_settings.max_turns,
            timeout_seconds=self._settings.runner.timeout_seconds,
        )

    def _run_directory(self, run: Run) -> Path:
        path = self._settings.workspace_path / f"{run.id}-run"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _runner_for(
        self,
        run: Run,
        task: Task,
        project: ProjectConfig,
        workspace: Workspace,
        spec: RunSpec,
    ) -> AgentRunner:
        if self._runner_factory is not None:
            return self._runner_factory(run, task, project, workspace, spec)
        run_dir = self._run_directory(run)
        (run_dir / "system.md").write_text(spec.system_prompt, encoding="utf-8")

        registry = McpRegistry(
            repository=task.repository.full_name,
            github_token=self._settings.github.token,
            project_servers=project.mcp,
            secrets={"GITHUB_TOKEN": self._settings.github.token},
        )
        agent_settings = self._settings.agent(task.agent)
        if task.agent == AgentKind.CODEX:
            registry.write_codex(run_dir / "codex" / "config.toml")
        else:
            registry.write_claude(run_dir / "mcp.json")

        environment = ExecutionEnvironment(
            mode=self._settings.runner.execution_mode,
            image=self._settings.runner.image,
            workspace=workspace.path,
            run_dir=run_dir,
            env=self._agent_env(task.agent),
            mounts=credential_mounts()
            if self._settings.runner.execution_mode == ExecutionMode.CONTAINER
            else {},
        )
        in_container = environment.mode == ExecutionMode.CONTAINER
        if task.agent == AgentKind.CODEX:
            executable = "codex" if in_container else (find_executable("codex") or "codex")
            return CodexRunner(environment, executable=executable, model=agent_settings.model)
        executable = "claude" if in_container else (find_executable("claude") or "claude")
        return ClaudeCodeRunner(environment, executable=executable, model=agent_settings.model)

    def _agent_env(self, agent: AgentKind) -> dict[str, str]:
        settings = self._settings.agent(agent)
        env = {"GITHUB_PERSONAL_ACCESS_TOKEN": self._settings.github.token}
        if agent == AgentKind.CLAUDE_CODE and settings.api_key:
            env["ANTHROPIC_API_KEY"] = settings.api_key
        if agent == AgentKind.CODEX and settings.api_key:
            env["CODEX_API_KEY"] = settings.api_key
        return env

    def _open_pull_request(
        self, task: Task, result: AgentResult, run: Run, branch: str, base: str
    ) -> dict[str, Any]:
        existing = self._pulls.find_open(task.repository, branch)
        if existing:
            return existing
        agent_settings = self._settings.agent(task.agent)
        body = self._pulls.body_for(
            task.issue_number, result, task.agent.value, agent_settings.model, run.cost_usd
        )
        return self._pulls.open(
            task.repository,
            head=branch,
            base=base,
            title=f"{task.branch_prefix}: {task.title}",
            body=body,
        )

    def _fail(self, run: Run, task: Task, detail: str) -> Run:
        run.mark_failed(detail)
        self._ledger.save(run)
        self._ledger.release(run.project_item_id)
        self._emit(run, "failed", {"error": detail})
        attempts_left = self._settings.loop.max_attempts - run.attempt
        self._reporter.failed(
            task,
            run,
            detail,
            self._settings.loop.max_attempts,
            will_retry=attempts_left > 0,
        )
        if attempts_left <= 0:
            self._board.set_status(task, TaskStatus.BLOCKED)
        else:
            self._board.set_status(task, TaskStatus.TODO)
        return run
