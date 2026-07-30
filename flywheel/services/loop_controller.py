import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from flywheel import bootstrap
from flywheel.config import Settings

StatusSink = Callable[[str, dict[str, Any]], None]

EVENT_HISTORY = 60


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class LoopController:
    """Owns the background poll loop and everything the loop panel needs to show it."""

    def __init__(self, settings: Settings, status_sink: StatusSink | None = None) -> None:
        self._settings = settings
        self._status_sink = status_sink or (lambda kind, payload: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._settings_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending_settings: Settings | None = None
        self._container: bootstrap.Container | None = None
        self._error: str | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=EVENT_HISTORY)
        self._ticks = 0
        self._started_at: str | None = None
        self._last_tick_at: str | None = None
        self._last_tick: dict[str, Any] = {}
        self._busy = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stopping(self) -> bool:
        """The thread is finishing the cycle it already started."""
        return self.running and self._stop.is_set()

    @property
    def error(self) -> str | None:
        return self._error

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "running": self.running,
                "stopping": self.stopping,
                "busy": self._busy,
                "error": self._error,
                "ticks": self._ticks,
                "started_at": self._started_at,
                "last_tick_at": self._last_tick_at,
                "last_tick": dict(self._last_tick),
                "poll_interval_seconds": self._settings.loop.poll_interval_seconds,
                "events": list(self._events)[::-1],
            }

    def start(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            # A stop that is still draining a long cycle is cancelled here rather than
            # racing a second thread onto the same board.
            self._stop.clear()
            self.poke()
            thread.join(timeout=0.1)
            if thread.is_alive():
                return
            self._thread = None
        self._stop.clear()
        self._wake.clear()
        self._error = None
        with self._state_lock:
            self._started_at = _now()
        self._thread = threading.Thread(target=self._run, name="factory-loop", daemon=True)
        self._thread.start()
        self._record("loop_started", {})

    def stop(self, wait_seconds: float = 5.0) -> bool:
        """Ask the loop to stop. Returns True once the thread is actually gone.

        A cycle that is running an agent can take minutes. The thread is kept — and
        reported as still running — rather than dropped, so a later start can never
        leave two loops dispatching the same board.
        """
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=wait_seconds)
        if thread.is_alive():
            self._record("loop_stopping", {"detail": "finishing the current cycle"})
            return False
        self._thread = None
        self._record("loop_stopped", {})
        return True

    def poke(self) -> None:
        self._wake.set()

    def reconfigure(self, settings: Settings) -> None:
        """Apply saved settings before the next poll without interrupting active work."""
        with self._settings_lock:
            self._pending_settings = settings
        self._wake.set()

    def _apply_pending_settings(self) -> None:
        with self._settings_lock:
            settings = self._pending_settings
            self._pending_settings = None
        if settings is None:
            return
        if self._container:
            self._container.close()
            self._container = None
        self._settings = settings
        self._record("reconfigured", {"poll_interval": settings.loop.poll_interval_seconds})

    def _record(self, kind: str, payload: dict[str, Any]) -> None:
        entry = {"at": _now(), "kind": kind, **payload}
        with self._state_lock:
            self._events.append(entry)
        self._status_sink(kind, entry)

    def _emit(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        entry = {"at": _now(), "run_id": run_id, "kind": kind, **payload}
        with self._state_lock:
            self._events.append(entry)
        self._status_sink("event", entry)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._apply_pending_settings()
            interval = max(5, self._settings.loop.poll_interval_seconds)
            if self._container is None and not self._build():
                self._sleep(interval)
                continue
            self._cycle()
            self._sleep(interval)
        if self._container:
            self._container.close()
            self._container = None

    def _build(self) -> bool:
        try:
            self._container = bootstrap.build(self._settings, on_event=self._emit)
            self._container.ledger.recover_stale()
            self._error = None
            return True
        except Exception as error:  # noqa: BLE001
            self._error = f"{type(error).__name__}: {error}"
            self._record("error", {"error": self._error})
            return False

    def _cycle(self) -> None:
        assert self._container is not None
        with self._state_lock:
            self._busy = True
        try:
            runs = self._container.dispatcher.tick()
            deliveries = self._container.delivery.poll_all()
            summary = {"count": len(runs), "deliveries": len(deliveries)}
            with self._state_lock:
                self._ticks += 1
                self._last_tick_at = _now()
                self._last_tick = summary
            self._error = None
            self._status_sink("tick", {**summary, "at": self._last_tick_at})
        except Exception as error:  # noqa: BLE001
            self._error = f"{type(error).__name__}: {error}"
            self._record("error", {"error": self._error})
        finally:
            with self._state_lock:
                self._busy = False

    def _sleep(self, interval: int) -> None:
        self._wake.wait(timeout=interval)
        self._wake.clear()
