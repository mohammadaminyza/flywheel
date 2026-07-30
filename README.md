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
- [Templates: what ships, and what only teaches](#templates-what-ships-and-what-only-teaches)
- [Planning a project with two agents](#planning-a-project-with-two-agents)
- [The base branch](#the-base-branch)
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
- **Knows your architecture.** Every template carries rules, code samples and architectural
  tests. They are staged into the working copy — never committed to your repository — and the
  agent reads them before writing anything, while the tests reject work that breaks the structure.
- **Fits the repository it is given.** A codebase that already exists is never restructured — its
  layout and its own lint/test scripts become the rules. A repository with no code yet gets a
  complete working project committed into it first, then the feature on top.
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

## Templates: what ships, and what only teaches

A template has two halves, and the split is the whole point:

```
templates/<id>/
├── template.yml            # catalogue entry: id, name, description, ports, environments
├── template/               # SHIPPED — copied into the repository and committed
│   ├── backend/  frontend/ # a working FastAPI + Next.js skeleton with tests
│   ├── .github/workflows/  # CI, preview environments, releases
│   └── docker-compose*.yml, README.md, .gitignore
└── guidance/               # NOT SHIPPED — staged as .template/ in the working copy only
    ├── template.yml        # commands, ports, health check, environments
    ├── README.md           # the architectural rules, in prose
    ├── samples/            # a complete vertical slice to imitate
    └── architecture-tests/ # tests that enforce the rules automatically
```

`guidance/` is the factory's instruction to the agent, not the client's source code. It is copied
into the workspace as `.template/` on **every** run, added to that clone's `.git/info/exclude`,
and listed in the shipped `.gitignore` — so the agent always reads the current rules while the
repository only ever receives the product. A repository that keeps its own committed `.template/`
folder wins: the bundled guidance is never written over it.

**Your own templates:** Setup → *Templates* has two fields that work together — where templates
come from, and which one is used. Point *Where your templates live* at either a folder of
template folders or at a single template folder; both do what they look like they do. The
dropdown then lists what was actually found, bundled and yours together, and names the folder
each one was read from. Yours win when an id matches a bundled template.

### A repository that already has code is its own template

The first time the factory opens a repository it looks at what is there, and one of two things
happens:

- **There is already an application.** Nothing is scaffolded and no bundled rules are applied.
  The repository's own layout becomes the structure to follow, its own `package.json` scripts and
  `pyproject.toml` tools become the checks that must pass, and the agent is told plainly not to
  restructure, rename, re-layer or reformat anything the task did not ask about. Your project
  keeps its principles; the factory adapts to them, not the other way round.
- **There is no code yet** — an empty repository, or one holding only a README and a licence —
  **the template is committed into it.** An empty repository gets it as the base branch; a
  repository that already has commits gets it on the feature branch, so it arrives through a
  pull request instead of being pushed over your branch. Flywheel also writes
  `.flywheel/project.yml` with `template: <id>`, which is what keeps the template's rules
  applying on later runs once the skeleton is real code.

A repository that keeps its own committed `.template/` folder always outranks both.

### The base branch

Setup → *Base branch* names the branch everything is cut from and merged back into — `main`,
`master`, `dev`, whatever you use. Every feature branch starts at that branch's latest commit and
every pull request targets it. Leave it blank and each repository's own default branch is used,
and a repository that does not have the branch you named falls back to its default too, so a
mixed set of `main` and `master` repositories works without extra configuration.

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
the factory. A repository with no guidance still works; the agent falls back to reading the
existing code and matching it.

## Planning a project with two agents

The **Project brief** page turns a brief into board tasks. Write the purpose, goals, client needs
and constraints, pick the repository, and press *Review repository and create plan*.

- **Both agents plan.** Claude Code and Codex each read the repository and the brief on their own
  and return their own plan. Tick either one off if you only want one opinion.
- **Deep research.** With it on, each agent may search and fetch the web while planning — current
  library versions, deprecations, the standard approach to the security-sensitive parts — and
  cites what it used in the task body. It never overrides what the repository already does.
- **The plans are merged.** Tasks with the same title are one task; the version with the fuller
  acceptance criteria wins, and anything both agents proposed is listed first. Tasks only one
  agent thought of are kept.
The repository list on that page comes from the token and owner already saved in Setup, so a
brand-new board with no issues on it still lets you create the first task.

### Grooming happens by itself

You do not write tasks for the factory by hand. On every cycle, **before it claims anything**,
the loop reads the Todo cards straight from GitHub and has the agents rewrite them:

- they re-read the repository and this brief, so a card is reconciled with what already exists;
- each card comes back with scope, what is out of scope, acceptance criteria as a checklist, the
  unit and integration tests it needs, and its dependencies on the other cards by number;
- cards that must ship together are given the same branch/workstream, which is written to the
  board's `Branch` field;
- overlap between two cards is called out in both of them;
- the rewritten text is pushed back to the GitHub issue, and the card is marked so it is groomed
  once, not every cycle. Delete the `<!-- flywheel:refined -->` line in an issue to have it
  groomed again.

Write issues however you like — a title and a sentence is enough. The *Backlog grooming* card on
the Project brief page shows what is queued and what was rewritten, lets you cap how many cards
are groomed per cycle, run a pass immediately, or switch the whole thing off.

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
2. **Prepare.** The repository is cloned into a fresh workspace and the base branch is resolved.
   If it holds no application code yet, the bootstrap template is laid down first; if it already
   has code, nothing is scaffolded and its own conventions are read instead.
3. **Read the rules.** For a repository the factory scaffolded, the template's guidance is staged
   as `.template/` in the workspace (git-excluded), so the agent gets the current architecture and
   the repository never receives it. For a repository that came with its own code, the rules are
   that code: its layout and its own scripts, with an explicit instruction not to restructure it.
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

**Retries.** A card only spends an attempt on something the agent could have done differently.
Failures that happen before the agent starts — a workspace that could not be cloned, a factory
restart, a rate limit or a 503 — are retried on the next cycle with the card's attempts
untouched, up to `loop.max_infrastructure_retries`. Anything that is failed or `Blocked` also
has a **Retry now** button on the dashboard, which hands the card its full budget back and puts
it in `Todo` immediately; the earlier runs stay in the ledger as history.

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
and Playwright, plus every connection saved on the **Connections** page and anything in your
project's `mcp:` block. The same set reaches whichever agent the card is routed to, in that
agent's own vocabulary: Claude Code receives an `--mcp-config` file, and Codex receives
`-c mcp_servers.<name>=…` overrides — the only form it honours, since a config file passed by
path is accepted and then ignored. Remote servers carry their headers as `headers` for Claude and
`http_headers` for Codex, which rejects `env` on an HTTP server. `${VAR}` placeholders resolve
from the factory's secrets.

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
| `github.base_branch` | — | Branch every feature branch is cut from; blank = the repository's own default |
| `github.status_field` / `agent_field` / `template_field` | `Status` / `Agent` / `Template` | Board field names |
| `runner.execution_mode` | `container` | `container` or `host` |
| `runner.auth_mode` | `subscription` | `subscription` or `api_key` |
| `runner.max_parallel` | `2` | Tasks in flight overall |
| `runner.max_parallel_per_agent` | `1` | Raise only with API keys |
| `runner.timeout_seconds` | `5400` | Per-agent-invocation limit |
| `loop.poll_interval_seconds` | `60` | Board polling interval |
| `loop.max_attempts` | `3` | Retries before a card is blocked |
| `loop.max_infrastructure_retries` | `5` | Free retries for failures the agent never caused |
| `loop.question_timeout_hours` | `48` | How long a question waits |
| `loop.auto_start` | `false` | Start the loop when the app opens |
| `default_template` | `python-fastapi-nextjs` | Used for empty repositories |
| `templates_dir` | — | A folder of templates, or one template folder; bundled ones stay available |
| `planning.agents` | `["claude-code", "codex"]` | Who plans a project brief and researches tasks |
| `planning.deep_research` | `true` | Let the planning agents search the web |
| `planning.max_turns` | `60` | Turn budget for one planning run |
| `planning.auto_refine` | `true` | Groom new Todo cards on every cycle, before they are built |
| `planning.refine_batch_size` | `5` | Cards groomed per cycle |

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
templates/<id>/template/   the project skeleton committed into an empty repository
templates/<id>/guidance/   rules, samples and architecture tests staged for the agent only
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
