# Handoff notes between tasks

Everything one task leaves for another lives here, filed under the task that has to act on it.

## Rules

- **Deferring something** ("left for T14", "T19 should decide", a known limitation someone will hit): add a note under the task that should act, in the same commit. If no task fits, put it under **Unassigned** and tell the user.
- **Starting a task:** read your task's section and **Any task**. Also run `git grep "TODO(T16)"` with your ID.
- **Finishing a task:** delete the notes your PR handled. Notes you decided not to handle stay, with one line saying why.
- A note is 1–3 lines: what, where in the code, what to do. Long explanations belong in the component's docs; the note links to them.
- Optional: mark the exact spot in code with `# TODO(T14): …`. The note here is still required.

## Any task

- `q`, `archetype`, `pd_12m` and `default_12m` are hidden truth of the synthetic world. They must never reach features, the engine or the UI; only training labels (T10) and validation (T11) may read the outcome. (from T01–T05, `apps/world/README.md`)
- New data in the world gets its own `company_rng` stream; hash tests pin what T01–T05 generate. (from T01, `apps/world/README.md`)

## T12 — demo personas

- Few young companies: about 4% are under 6 months, 13% under a year. If personas need more, shift the age distribution in `_company` (`apps/world/`) or add a young-company group. (from T04)
- Every archetype has candidates among both ИП and ООО; the shell archetype is ООО only. (from T04)
- Expected grades come from `apps/ml/scorecard_v1.json`. Retraining it (any change to `config/features.json`, `config/world_outcomes.json` or the world) moves persona grades, so the expected-outcomes table must be regenerated with it. (from T10)
- Per-persona source failures (e.g. ФССП down) go in `MOCK_SOURCES_SCENARIOS`; the world doesn't change. For sampling without latency use `payload(company, source)`. (from T06/T07, `apps/sources/README.md`)

## T13 — fact store

- `Enrichment` round-trips through `model_dump_json` / `model_validate_json`; its facts are contract `Fact` objects. (from T07)

## T14 — stop-factors and flags

- Revenue mismatch flag: compare the declared revenue with `fns_revenue(...).estimate_12m` (`apps/api/features/revenue.py`), not with ФНС as filed: 7% / 15% of honest clients instead of 21% / 82%. (from T08, `apps/api/features/README.md`)
- Overdue stop factors: use `subject_history(report, as_of, default_policy().overdue)` from `apps/api/features/credit.py` per report: `worst_days_24m >= 90` and `current_overdue_days > 30` already exclude technical delays and small amounts. "3+ inquiries in 30 days": `inquiries_since(bki, as_of - 30 days)`. (from T08)
- "3+ credit inquiries in 30 days" must count other banks' inquiries only; our application leaves no inquiry in the report. (from T06)
- `Enrichment.degraded` lists optional sources that didn't answer (ФНС, ФССП, арбитраж): each needs the "source unavailable" flag and a penalty. (from T07)

## T15 — scoring adapter

- The trained card is `apps/ml/scorecard_v1.json`. Read bins as `apps/ml/score.py` does (`find_bin`): numeric `[lower, upper)`, categorical by membership, `None` → the missing bin; a missing bin with `woe: null` has 0 points (average risk). Simplest is to import `find_bin` / `total_points` from `apps.ml`: the package imports only the contracts. (from T10)
- The `Scorecard` contract has no `feature_set_version`; it is in `apps/ml/scorecard_v1.training.json`. Refuse to score when it differs from `Features.feature_set_version`. Proposed: a shared-folder PR adding `feature_set_version` to `Scorecard`. (from T10)

## T16 — limit and pricing

- Data-quality tier: `data_quality_from_responses(enrichment.responses, request.declared_annual_revenue)` (`apps/api/features/data_quality.py`). `PARTIAL`: L_поток on the actual account flow, no extrapolation; `THIN`: no L_поток (SPEC §5.1). `revenue_coverage` is scaled to 12 months for short histories, so don't reuse it as "actual flow". (from T09)

