from typing import Any

from fastapi import FastAPI, HTTPException

from flywheel.config import load_settings
from flywheel.storage import Ledger


def create_app() -> FastAPI:
    app = FastAPI(title="Flywheel", version="0.1.0")
    settings = load_settings()

    def ledger() -> Ledger:
        return Ledger(settings.database_path)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/runs")
    def runs(limit: int = 50) -> list[dict[str, Any]]:
        store = ledger()
        try:
            return [run.model_dump(mode="json") for run in store.list_runs(limit)]
        finally:
            store.close()

    @app.get("/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        store = ledger()
        try:
            run = store.get(run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="run not found")
            return run.model_dump(mode="json")
        finally:
            store.close()

    @app.get("/runs/{run_id}/events")
    def run_events(run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        store = ledger()
        try:
            return store.events(run_id, limit)
        finally:
            store.close()

    @app.get("/questions")
    def questions() -> list[dict[str, Any]]:
        store = ledger()
        try:
            return [run.model_dump(mode="json") for run in store.awaiting_input()]
        finally:
            store.close()

    return app


app = create_app()
