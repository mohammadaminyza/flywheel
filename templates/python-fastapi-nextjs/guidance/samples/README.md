# Code samples

A complete vertical slice — the "credentials" feature — showing exactly how a feature is built
in this repository. **Read these before writing new code and copy their shape.** They are
reference material, not part of the running application, and no test imports them.

## The slice, in dependency order

| File | What it demonstrates |
| --- | --- |
| `backend/app/domains/entities/credential.py` | The aggregate root. Every state change is a method (`rename`, `share_with_team`, `soft_delete`) — nothing outside sets a field. |
| `backend/app/repositories/credentials.py` | A pure persistence collection: get / list / exists / add / save / remove. No validation, no branching on domain state, no raising. |
| `backend/app/services/credentials/exceptions.py` | One class per failure case; the message is built inside `__init__`, never at the call site. |
| `backend/app/services/credentials/service.py` | All use-case logic, validation and the permission check (`_guard_access`, via claims). Calls aggregate methods, then persists. |
| `backend/app/schemas/credential.py` | Request and response schemas. These exist only for the router boundary. |
| `backend/app/api/routes/credentials.py` | Thin handlers: read the schema, call one service method, return `Response.from_model(...)`. No `if`, no logic. |
| `backend/tests/unit/test_credential_service.py` | Service tests against an in-memory repository — fast, no database, one behaviour per test. |
| `backend/tests/integration/test_credentials_api.py` | The same feature through the real HTTP route, including the 404, the 400 and the 401. |
| `frontend/lib/hooks/useCredentials.ts` | TanStack Query hooks, with the query key shared between the query and its invalidations. |
| `frontend/app/credentials/page.tsx` | A page where **every** user-visible string goes through `t(...)` — loading, errors, empty state and buttons included. |

## What to copy

- The direction of dependencies: routes → services → repositories → ORM. Never backwards, never
  skipping a layer.
- Raising a specific domain exception instead of returning `None` or `False` on failure.
- Mutating state only through aggregate methods.
- Writing both a unit test and an integration test for every feature.
- Routing all user-visible text through `t(key)`.

## What not to copy

- The imports resolve against `app.*` and `@/*` as they exist in this template; adjust them to
  wherever your feature actually lives.
- `Credential` is illustrative. Do not add a credentials feature to the application because it
  appears here.
