import threading
from collections.abc import Callable
from typing import Any

from flywheel import bootstrap
from flywheel.config import Settings

StatusSink = Callable[[str, dict[str, Any]], None]


class LoopController:
    def __init__(self, settings: Settings, status_sink: StatusSink | None = None) -> None:
        self._settings = settings
        self._status_sink = status_sink or (lambda kind, payload: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._container: bootstrap.Container | None = None
        self._error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="factory-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        self._thread = None

    def poke(self) -> None:
        self._wake.set()

    def _emit(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        self._status_sink("event", {"run_id": run_id, "kind": kind, **payload})

    def _run(self) -> None:
        try:
            self._container = bootstrap.build(self._settings, on_event=self._emit)
        except Exception as error:  # noqa: BLE001
            self._error = f"{type(error).__name__}: {error}"
            self._status_sink("error", {"error": self._error})
            return
        self._container.ledger.recover_stale()
        interval = self._settings.loop.poll_interval_seconds
        while not self._stop.is_set():
            try:
                runs = self._container.dispatcher.tick()
                self._status_sink("tick", {"count": len(runs)})
            except Exception as error:  # noqa: BLE001
                self._error = f"{type(error).__name__}: {error}"
                self._status_sink("error", {"error": self._error})
            self._wake.wait(timeout=interval)
            self._wake.clear()
        if self._container:
            self._container.close()
            self._container = None
