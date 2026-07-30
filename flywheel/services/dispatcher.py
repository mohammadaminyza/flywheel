import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flywheel.agents.base import AgentRunner
from flywheel.agents.claude_code import ClaudeCodeRunner
from flywheel.agents.codex import CodexRunner
from flywheel.agents.execution import ExecutionEnvironment
from flywheel.agents.prompt import (
    PLANNING_SYSTEM_PROMPT,
    REFINEMENT_SYSTEM_PROMPT,
    build_backlog_refinement_prompt,
    build_project_plan_prompt,
    build_review_prompt,
    build_system_prompt,
    build_task_prompt,
)
from flywheel.config import ProjectBriefSettings, Settings
from flywheel.delivery.telegram import TelegramNotifier
from flywheel.domain.enums import (
    AgentKind,
    ExecutionMode,
    RunPhase,
    RunStatus,
    TaskStatus,
)
from flywheel.domain.project import ProjectConfig
from flywheel.domain.result import AgentResult, PlannedTask
from flywheel.domain.run import Run, RunOutcome, RunSpec
from flywheel.domain.task import Repository, Task
from flywheel.domain.template import (
    TemplateManifest,
    describe_repository,
    has_code,
    load_repository_template,
)
from flywheel.github.actions import ActionsService
from flywheel.github.board import BoardService
from flywheel.github.issues import IssueService
from flywheel.github.pulls import PullRequestService
from flywheel.mcp.registry import McpRegistry
from flywheel.probes import credential_mounts, find_executable
from flywheel.services.clarification_service import ClarificationService
from flywheel.services.exceptions import ValidationException
from flywheel.services.project_service import ProjectService
from flywheel.services.scaffold_service import ScaffoldService
from flywheel.services.task_reporter import TaskReporter
from flywheel.storage import Ledger
from flywheel.workspace import Workspace, WorkspaceFactory

EventSink = Callable[[str, str, dict[str, Any]], None]

REFINED_MARKER = "<!-- flywheel:refined -->"

INFRASTRUCTURE_FAILURES = (
    "workspaceexception",
    "could not clone",
    "destination path",
    "interrupted by factory restart",
    "could not free a workspace",
    "docker",
    "connection refused",
    "temporary failure in name resolution",
    "read timed out",
    "network error",
    "429 too many requests",
    "502 bad gateway",
    "503 service unavailable",
)


def is_infrastructure_failure(detail: str) -> bool:
    """Did the run fail before the agent could do anything about it?

    These are the machine's problems — a locked workspace, a restart, a rate limit — and a
    card must not spend one of its attempts on them.
    """
    haystack = detail.casefold()
    return any(marker in haystack for marker in INFRASTRUCTURE_FAILURES)


def needs_refinement(task: Task) -> bool:
    """A card is groomed once. Delete the marker line in the issue to have it done again."""
    return task.status == TaskStatus.TODO and REFINED_MARKER not in task.body


def mark_refined(body: str) -> str:
    if REFINED_MARKER in body:
        return body
    return f"{body.rstrip()}\n\n{REFINED_MARKER}\n"


PROJECT_CONFIG_PATH = Path(".flywheel") / "project.yml"


def record_template(workspace: Path, template_id: str) -> None:
    """Write down which template a scaffolded repository follows.

    Once the skeleton is committed the repository has code, so later runs would otherwise
    read it as somebody else's project and stop applying the template's rules.
    """
    path = workspace / PROJECT_CONFIG_PATH
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Written by Flywheel when it scaffolded this repository.\n"
        "# It is what tells the factory which template's rules this project follows.\n"
        f"template: {template_id}\n",
        encoding="utf-8",
    )


def _by_repository(tasks: list[Task]) -> dict[str, list[Task]]:
    grouped: dict[str, list[Task]] = {}
    for task in tasks:
        grouped.setdefault(task.repository.full_name, []).append(task)
    return grouped


def _repository_from(value: str) -> Repository:
    parts = [part.strip() for part in value.split("/", 1)]
    if len(parts) != 2 or not all(parts):
        raise ValidationException("repository must be in owner/name format")
    return Repository(owner=parts[0], name=parts[1])


