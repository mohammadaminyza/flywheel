import threading

import pytest

from flywheel.config import Settings
from flywheel.domain.enums import AgentKind
from flywheel.services.loop_controller import LoopController


def test_reconfigure_rebuilds_the_factory_with_latest_agent(monkeypatch: object) -> None:
    from flywheel import bootstrap

    rebuilt = threading.Event()
    built_agents: list[AgentKind] = []

    class Component:
        def tick(self) -> list[object]:
            return []

        def poll_all(self) -> list[object]:
            return []

    class Ledger:
        def recover_stale(self) -> None:
            return None

    class Container:
        dispatcher = Component()
        delivery = Component()
        ledger = Ledger()

        def close(self) -> None:
            return None

    def build(settings: Settings, on_event: object = None) -> Container:
        built_agents.append(settings.default_agent)
        if settings.default_agent == AgentKind.CLAUDE_CODE:
            rebuilt.set()
        return Container()

    monkeypatch.setattr(bootstrap, "build", build)  # type: ignore[attr-defined]
    original = Settings(default_agent=AgentKind.CODEX)
    original.loop.poll_interval_seconds = 60
    controller = LoopController(original)
    controller.start()
    try:
        updated = Settings(default_agent=AgentKind.CLAUDE_CODE)
        updated.loop.poll_interval_seconds = 60
        controller.reconfigure(updated)

        assert rebuilt.wait(timeout=2)
        assert built_agents[:2] == [AgentKind.CODEX, AgentKind.CLAUDE_CODE]
    finally:
        controller.stop()


def _settings(interval: int = 60) -> Settings:
    settings = Settings()
    settings.loop.poll_interval_seconds = interval
    return settings


def _container(dispatcher_tick: object) -> object:
    class Delivery:
        def poll_all(self) -> list[object]:
            return []

    class LedgerStub:
        def recover_stale(self) -> None:
            return None

    class Container:
        dispatcher = dispatcher_tick
        delivery = Delivery()
        ledger = LedgerStub()

        def close(self) -> None:
            return None

    return Container()


def test_status_reports_cycles_and_events(monkeypatch: pytest.MonkeyPatch) -> None:
    from flywheel import bootstrap

    ticked = threading.Event()

    class Dispatcher:
        def tick(self) -> list[object]:
            ticked.set()
            return []

    monkeypatch.setattr(
        bootstrap, "build", lambda settings, on_event=None: _container(Dispatcher())
    )
    controller = LoopController(_settings())
    controller.start()
    try:
        assert ticked.wait(timeout=2)
        for _ in range(50):
            if controller.status()["ticks"]:
                break
            threading.Event().wait(0.02)
        status = controller.status()

        assert status["running"] is True
        assert status["ticks"] >= 1
        assert status["last_tick_at"]
        assert status["poll_interval_seconds"] == 60
        assert any(event["kind"] == "loop_started" for event in status["events"])
    finally:
        controller.stop()


def test_a_build_failure_is_reported_without_killing_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flywheel import bootstrap

    def build(settings: Settings, on_event: object = None) -> object:
        raise RuntimeError("no board")

    monkeypatch.setattr(bootstrap, "build", build)
    settings = _settings(interval=5)
    controller = LoopController(settings)
    controller.start()
    try:
        for _ in range(100):
            if controller.error:
                break
            threading.Event().wait(0.02)

        assert controller.error == "RuntimeError: no board"
        assert controller.running is True
    finally:
        controller.stop()


def test_stopping_during_a_long_cycle_keeps_one_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from flywheel import bootstrap

    entered = threading.Event()
    release = threading.Event()
    ticks: list[int] = []

    class Dispatcher:
        def tick(self) -> list[object]:
            ticks.append(1)
            entered.set()
            release.wait(timeout=5)
            return []

    monkeypatch.setattr(
        bootstrap, "build", lambda settings, on_event=None: _container(Dispatcher())
    )
    controller = LoopController(_settings())
    controller.start()
    try:
        assert entered.wait(timeout=2)

        assert controller.stop(wait_seconds=0.2) is False
        assert controller.stopping is True

        # Starting again while the old cycle drains resumes that loop instead of
        # spawning a second one onto the same board.
        controller.start()
        release.set()

        assert controller.running is True
        assert controller.stopping is False
        assert len([t for t in threading.enumerate() if t.name == "factory-loop"]) == 1
        assert len(ticks) == 1
    finally:
        release.set()
        controller.stop()