## T18 — routing

- Route on `DataQuality.tier` from the same call as T16 (`apps/api/features/data_quality.py`); the reason to show is in `client_message`, and each metric's tier tells the underwriter which one failed. (from T09)

## T19 — state machine

- Features need an `as_of`: `features_from_responses` takes the day the sources answered. The mock world is frozen at `WORLD_AS_OF` (default 2026-10-01), so the further today is from it, the more business age, episode ages and the 3-month inquiry window drift from the world's data. Decide which date the demo pipeline passes (probably the world's `as_of`). (from T08)
- Stage 3 ends with the data-quality tier: call `data_quality_from_responses(enrichment.responses, request.declared_annual_revenue)` right after `enrich` and store it with the application. (from T09)
- `company_not_found` → the application can't be processed; enrichment not complete (`unavailable_required`) → underwriter queue with reason "source unavailable"; otherwise → stop-factors and scoring. (from T07, `apps/sources/README.md`)

## T22 — overdraft pre-approval

- The batch has no application: call `data_quality(payloads)` without `declared_revenue`. A company with no ФНС revenue then gets `revenue_coverage = null` → `THIN`, so it is never pre-approved. (from T09)

## T23 — statement upload

- `data_quality` reads only our own account statement. Decide how an uploaded statement changes the metrics (most likely coverage: add its operating inflows to ours, at the lower trust level) and recompute through the same function. (from T09)

## T25 — ИНН entry and consents

- Collect both 218-ФЗ consents: `bki_company` with the company's ИНН and `bki_owner` with the main owner's ИНН (for ИП: the entrepreneur's ИНН). Without them БКИ isn't queried and the application can't go past the underwriter. (from T07)
- ИНН/ОГРН in `tests/fixtures/` fail checksum validation; generated ones pass. If the UI validates checksums, fix the fixtures through a shared-folder PR. (from T01)

## T27 — "analyzing" screen

- Use `enrich(..., on_event=handler)`: `SourceEvent(source, attempt, status, final, error)`. The mock service's HTTP router isn't mounted in `apps/api/main.py`; the orchestrator calls the service in-process by default. (from T06/T07)

## T30 — staff dossier

- Our own БКИ request doesn't appear in the report. If the dossier should show it, ask track 1 to add it in the БКИ adapter (`apps/sources/`). (from T06)

## T32 — risk-manager page

- The page shows `apps/ml/scorecard_v1.validation.json`, regenerated by `python -m apps.ml` with the card. Format: `ValidationReport` in `apps/ml/validation.py` (imports only the contracts, so the API can load it without the world). No API endpoint serves it yet: add a read-only GET in `apps/api/` (agree with track 2). (from T11, `apps/ml/README.md`, «Валидация»)
- Show `limitations` next to the metrics, and the true-PD ceiling next to the holdout Gini. `predicted_vs_actual: below` means the card underestimates risk in that grade (grade C now). (from T11)
- `ValidationReport` is not a contract. If the UI wants generated types for it, propose a shared-folder PR moving it into `apps/api/contracts.py`. (from T11)

## T34 — Docker

- The Dockerfile copies only `apps/api` and `config`: add `apps/ml/` so the scoring adapter (T15) finds `scorecard_v1.json`, its training passport and `apps.ml.score` in the image, and the risk-manager page (T32) finds `scorecard_v1.validation.json`. `apps.ml.train` needs `apps/world/`, but nothing imports it at runtime. (from T10)
- `MOCK_SOURCES_*` and `WORLD_*` are in `.env.example`. So the API doesn't rebuild the world on every start, write it to `apps/world/out` when building the image. (from T07)

## Unassigned

- PSI on the live application flow (model monitoring) has no task. T11's PSI compares the holdout with the training sample, so it is ≈ 0 by construction. Once applications are stored (T19), `psi(...)` from `apps/ml/validation.py` can compare their grades and card features with the training sample. (from T11)
