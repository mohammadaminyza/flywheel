from datetime import UTC, datetime, timedelta

from flywheel.domain.enums import TaskStatus
from flywheel.domain.result import AgentResult
from flywheel.domain.run import Run
from flywheel.domain.task import Task
from flywheel.github.board import BoardService
from flywheel.github.issues import IssueService


class ClarificationService:
    def __init__(
        self,
        issues: IssueService,
        board: BoardService,
        timeout_hours: int = 48,
    ) -> None:
        self._issues = issues
        self._board = board
        self._timeout = timedelta(hours=timeout_hours)

    def park(self, run: Run, task: Task, result: AgentResult) -> str:
        mention = task.assignees or []
        comment_id = self._issues.ask(
            task.repository,
            task.issue_number,
            result.questions,
            agent=task.agent.value,
            mention=mention,
        )
        run.mark_awaiting_answer(comment_id)
        self._board.set_status(task, TaskStatus.NEEDS_INFO)
        return comment_id

    def pending_answer(self, run: Run, task: Task) -> str | None:
        if not run.pending_question_comment_id:
            return None
        reply = self._issues.human_reply_after(
            task.repository, task.issue_number, run.pending_question_comment_id
        )
        return reply.body.strip() if reply else None

    def has_timed_out(self, run: Run) -> bool:
        return datetime.now(UTC) - run.updated_at > self._timeout

    def abandon(self, run: Run, task: Task) -> None:
        hours = int(self._timeout.total_seconds() // 3600)
        self._issues.comment(
            task.repository,
            task.issue_number,
            f"No answer after {hours} hours, so this task has been moved to Blocked. "
            f"Reply here and move the card back to Todo to restart it.",
        )
        run.mark_failed(f"no answer to the agent's question within {hours} hours")
        self._board.set_status(task, TaskStatus.BLOCKED)
