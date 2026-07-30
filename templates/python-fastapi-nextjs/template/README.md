# Project

A FastAPI backend and a Next.js App Router frontend, wired for Docker, GitHub Actions and
per-pull-request preview environments.

```
backend/     FastAPI service — route -> service -> aggregate/repository -> ORM
frontend/    Next.js App Router client — pages, hooks, every string through t(...)
.github/     CI, preview environments and tag-driven releases
```

## Run it locally

```bash
docker compose up --build          # backend on :8120, frontend on :3120
```

Or run each side on its own:

```bash
cd backend  && uv sync --all-extras && uv run uvicorn app.main:app --reload --port 8120
cd frontend && npm install && npm run dev
```

The API answers on `/api/v1/health`.

## Checks that must pass before anything ships

```bash
cd backend
uv run ruff check .
uv run mypy app
uv run pytest tests/unit -q
uv run pytest tests/integration -q

cd ../frontend
npm run lint
npx tsc --noEmit
npm run build
```

## Environment

Copy `backend/.env.example` to `backend/.env` and `frontend/.env.example` to
`frontend/.env.local`, then fill in the values for your machine.
