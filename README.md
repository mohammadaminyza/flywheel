# Flywheel

An autonomous software factory. It watches a GitHub Projects board, and for every card marked
**Todo** it checks out the repository, writes the feature and its tests inside an isolated
container, reviews its own work, opens a pull request, deploys a live preview environment, and
sends you the link and screenshots on Telegram.

If the agent hits a decision it cannot make safely, it stops and asks you in the issue comments —
then resumes the *same* conversation once you reply.

```
GitHub Projects board
        |
        v
   [ factory ] --claims a card--> container --> branch --> code + tests --> self-review
        |                                                                        |
        |                                                                        v
        |<--------------- pull request <-- push <------------------------- committed work
        v
  GitHub Actions --> preview env (per pull request) --> screenshots --> PR comment + Telegram
                 --> tags --> stage / production
```

---

## Table of contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Install](#install)
- [First run](#first-run)
- [Setting up your board](#setting-up-your-board)
- [The `.template/` folder](#the-template-folder)
- [Per-project configuration](#per-project-configuration)
- [How a task flows](#how-a-task-flows)
- [When the agent asks a question](#when-the-agent-asks-a-question)
- [CI/CD: branches, tags and environments](#cicd-branches-tags-and-environments)
- [Agents, authentication and MCP](#agents-authentication-and-mcp)
- [Command reference](#command-reference)
- [Configuration reference](#configuration-reference)
- [Project layout](#project-layout)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

---

## What it does

- **Reads work from a real board.** GitHub Projects v2, using your existing columns and custom
  fields. An `Agent` field on each card decides who does the work.
- **Runs the agents you already pay for.** It drives the Claude Code and Codex CLIs installed on
  your machine, signed in with your existing subscriptions. No API keys required.
- **Isolates every task.** One disposable Docker container per card, with the repository mounted
  and credentials injected — never baked into an image.
- **Knows your architecture.** Each repository carries a `.template/` folder with its rules, code
  samples and architectural tests. The agent reads it before writing anything, and the tests
  reject work that breaks the structure.
- **Scaffolds empty repositories.** Point it at a repository with no commits and it lays down a
  complete working project first, then builds the feature on top.
- **Reviews itself.** A second adversarial pass over its own diff before the pull request opens.
- **Asks instead of guessing.** Ambiguity becomes a question on the issue, not a wrong assumption.
- **Ships previews.** Every pull request gets its own environment at a real URL; tags promote to
  stage and production.
- **Never loses a task.** A SQLite ledger records every run, its cost, its session and its
  outcome, and recovers cleanly from a restart.

## Requirements

| Requirement | Why | Checked by |
| --- | --- | --- |
| Docker | Isolates each task | `flywheel doctor` |
| Claude Code, signed in | The default agent | `flywheel doctor` |
| Codex, signed in | Optional second agent | `flywheel doctor` |
| A GitHub token with `repo` + `project` | Reads the board, opens pull requests | `flywheel doctor` |
| Python 3.11+ and [uv](https://docs.astral.sh/uv/) | Runs the factory | — |
| A Docker host + wildcard DNS | Optional, for preview URLs | `flywheel doctor` |
| A Telegram bot | Optional, for notifications | `flywheel doctor` |

## Install

```bash
git clone https://github.com/mohammadaminyza/flywheel.git
cd flywheel
uv sync
uv run flywheel build-runner     # builds the container agents run in (~10 minutes, once)
```

## First run

The factory has three faces, all sharing one config and one ledger — use whichever you like:

```bash
uv run flywheel gui     # graphical app in your browser (easiest)
uv run flywheel         # terminal UI (TUI)
uv run flywheel doctor  # headless, for scripts and CI
```

Launcher scripts are provided so you never have to think about environments:

- **Windows** — double-click **`gui.bat`** (browser app) or **`run.bat`** (terminal app)
- **Linux / macOS / Git-Bash** — `./gui.sh` or `./run.sh`

They all `cd` to the project and use its own `uv` environment, so the active conda/system Python
doesn't matter.

If the terminal UI's text boxes don't accept input in your console (some legacy Windows consoles
don't support it), use the browser app or configure from any terminal with plain prompts:

```bash
uv run flywheel login     # paste your token (hidden), it's verified and saved
uv run flywheel board     # lists your boards, pick one by number
```

Whichever you pick, a **setup wizard** walks you through GitHub token, board, agents, isolation
mode, default template, preview host and notifications — testing each as you go and writing
`~/.flywheel/config.toml` for you. Nothing is edited by hand.

> **Always launch via `uv run flywheel ...`** (or the `.bat`/`.ps1` scripts). Running
> `python main.py` uses whatever Python is active — e.g. conda base — which does not have the
> app's dependencies and fails with `ModuleNotFoundError`. `uv run` uses the project's `.venv`.

To check everything at any time:

```bash
uv run flywheel doctor
```

```
+----------------------------- Flywheel readiness -----------------------------+
|    OK    Docker              daemon 29.6.1                                  |
|    OK    Claude Code         2.1.206 (subscription login)                   |
|    OK    Codex               0.145.0 (ChatGPT login)                        |
|    OK    GitHub token        authenticated as mohammadaminyza               |
|    OK    Project board       Factory Board                                  |
+-----------------------------------------------------------------------------+
```

Every failure comes with the exact command that fixes it.

## Setting up your board

Create a GitHub Projects v2 board and give it these fields:

| Field | Type | Values |
| --- | --- | --- |
| `Status` | single select | `Todo`, `In Progress`, `In Review`, `Needs Info`, `Blocked`, `Done` |
| `Agent` | single select | `claude-code`, `codex` |
| `Template` | text | optional; the template for an empty repository |

All six `Status` options must exist — the factory moves cards between them, and `flywheel doctor`
tells you which are missing.

Then: add an issue to the board, set `Status = Todo` and `Agent = claude-code`, and start the
loop. Write issues the way you would for a capable new colleague — the title and body are the
entire brief, so acceptance criteria pay for themselves.

```bash
uv run flywheel run-once      # one pass, useful the first time
uv run flywheel loop          # keep going
```

## The `.template/` folder

**This is where you teach the factory your architecture.** It lives inside each target
repository, is read fresh on every single run, and is meant to change as the project evolves.

```
.template/
├── template.yml            # commands, ports, health check, environments
├── README.md               # the architectural rules, in prose
├── samples/                # a complete vertical slice to imitate
│   ├── README.md
│   ├── backend/            # entity -> repository -> service -> schema -> route + tests
│   └── frontend/           # hooks and a page, with i18n
└── architecture-tests/     # tests that enforce the rules automatically
```

Each part does a different job:

- **`README.md`** is injected into the agent's system prompt verbatim. Write the rules you would
  give a new engineer: layering, error handling, naming, what never to do.
- **`samples/`** is the reference the agent copies. Prose describes the rules; samples show them.
  A tree of the sample structure plus excerpts of the sample files go into the prompt.
- **`architecture-tests/`** are the enforcement. Rules that are only prose get bent; rules with a
  failing test do not. The bundled set catches routes importing repositories, branching in a
  route, services raising `HTTPException`, repositories raising at all, dataclasses in the domain,
  lazy imports, and schemas leaking outside the router.
- **`template.yml`** tells the factory the commands to run:

```yaml
commands:
  install:   { backend: uv sync --all-extras, frontend: npm ci }
  lint:      { backend: uv run ruff check ., frontend: npm run lint }
  typecheck: { backend: uv run mypy app,     frontend: npx tsc --noEmit }
  test_unit: { backend: uv run pytest tests/unit -q }
  test_integration:  { backend: uv run pytest tests/integration -q }
  test_architecture: { backend: uv run pytest ../.template/architecture-tests -q }
```

The agent must make **all** of these pass before it may report success.

Because the factory only reads `template.yml`, it is stack-agnostic. An ASP.NET repository just
supplies `dotnet build`, `dotnet test` and NetArchTest-based architectural tests — no changes to
the factory. A repository with no `.template/` still works; the agent falls back to reading the
existing code and matching it.

## Per-project configuration

Optional, in `.factory/project.yml` in the target repository:

```yaml
template: python-fastapi-nextjs     # used only when the repository is empty
instructions: |
  This is a stock forecasting product. Prefer explicit code over clever code.
rules:
  - Never call the pricing API from a request handler
  - All money values are integer minor units
default_agent: claude-code
mcp:                                 # extra MCP servers for the agent
  postgres:
    command: postgres-mcp
    env: { DATABASE_URL: "${DATABASE_URL}" }
review:
  enabled: true
  max_attempts: 3
deploy:
  domain: example.com
```

## How a task flows

1. **Claim.** The card moves to `In Progress`. A SQLite ledger guarantees one run per card even
   if two polls overlap.
2. **Prepare.** The repository is cloned into a fresh workspace. If it has no commits, the
   bootstrap template is laid down first.
3. **Read the rules.** `.template/` is loaded from the checkout, so the agent always gets the
   current architecture, not a cached copy.
4. **Implement.** The agent works in the container on `feat/<issue>-<slug>` (or `fix/`, `chore/`,
   from the issue's labels), writing code and tests and running lint, types and the full suite
   until they pass.
5. **Review.** A second pass re-reads the diff adversarially and fixes what it finds.
6. **Pull request.** The branch is pushed and a pull request opened, linked with `Closes #n` and
   listing the tests added, the agent, the model and the cost. The card moves to `In Review`.
7. **Deploy and report.** GitHub Actions builds, deploys the preview, captures screenshots, and
   comments the link. Telegram gets the same.

Failures return the card to `Todo` and the next attempt receives the failure log, so it addresses
the cause instead of repeating itself. After `max_attempts` the card moves to `Blocked` and the
issue gets a comment explaining why.

## When the agent asks a question

Ambiguity is the main way autonomous coding wastes an afternoon, so the agent is instructed to
stop rather than guess. When it does:

1. It ends its run with `status: needs_input` and its questions.
2. The factory posts them to the issue, @-mentioning the assignee, and moves the card to
   `Needs Info`. Telegram gets a copy.
3. The run parks in the ledger **with its session id**.
4. When you reply in the issue, the factory resumes that exact session — `claude --resume` /
   `codex exec resume` — so the agent keeps its full context instead of starting over.

Multiple rounds are fine. After 48 hours without an answer the card moves to `Blocked`.

## CI/CD: branches, tags and environments

GitHub Actions, shipped inside the template:

| Workflow | Trigger | Result |
| --- | --- | --- |
| `ci.yml` | pull request, push to `main` | lint, types, unit, integration and architectural tests, both apps |
| `preview.yml` | pull request opened / updated | builds images, deploys stack `pr-<n>`, waits for health, screenshots with Playwright, comments the link |
| `release.yml` | tag | `v*-rc.*` / `v*-stage` → **stage**; `v*.*.*` / `v*-prod` → **production**, behind an environment approval |
| `preview-teardown.yml` | pull request closed | removes the stack, volumes and images |

Environments follow the port convention:

| Environment | Trigger | URL | Ports |
| --- | --- | --- | --- |
| dev | every pull request | `pr-<n>.dev.<domain>` | 8120 / 3120 |
| stage | `v1.2.0-rc.1`, `v1.2.0-stage` | `stage.<domain>` | 8110 / 3110 |
| prod | `v1.2.0`, `v1.2.0-prod` | `<domain>` | 8100 / 3100 |

All three use the same `docker-compose.deploy.yml` with a different `COMPOSE_PROJECT_NAME`, so
what you test in a preview is what reaches production.

Repository secrets required: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`; variable: `DOMAIN`.
The host needs Docker and Traefik with a wildcard certificate for `*.dev.<domain>`.

## Agents, authentication and MCP

**Authentication.** The factory uses your existing logins — `~/.claude/.credentials.json` and
`~/.codex/auth.json` — bind-mounted into the container. API keys work too but are not required.

> Because a subscription credential file is rewritten when its token refreshes, subscription mode
> runs **one task per agent at a time** to avoid a refresh race. Raise `max_parallel_per_agent`
> only when using API keys.

**Transports.** Codex can be driven either as a CLI (`codex exec`) or over MCP (`codex mcp-server`
exposes `codex()` / `codex-reply()`, a real agent loop). Claude Code is driven as a CLI —
`claude mcp serve` exposes its *tools* to a client rather than executing a task, so it is not a
task-execution transport. Both sit behind one interface; the board's `Agent` field is all you set.

**MCP servers for the agent.** Every run gets the GitHub MCP server (scoped to that repository)
and Playwright, plus anything in your project's `mcp:` block, written out as `.mcp.json` for
Claude and `config.toml` for Codex. `${VAR}` placeholders resolve from the factory's secrets.

## Command reference

| Command | What it does |
| --- | --- |
| `flywheel gui` | Opens the graphical app in your browser (setup, dashboard, questions) |
| `factory` | Opens the terminal UI — wizard on first run, dashboard after |
| `flywheel login` | Save a GitHub token from any terminal (prompts securely, verifies it) |
| `flywheel board` | List your boards and pick one from any terminal |
| `flywheel setup` | Re-runs the setup wizard |
| `flywheel doctor` | Checks every dependency and prints the fix for each failure |
| `flywheel run-once` | One pass over the board |
| `flywheel loop` | Polls continuously (`--interval` in seconds) |
| `flywheel runs` | Recent runs with status, phase and cost |
| `flywheel serve` | Runs the status API (needs `uv sync --extra api`) |
| `flywheel build-runner` | Builds the agent container image |
| `flywheel config-path` | Prints the config file location |

Inside the TUI dashboard: **s** starts/pauses the loop, **a** opens the questions inbox (answer
an agent's question and it resumes on the next poll), **r** refreshes, **w** re-runs setup.

## Configuration reference

`~/.flywheel/config.toml`, written by the wizard.

| Setting | Default | Meaning |
| --- | --- | --- |
| `github.owner` / `github.project_number` | — | Which board to watch |
| `github.status_field` / `agent_field` / `template_field` | `Status` / `Agent` / `Template` | Board field names |
| `runner.execution_mode` | `container` | `container` or `host` |
| `runner.auth_mode` | `subscription` | `subscription` or `api_key` |
| `runner.max_parallel` | `2` | Tasks in flight overall |
| `runner.max_parallel_per_agent` | `1` | Raise only with API keys |
| `runner.timeout_seconds` | `5400` | Per-agent-invocation limit |
| `loop.poll_interval_seconds` | `60` | Board polling interval |
| `loop.max_attempts` | `3` | Retries before a card is blocked |
| `loop.question_timeout_hours` | `48` | How long a question waits |
| `default_template` | `python-fastapi-nextjs` | Used for empty repositories |

## Project layout

```
flywheel/
├── __main__.py            `python -m flywheel` entry point
├── cli/                   the command-line interface (doctor, gui, run-once, loop, ...)
├── tui/                   the Textual terminal app: wizard, dashboard, questions inbox
├── gui/                   the browser app: FastAPI server + self-contained SPA
├── config.py              settings, loaded from ~/.flywheel/config.toml
├── probes.py              environment detection (powers doctor and the wizard)
├── storage.py             SQLite ledger: claims, runs, events
├── workspace.py           clone, branch, commit, push
├── bootstrap.py           composition root
├── api.py                 optional status API (flywheel serve)
├── domain/                entities, enums, the shared agent-result contract
├── github/                board (Projects v2), issues, pull requests, actions
├── agents/                runners, prompt composition, container execution
├── mcp/                   MCP server registry for both agents
├── services/              dispatcher, clarification, scaffolding, delivery, loop controller
└── delivery/              Telegram
docker/runner.Dockerfile   the container agents run in
templates/                 bootstrap templates for empty repositories
tests/
```

Three entry surfaces, cleanly separated: **`flywheel/cli/`** is everything you drive from a
terminal, **`flywheel/tui/`** is the interactive Textual (terminal) app, and **`flywheel/gui/`**
is the browser app. `flywheel` with no arguments opens the TUI; `flywheel gui` opens the browser
app; any other subcommand runs headless. All three read and write the same
`~/.flywheel/config.toml` and the same run ledger.

## Development

```bash
uv run pytest              # test suite
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy factory        # types
```

The architectural tests shipped in the template are themselves tested:
`tests/test_architecture_tests.py` builds synthetic projects containing each violation and
asserts they are rejected.

## Troubleshooting

**`Codex: package installed but the PATH shim is missing or corrupt`** — an interrupted
`npm -g` install. Run `npm i -g @openai/codex`. Container mode is unaffected: the image has its
own working copy.

**`Project board: board #N not visible to this token`** — a classic PAT needs `project` ticked;
`repo` alone is not enough.

**`board status field has no 'Needs Info' option`** — add every `Status` option listed above.

**The agent reports success but nothing was committed** — the run is failed deliberately and
retried. Usually the task was too vague; add acceptance criteria to the issue.

**Preview deploy fails at the SSH step** — check `DEPLOY_HOST`, `DEPLOY_USER` and
`DEPLOY_SSH_KEY`, and that the `traefik-edge` network exists on the host.

**Everything is slow / cards sit in `Todo`** — subscription mode allows one task per agent.
Check `flywheel runs` for something stuck; a restart marks interrupted runs failed and releases
their claims.
