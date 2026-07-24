# Architectural rules

This folder is the authority on how this repository is structured. It is read by every agent
before it writes a line of code, and the rules below are enforced by the tests in
`architecture-tests/`. Edit this folder as the project evolves — it is meant to change.

## Request flow

```
route -> service -> aggregate/entity + repository -> ORM/DB
```

Each layer has exactly one job. **Domain entities and domain models are the only currency
between layers.** Schemas never cross the router boundary.

| Folder | Responsibility |
| --- | --- |
| `api/routes/` | The only layer that touches request/response schemas. Wire input → service → `Response.from_model(...)`. No branching, no logic. |
| `api/deps.py` | Dependency injection: repositories, services, auth deps, `Annotated` `*Dep` aliases. |
| `services/<domain>/` | All use-case logic, validation and permission checks. Raises domain exceptions on invalid state. |
| `services/<domain>/exceptions.py` | One exception class per failure case. |
| `repositories/` | A pure persistence collection: get / list / add / save / remove. Nothing else. |
| `domains/entities/` | Aggregate roots. They own **all** state changes through their own methods. |
| `domains/models/` | Read and query models. |
| `domains/orm/` | SQLAlchemy table mappings, persistence only. |
| `schemas/` | Router-facing pydantic. Router boundary only. |
| `common/` | Base `Entity` / `AggregateRoot`, domain exceptions, `BaseResponse` / `BaseSerializer`. |

## The rules

1. **The router only speaks HTTP.** Routes are the single layer allowed to import from
   `schemas/`. A handler reads the request schema, calls one service method, and returns
   `Response.from_model(...)`. No `if`, no branching, no permission checks in a route.
2. **Mapping happens in the router** via `from_model` / `from_list_model`. Services and
   repositories accept and return entities and domain models — never a schema.
3. **All logic and validation lives in the service** (occasionally in the aggregate). On invalid
   state, raise the related domain exception. Never validate in the router or the repository.
4. **Errors are domain exceptions, one class per case.** Build the message inside the class
   `__init__` and `raise` it bare. Never assemble the message at the call site. Never raise
   `HTTPException` for a business rule — global handlers map domain exceptions to HTTP.
5. **Every state change lives in the aggregate.** Updates and deletes are aggregate methods
   (`credential.rename(name)`, `.soft_delete()`). Services never `setattr` entity fields;
   repositories never mutate.
6. **The repository is just a persistence collection.** No business rules, no validation, no
   branching on domain state.
7. **Raise, don't return bool.** A missing row on update/delete raises `NotFoundException`.
8. **Data containers are pydantic `BaseModel`**, not `@dataclass`.
9. **Authorization via roles → scopes → claims.** Use `require_claims(Claims.X)` and
   `user.has_claim(...)`, checked in the service. Never an `is_admin` boolean gate.
10. **No lazy imports.** Imports go at module top, not inside functions.
11. **KISS · SOLID · DRY.** No defensive `try/except` — let exceptions reach the global handlers.
12. **Do not write comments.** No docstrings restating a signature, no divider banners. Write a
    comment only for a non-obvious *why*, and keep it to one line. If you want to explain
    *what* the code does, rename things instead.

## Frontend rules

- **i18n is mandatory.** All user-facing text goes through `t(key)` with a typed key in
  `lib/translations.ts` (`fa` default + `en`). Never inline `lang === 'fa' ? … : …` ternaries
  and never hardcode a user-visible string. The app is RTL-first.
- Data fetching through TanStack Query; the API client lives in `lib/api.ts`.
- Pages in `app/`, shared components in `components/` (`ui/` primitives plus feature folders).

## Adding a feature (the expected slice)

Entity in `domains/entities/` → ORM in `domains/orm/` → repository in `repositories/`
(registered in `repositories/__init__.py`) → service in `services/<domain>/` (+ `exceptions.py`)
→ schemas in `schemas/` → route in `api/routes/` → DI wiring and a `*Dep` alias in `api/deps.py`
→ router included in `api/main.py` → alembic migration.

## Tests

- Unit tests for services and aggregates, in `backend/tests/unit/`.
- At least one integration test per feature that goes through the real HTTP route, in
  `backend/tests/integration/`.
- The architectural tests in `architecture-tests/` must pass. If a rule genuinely blocks the
  task, that is a question for the product owner — not a rule to work around and not a test
  to weaken.
