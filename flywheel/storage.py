import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flywheel.domain.enums import AgentKind, RunPhase, RunStatus
from flywheel.domain.run import Run

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_item_id TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    repository TEXT NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    branch TEXT,
    session_id TEXT,
    pull_request_number INTEGER,
    preview_url TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    error TEXT,
    counts_toward_attempts INTEGER NOT NULL DEFAULT 1,
    pending_question_comment_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    project_item_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_item ON runs(project_item_id);
"""

_RUN_COLUMNS = (
    "id",
    "project_item_id",
    "issue_number",
    "repository",
    "agent",
    "status",
    "phase",
    "attempt",
    "branch",
    "session_id",
    "pull_request_number",
    "preview_url",
    "cost_usd",
    "error",
    "counts_toward_attempts",
    "pending_question_comment_id",
    "created_at",
    "updated_at",
)

MIGRATIONS = (
    ("runs", "counts_toward_attempts", "INTEGER NOT NULL DEFAULT 1"),
)


class Ledger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(SCHEMA)
        self._migrate()
        self._connection.commit()

    def _migrate(self) -> None:
        """Add columns a ledger written by an older version does not have yet."""
        for table, column, definition in MIGRATIONS:
            existing = {
                row["name"]
                for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing:
                self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self._connection.close()

    def claim(self, project_item_id: str, run_id: str) -> bool:
        try:
            self._connection.execute(
                "INSERT INTO claims (project_item_id, run_id, claimed_at) VALUES (?, ?, ?)",
                (project_item_id, run_id, datetime.now(UTC).isoformat()),
            )
        except sqlite3.IntegrityError:
            return False
        self._connection.commit()
        return True

    def release(self, project_item_id: str) -> None:
        self._connection.execute("DELETE FROM claims WHERE project_item_id = ?", (project_item_id,))
        self._connection.commit()

    def is_claimed(self, project_item_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM claims WHERE project_item_id = ?", (project_item_id,)
        ).fetchone()
        return row is not None

    def save(self, run: Run) -> None:
        run.touch()
        values = run.model_dump(mode="json")
        placeholders = ", ".join("?" for _ in _RUN_COLUMNS)
        columns = ", ".join(_RUN_COLUMNS)
        self._connection.execute(
            f"INSERT OR REPLACE INTO runs ({columns}) VALUES ({placeholders})",
            tuple(values[column] for column in _RUN_COLUMNS),
        )
        self._connection.commit()

    def get(self, run_id: str) -> Run | None:
        row = self._connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return Run(**dict(row)) if row else None

    def list_runs(self, limit: int = 100) -> list[Run]:
        rows = self._connection.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Run(**dict(row)) for row in rows]

    def by_status(self, *statuses: RunStatus) -> list[Run]:
        marks = ", ".join("?" for _ in statuses)
        rows = self._connection.execute(
            f"SELECT * FROM runs WHERE status IN ({marks}) ORDER BY created_at",
            tuple(status.value for status in statuses),
        ).fetchall()
        return [Run(**dict(row)) for row in rows]

    def active(self) -> list[Run]:
        return self.by_status(RunStatus.PENDING, RunStatus.RUNNING, RunStatus.REVIEWING)

    def awaiting_input(self) -> list[Run]:
        return self.by_status(RunStatus.NEEDS_INPUT)

    def active_count(self, agent: AgentKind | None = None) -> int:
        runs = self.active()
        if agent is None:
            return len(runs)
        return sum(1 for run in runs if run.agent == agent)

    def branch_is_active(self, repository: str, branch: str) -> bool:
        return any(
            run.repository == repository and run.branch == branch for run in self.active()
        )

    def attempts_for(self, project_item_id: str) -> int:
        """How many real attempts this card has spent.

        Runs that never reached the agent — a clone that failed, a restart, anything the
        user has since retried — are excluded, so infrastructure trouble cannot exhaust a
        card's budget.
        """
        row = self._connection.execute(
            "SELECT COALESCE(MAX(attempt), 0) AS attempts FROM runs "
            "WHERE project_item_id = ? AND counts_toward_attempts = 1",
            (project_item_id,),
        ).fetchone()
        return int(row["attempts"])

    def reset_attempts(self, project_item_id: str) -> int:
        """Give a card its full budget back, keeping every run in the history."""
        cursor = self._connection.execute(
            "UPDATE runs SET counts_toward_attempts = 0 WHERE project_item_id = ?",
            (project_item_id,),
        )
        self._connection.commit()
        return int(cursor.rowcount)

    def latest_for_item(self, project_item_id: str) -> Run | None:
        row = self._connection.execute(
            "SELECT * FROM runs WHERE project_item_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_item_id,),
        ).fetchone()
        return Run(**dict(row)) if row else None

    def recent_for_item(self, project_item_id: str, limit: int = 20) -> list[Run]:
        rows = self._connection.execute(
            "SELECT * FROM runs WHERE project_item_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_item_id, limit),
        ).fetchall()
        return [Run(**dict(row)) for row in rows]

    def append_event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        self._connection.execute(
            "INSERT INTO events (run_id, ts, kind, payload) VALUES (?, ?, ?, ?)",
            (run_id, datetime.now(UTC).isoformat(), kind, json.dumps(payload, default=str)),
        )
        self._connection.commit()

    def events(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT ts, kind, payload FROM events WHERE run_id = ? ORDER BY id DESC LIMIT ?",
            (run_id, limit),
        ).fetchall()
        return [
            {"ts": row["ts"], "kind": row["kind"], "payload": json.loads(row["payload"])}
            for row in reversed(rows)
        ]

    def recover_stale(self) -> int:
        rows = self._connection.execute(
            "SELECT id, project_item_id FROM runs WHERE status IN (?, ?)",
            (RunStatus.RUNNING.value, RunStatus.PENDING.value),
        ).fetchall()
        for row in rows:
            # A restart is not the agent's fault, so the card keeps its attempts.
            self._connection.execute(
                "UPDATE runs SET status = ?, phase = ?, error = ?, "
                "counts_toward_attempts = 0 WHERE id = ?",
                (
                    RunStatus.FAILED.value,
                    RunPhase.FINISHED.value,
                    "interrupted by factory restart",
                    row["id"],
                ),
            )
            self._connection.execute(
                "DELETE FROM claims WHERE project_item_id = ?", (row["project_item_id"],)
            )
        self._connection.commit()
        return len(rows)