def _plan_key(task: PlannedTask) -> str:
    return " ".join(task.title.casefold().split())


def merge_plans(results: list[tuple[AgentKind, AgentResult]]) -> AgentResult:
    """Fold every agent's plan into one, keeping the richest version of each task.

    Two agents researching the same brief overlap heavily. Tasks with the same title are
    the same task, so the longest body wins — that is the one with the most acceptance
    criteria — while everything only one agent thought of is kept as well.
    """
    if len(results) == 1:
        agent, single = results[0]
        return single.model_copy(update={"summary": f"{agent.value}: {single.summary}".strip()})

    merged: dict[str, PlannedTask] = {}
    proposals: dict[str, list[str]] = {}
    summaries: list[str] = []
    follow_ups: list[str] = []
    questions = []
    for agent, result in results:
        if result.summary.strip():
            summaries.append(f"**{agent.value}** — {result.summary.strip()}")
        follow_ups += [item for item in result.follow_ups if item not in follow_ups]
        questions += result.questions
        for planned in result.planned_tasks:
            key = _plan_key(planned)
            proposals.setdefault(key, []).append(agent.value)
            existing = merged.get(key)
            if existing is None or len(planned.body) > len(existing.body):
                merged[key] = planned.model_copy(
                    update={"branch": planned.branch or (existing.branch if existing else "")}
                )
    agreed = [key for key, agents in proposals.items() if len(agents) > 1]
    ordered = [merged[key] for key in agreed] + [
        task for key, task in merged.items() if key not in agreed
    ]
    header = (
        f"Merged the plans of {', '.join(agent.value for agent, _ in results)}: "
        f"{len(ordered)} tasks, {len(agreed)} of them proposed by more than one agent."
    )
    return AgentResult(
        status="completed",
        summary="\n\n".join([header, *summaries]),
        questions=questions,
        follow_ups=follow_ups,
        planned_tasks=ordered,
    )


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
        actions: ActionsService | None = None,
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
        self._actions = actions
        self._reporter = reporter or TaskReporter(issues, TelegramNotifier(settings.telegram))
        self._on_event = on_event or (lambda run_id, kind, payload: None)

    def tick(self) -> list[Run]:
        touched = self._resume_answered()
        touched += self._recover_invalid_base()
        self._auto_refine()
        touched += self._start_new()
        return touched

    def _auto_refine(self) -> None:
        """Groom new cards before anyone starts building them."""
        if not self._settings.planning.auto_refine:
            return
        try:
            self.refine_backlog()
        except Exception as error:  # noqa: BLE001
            # A failed grooming pass must never stop the factory from doing the work.
            self._emit_raw("refinement_failed", {"error": f"{type(error).__name__}: {error}"})

    def plan_project(
        self,
        brief: ProjectBriefSettings,
        agents: list[AgentKind] | None = None,
        research: bool | None = None,
    ) -> AgentResult:
        """Research the repository with every selected agent and merge one plan out of it."""
        repository = _repository_from(brief.repository)
        deep_research = self._settings.planning.deep_research if research is None else research
        prompt = build_project_plan_prompt(
            repository.full_name,
            brief.purpose,
            brief.goals,
            brief.client_needs,
            brief.constraints,
            deep_research,
        )
        results = self._research(
            repository,
            title="Review project and generate plan",
            body=brief.purpose,
            prompt=prompt,
            system_prompt=PLANNING_SYSTEM_PROMPT,
            agents=agents,
            research=deep_research,
        )
        merged = merge_plans(results)
        if not merged.planned_tasks:
            raise ValidationException("the planning agents returned no tasks")
        return merged

    def refine_backlog(
        self,
        agents: list[AgentKind] | None = None,
        research: bool | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read the board's open tasks, improve them with the agents, write them back.

        This is the automatic pass the loop runs before it claims anything: the agents read
        the repository and the product brief, sharpen every card that has not been groomed
        yet, and the rewritten text goes straight back onto the GitHub issue.
        """
        pending = self._refinable_tasks(limit)
        if not pending:
            return []
        brief = self._settings.project_brief
        deep_research = self._settings.planning.deep_research if research is None else research
        refined: list[dict[str, Any]] = []
        for repository_name, tasks in _by_repository(pending).items():
            repository = _repository_from(repository_name)
            prompt = build_backlog_refinement_prompt(
                repository.full_name,
                tasks,
                brief.purpose,
                brief.goals,
                brief.client_needs,
                brief.constraints,
                deep_research,
            )
            results = self._research(
                repository,
                title=f"Groom {len(tasks)} open task(s)",
                body=brief.purpose,
                prompt=prompt,
                system_prompt=REFINEMENT_SYSTEM_PROMPT,
                agents=agents,
                research=deep_research,
            )
            merged = merge_plans(results)
            refined += self._apply_refinements(tasks, merged)
        return refined

    def _refinable_tasks(self, limit: int | None = None) -> list[Task]:
        batch = limit if limit is not None else self._settings.planning.refine_batch_size
        pending = [task for task in self._board.tasks() if needs_refinement(task)]
        return pending[: max(1, batch)]

    def _apply_refinements(self, tasks: list[Task], plan: AgentResult) -> list[dict[str, Any]]:
        by_number = {task.issue_number: task for task in tasks}
        by_title = {task.title.casefold().strip(): task for task in tasks}
        applied: list[dict[str, Any]] = []
        for planned in plan.planned_tasks:
            task = by_number.get(planned.issue_number) or by_title.get(
                planned.title.casefold().strip()
            )
            if task is None:
                continue
            applied.append(self._write_refinement(task, planned))
            by_number.pop(task.issue_number, None)
        for untouched in by_number.values():
            # Marked as groomed anyway: a card the agents left alone was already precise,
            # and re-reading it every cycle would burn the same tokens forever.
            applied.append(self._write_refinement(untouched, None))
        return applied

    def _write_refinement(self, task: Task, planned: PlannedTask | None) -> dict[str, Any]:
        title = (planned.title.strip() if planned else "") or task.title
        body = (planned.body.strip() if planned else "") or task.body
        branch = planned.branch.strip() if planned else ""
        changed = planned is not None and (title != task.title or body.strip() != task.body.strip())
        self._issues.update(task.repository, task.issue_number, title, mark_refined(body))
        if branch and branch != (task.branch or ""):
            self._board.set_text(
                task.project_item_id, self._settings.github.branch_field, branch, create=True
            )
        record = {
            "issue_number": task.issue_number,
            "repository": task.repository.full_name,
            "title": title,
            "branch": branch or (task.branch or ""),
            "changed": changed,
        }
        self._emit_raw("refined", record)
        return record

    def _emit_raw(self, kind: str, payload: dict[str, Any]) -> None:
        identifier = f"backlog-{payload.get('repository', 'board')}"
        self._ledger.append_event(identifier, kind, payload)
        self._on_event(identifier, kind, payload)

    def _research(
        self,
        repository: Repository,
        title: str,
        body: str,
        prompt: str,
        system_prompt: str,
        agents: list[AgentKind] | None,
        research: bool,
    ) -> list[tuple[AgentKind, AgentResult]]:
        selected = self._settings.planning.selected(agents)
        run_id = f"plan-{uuid.uuid4().hex}"
        workspace = self._workspaces.prepare(repository, run_id)
        collected: list[tuple[AgentKind, AgentResult]] = []
        failures: list[str] = []
        try:
            project = self._projects.load(repository)
            template = self._template_for(workspace, repository.name, project.template)
            for agent in selected:
                task = Task(
                    project_item_id=run_id,
                    issue_number=0,
                    issue_node_id=run_id,
                    title=title,
                    body=body,
                    url=f"https://github.com/{repository.full_name}",
                    repository=repository,
                    agent=agent,
                    status=TaskStatus.TODO,
                )
                run = Run(
                    id=f"{run_id}-{agent.value}",
                    project_item_id=run_id,
                    issue_number=0,
                    repository=repository.full_name,
                    agent=agent,
                    branch=workspace.default_branch(),
                )
                spec = self._spec(
                    run, task, project, template, workspace, prompt, system_prompt
                )
                spec.research = research
                spec.max_turns = self._settings.planning.max_turns or spec.max_turns
                runner = self._runner_for(run, task, project, workspace, spec)
                try:
                    outcome = runner.run(spec, lambda _kind, _payload: None)
                except Exception as error:  # noqa: BLE001
                    failures.append(f"{agent.value}: {type(error).__name__}: {error}")
                    continue
                if not outcome.ok or outcome.result is None:
                    failures.append(f"{agent.value}: {outcome.error or 'produced no result'}")
                    continue
                collected.append((agent, outcome.result))
        finally:
            workspace.destroy()
        if not collected:
            raise ValidationException("; ".join(failures) or "no planning agent produced a result")
        return collected

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

                result = self._recorded_result(run) or AgentResult(
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

    def _recorded_result(self, run: Run) -> AgentResult | None:
        for event in reversed(self._ledger.events(run.id)):
            if event["kind"] != "agent_result":
                continue
            try:
                return AgentResult.model_validate(event["payload"])
            except ValueError:
                return None
        return None

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
            branch = task.branch_name
            if self._ledger.branch_is_active(task.repository.full_name, branch):
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
                branch=branch,
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
            if self._actions is not None and self._settings.deploy.configured:
                self._actions.configure_deploy(task.repository, self._settings.deploy)
                self._reporter.progress(
                    task,
                    "Preview configuration synchronized",
                    "Docker host credentials and the preview domain were synchronized to "
                    "GitHub Actions secrets for this repository.",
                )
            preferred_base = self._settings.github.base_branch
            has_commits = not workspace.is_empty_repository
            base = workspace.resolve_base(preferred_base) if has_commits else (
                preferred_base or "main"
            )
            declared_template = project.template or task.template_id
            scaffolded = self._scaffold_if_needed(run, task, workspace, declared_template, base)

            template = self._template_for(workspace, task.repository.name, declared_template)
            self._emit(
                run,
                "conventions",
                {"template": template.id, "discovered": template.discovered, "base": base},
            )
            branch = task.branch_name
            workspace.create_branch(branch, base)
            run.branch = branch
            run.advance(RunPhase.IMPLEMENTING)
            self._ledger.save(run)

            outcome = self._invoke(
                run,
                task,
                project,
                template,
                workspace,
                scaffolded,
                branch,
                answer,
                previous_failure,
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

            self._emit(run, "agent_result", result.model_dump(mode="json"))

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
                if not review.ok or review.result is None:
                    return self._fail(run, task, review.error or "self-review produced no result")
                if review.result.status == "failed":
                    return self._fail(
                        run,
                        task,
                        review.result.summary or "self-review rejected the implementation",
                    )

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

    def _scaffold_if_needed(
        self,
        run: Run,
        task: Task,
        workspace: Workspace,
        declared_template: str | None,
        base: str,
    ) -> bool:
        """Lay down a template only when the repository has nothing of its own.

        A repository that already contains an application is never scaffolded and never
        restructured — its own code is the template from here on.
        """
        if has_code(workspace.path):
            return False
        template_id = declared_template or self._settings.default_template
        run.advance(RunPhase.SCAFFOLDING)
        self._ledger.save(run)
        self._scaffold.apply(workspace.path, template_id)
        record_template(workspace.path, template_id)
        self._emit(run, "scaffolded", {"template": template_id, "base": base})

        if workspace.is_empty_repository:
            # Nothing has ever been committed: the scaffold becomes the base branch itself.
            workspace.create_branch(base)
            workspace.commit_all("chore: initialize project scaffold")
            workspace.push(base)
            self._reporter.progress(
                task,
                "Project scaffolded",
                f"The repository was empty, so the `{template_id}` template was committed and "
                f"pushed as `{base}`. Feature implementation is starting now.",
            )
            return True

        # The repository exists but holds no application yet (a README, a licence). The
        # template rides in on the feature branch and arrives through the pull request
        # rather than being pushed over anyone's base branch.
        self._reporter.progress(
            task,
            "Project scaffolded",
            f"The repository had no application code, so the `{template_id}` template was "
            f"added on the feature branch and will arrive with the pull request into `{base}`.",
        )
        return True

    def _template_for(
        self, workspace: Workspace, repository_name: str, declared_template: str | None
    ) -> TemplateManifest:
        """Whose conventions win in this repository.

        In order: rules the repository keeps itself, then the template it was built from,
        and finally — for a codebase the factory did not create — the codebase itself.
        """
        owned = load_repository_template(workspace.path)
        if owned.found:
            return owned
        if declared_template or not has_code(workspace.path):
            template_id = declared_template or self._settings.default_template
            # Guidance is the factory's instruction to the agent, not the client's source,
            # so it is staged in the workspace on every run and never committed.
            self._scaffold.apply_guidance(workspace.path, template_id)
            staged = load_repository_template(workspace.path)
            if staged.found:
                return staged
        return describe_repository(workspace.path, repository_name)

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

        global_servers = {
            name: spec for name, spec in self._settings.mcp_servers.items() if spec.enabled
        }
        project_servers = {name: spec for name, spec in project.mcp.items() if spec.enabled}
        registry = McpRegistry(
            repository=task.repository.full_name,
            github_token=self._settings.github.token,
            project_servers={**global_servers, **project_servers},
            secrets={"GITHUB_TOKEN": self._settings.github.token},
        )
        agent_settings = self._settings.agent(task.agent)
        if task.agent == AgentKind.CODEX:
            # Written as the run's record of what Codex was given; Codex itself is
            # configured through the overrides below, the only form it actually reads.
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
            return CodexRunner(
                environment,
                executable=executable,
                model=agent_settings.model,
                config_overrides=registry.codex_overrides(),
            )
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
            return self._pulls.link_issue(
                task.repository,
                existing,
                task.issue_number,
                result.summary,
            )
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
        infrastructure = is_infrastructure_failure(detail)
        streak = self._infrastructure_streak(run.project_item_id, run.id)
        retry_budget = self._settings.loop.max_infrastructure_retries
        if infrastructure and streak < retry_budget:
            # The agent never got to work, so this cannot be one of its attempts.
            run.counts_toward_attempts = False
        self._ledger.save(run)
        self._ledger.release(run.project_item_id)
        self._emit(
            run,
            "failed",
            {
                "error": detail,
                "infrastructure": infrastructure,
                "retried": not run.counts_toward_attempts,
            },
        )
        if not run.counts_toward_attempts:
            self._reporter.progress(
                task,
                "Retrying after an environment problem",
                f"The task never reached the agent: {detail}\n\n"
                f"It keeps all {self._settings.loop.max_attempts} of its attempts and is "
                f"queued again (environment retry {streak + 1} of {retry_budget}).",
            )
            self._board.set_status(task, TaskStatus.TODO)
            return run

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

    def _infrastructure_streak(self, project_item_id: str, current_run_id: str) -> int:
        """How many times in a row this card already failed before reaching the agent.

        The run being failed right now is excluded — it is still marked running in the
        ledger, and counting it would end the streak at zero every time.
        """
        streak = 0
        for run in self._ledger.recent_for_item(project_item_id):
            if run.id == current_run_id:
                continue
            if run.status != RunStatus.FAILED or not is_infrastructure_failure(run.error or ""):
                break
            streak += 1
        return streak

    def retry(self, task: Task) -> Run | None:
        """Hand a card its full budget back and put it in front of the loop again."""
        self._ledger.reset_attempts(task.project_item_id)
        self._ledger.release(task.project_item_id)
        self._board.set_status(task, TaskStatus.TODO)
        latest = self._ledger.latest_for_item(task.project_item_id)
        self._emit_raw(
            "retry_requested",
            {
                "issue_number": task.issue_number,
                "repository": task.repository.full_name,
                "previous_error": latest.error if latest else None,
            },
        )
        return latest
