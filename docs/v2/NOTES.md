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

- World margins are thin: at seed 7, 59% of non-`THIN` companies get a zero cash-flow limit for a 12-month working-capital loan, and only 27% get 100k ₽ or more (`apps/api/LIMITS.md`, «Что видно на синтетическом мире»). Pick approval personas from those, or ask track 1 to raise operating margins in `apps/world/turnovers.py` (then retrain T10). Raised with the econ team. (from T16)
- Few young companies: about 4% are under 6 months, 13% under a year. If personas need more, shift the age distribution in `_company` (`apps/world/`) or add a young-company group. (from T04)
- Every archetype has candidates among both ИП and ООО; the shell archetype is ООО only. (from T04)
- Expected grades come from `apps/ml/scorecard_v1.json`. Retraining it (any change to `config/features.json`, `config/world_outcomes.json` or the world) moves persona grades, so the expected-outcomes table must be regenerated with it. (from T10)
- Per-persona source failures (e.g. ФССП down) go in `MOCK_SOURCES_SCENARIOS`; the world doesn't change. For sampling without latency use `payload(company, source)`. (from T06/T07, `apps/sources/README.md`)

## T15 — scoring adapter

- T14 emits `source_unavailable_fns`, `source_unavailable_fssp` and `source_unavailable_arbitration` flags. Apply a score penalty for each degraded source after the econ team defines penalty values; no such values exist in the current policy. (`apps/api/stop_factors.py`)
  - Not handled in T15: the values belong in `config/` (shared-folder PR, econ team). Then apply them in `Scorer.score` (`apps/api/scoring.py`) and show each penalty as its own row in `ScoreResult.contributions`, so points still add up. (T15)

## T17 — offers and counterfactuals

- Build each variant with `quote(cash_flow, score, product, term, route, collateral, requested, equipment)` (`apps/api/limits.py`, `apps/api/LIMITS.md`) and copy `limit`, `price`, `monthly_payment`, `guarantee_fee`, `expected_profit` into the `Offer`. `amount == 0` or `below_product_minimum` → not offerable; `within_cap == False` → try collateral (it lowers LGD) or decline (SPEC §9.2). (from T16)
- T16 picks no collateral: choose it per product and legal form (owner guarantee is standard for ООО working capital, equipment pledge for the investment loan, SPEC §3–4). "Longer term": call `quote(requested=amount)` over the product's terms until `quote.amount == amount`. (from T16)
- Counterfactuals: re-run `quote` on a changed `CashFlow` (`dataclasses.replace(cf, current_payments=0)` gives the fixture's "without the current loan" hint). The credit line's 1% fee on the unused limit (SPEC §4) is in neither the rate nor `Offer`: show it in `tradeoff`. (from T16)

## T18 — routing

- Pricing needs the route of each offer's amount: the processing cost in `quote(route=...)` (`apps/api/limits.py`) is 3k / 25k / 60k ₽ by route. Expose routing of a given amount (grade, flags, tier, amount) so T17 can price each offer with its own route. (from T16)
- Grade comes from the same `ScoreResult` as T16 (`apps/api/scoring.py`); the challenger's grade must not affect the route. (from T15)
- Route on `DataQuality.tier` from the same call as T16 (`apps/api/features/data_quality.py`); the reason to show is in `client_message`, and each metric's tier tells the underwriter which one failed. (from T09)
- Every flag returned by T14 requires staff review under SPEC §7; preserve its reason code and staff/client wording in route reasons. (`apps/api/stop_factors.py`)

## T19 — state machine

- After scoring, call `cash_flow_from_responses(enrichment.responses, data_quality)` (`apps/api/limits.py`) once and pass it to T17. It raises `ValueError` without БКИ, which can't happen after the "source unavailable" route. `Decision.policy_version` is one string, but there are now three policy versions (`credit_policy_v0`, `DataQuality.policy_version`, the feature set): decide how to combine them, or propose a contract field. (from T16)
- After stop factors (none fired), call `score(features, application_id)` from `apps/api/scoring.py` once and store `Scoring.score`, `Scoring.challenger` and `score.model_version` in the `Decision`. `FeatureSetMismatch` means the card doesn't fit the features: route to the underwriter with a reason, don't decline. (from T15, `apps/api/SCORING.md`)
- Persist completed `Enrichment.facts` through `apps/api/fact_store.py` in the application workflow transaction; T13 provides the store, but no workflow calls it yet. (from T13)
- Features need an `as_of`: `features_from_responses` takes the day the sources answered. The mock world is frozen at `WORLD_AS_OF` (default 2026-10-01), so the further today is from it, the more business age, episode ages and the 3-month inquiry window drift from the world's data. Decide which date the demo pipeline passes (probably the world's `as_of`). (from T08)
- Stage 3 ends with the data-quality tier: call `data_quality_from_responses(enrichment.responses, request.declared_annual_revenue)` right after `enrich` and store it with the application. (from T09)
- `company_not_found` → the application can't be processed; enrichment not complete (`unavailable_required`) → underwriter queue with reason "source unavailable"; otherwise → stop-factors and scoring. (from T07, `apps/sources/README.md`)

## T22 — overdraft pre-approval

- "Вам доступно до X": `quote(cf, score, ProductId.OVERDRAFT, 12, Route.AUTO, requested=None)` (`apps/api/limits.py`); `amount` is the limit rounded down to 10 000 ₽, with the turnover, cash-flow and leverage constraints applied. Skip `below_product_minimum` and `within_cap == False`. (from T16)
- The batch has no application: call `data_quality(payloads)` without `declared_revenue`. A company with no ФНС revenue then gets `revenue_coverage = null` → `THIN`, so it is never pre-approved. (from T09)

## T23 — statement upload

- `cash_flow` (`apps/api/limits.py`) reads only our own account. For an uploaded statement to raise the limit (SPEC §5.1), decide how its operating flow joins ours, then recompute through `cash_flow` with the recomputed tier. (from T16)
- `data_quality` reads only our own account statement. Decide how an uploaded statement changes the metrics (most likely coverage: add its operating inflows to ours, at the lower trust level) and recompute through the same function. (from T09)

## T25 — ИНН entry and consents

- Collect both 218-ФЗ consents: `bki_company` with the company's ИНН and `bki_owner` with the main owner's ИНН (for ИП: the entrepreneur's ИНН). Without them БКИ isn't queried and the application can't go past the underwriter. (from T07)
- ИНН/ОГРН in `tests/fixtures/` fail checksum validation; generated ones pass. If the UI validates checksums, fix the fixtures through a shared-folder PR. (from T01)

## T27 — "analyzing" screen

- Use `enrich(..., on_event=handler)`: `SourceEvent(source, attempt, status, final, error)`. The mock service's HTTP router isn't mounted in `apps/api/main.py`; the orchestrator calls the service in-process by default. (from T06/T07)

## T30 — staff dossier

- `ScoreResult.contributions` has one row per card feature but not the base points: show `points - sum(contributions)` as «базовые баллы». The challenger (`Decision.challenger`) is for staff only, labelled as a shadow model that didn't affect the decision. (from T15)
- Limit breakdown: every `LimitConstraint` carries its `inputs` and `note`; the `leverage` note says when the revenue came from our account because ФНС has none. `PriceBreakdown` components add up to the rate. (from T16, `apps/api/LIMITS.md`)
- Our own БКИ request doesn't appear in the report. If the dossier should show it, ask track 1 to add it in the БКИ adapter (`apps/sources/`). (from T06)

## T32 — risk-manager page

- To compare the champion with the shadow challenger, `validate_scorecard(world, card)` (`apps/ml/validate.py`) also works for `apps/api/scorecards/scorecard_v0.json`; holdout Gini is 0.32 for v0 vs 0.46 for v1 (`apps/api/SCORING.md`). Optional. (from T15)
- The page shows `apps/ml/scorecard_v1.validation.json`, regenerated by `python -m apps.ml` with the card. Format: `ValidationReport` in `apps/ml/validation.py` (imports only the contracts, so the API can load it without the world). No API endpoint serves it yet: add a read-only GET in `apps/api/` (agree with track 2). (from T11, `apps/ml/README.md`, «Валидация»)
- Show `limitations` next to the metrics, and the true-PD ceiling next to the holdout Gini. `predicted_vs_actual: below` means the card underestimates risk in that grade (grade C now). (from T11)
- `ValidationReport` is not a contract. If the UI wants generated types for it, propose a shared-folder PR moving it into `apps/api/contracts.py`. (from T11)

## T34 — Docker

- The API has no logging config of its own: `init_db` runs Alembic, whose `alembic.ini` sets the root logger to WARN, so INFO lines (e.g. the champion-vs-challenger line from `apps/api/scoring.py`) don't reach `docker logs`. Configure app logging (level from `.env`) at startup. (from T15)
- The Dockerfile copies only `apps/api` and `config`: add `apps/ml/` so the scoring adapter (`apps/api/scoring.py` imports `apps.ml`) finds `scorecard_v1.json`, its training passport and `apps.ml.score` in the image, and the risk-manager page (T32) finds `scorecard_v1.validation.json`. `apps.ml.train` needs `apps/world/`, but nothing imports it at runtime. The challenger card `apps/api/scorecards/` is already copied with `apps/api`. Set `SCORECARD_*` from `.env.example` in compose. (from T10, T15)
- `MOCK_SOURCES_*` and `WORLD_*` are in `.env.example`. So the API doesn't rebuild the world on every start, write it to `apps/world/out` when building the image. (from T07)

## T36 — policy simulator

- Pass a changed `CreditPolicy` (`load_policy(path)` or `default_policy().model_copy(...)`, `apps/api/credit_policy.py`) into `quote` / `limit_breakdown`; nothing in T16 reads global state besides the default policy. (from T16)

## Unassigned

- PSI on the live application flow (model monitoring) has no task. T11's PSI compares the holdout with the training sample, so it is ≈ 0 by construction. Once applications are stored (T19), `psi(...)` from `apps/ml/validation.py` can compare their grades and card features with the training sample. (from T11)
- SPEC §7 includes a flag when the ОКВЭД does not match the application purpose, but defines no purpose-to-ОКВЭД mapping. Agree and configure that policy before implementing the flag in `apps/api/stop_factors.py`.
- The `Scorecard` contract has no `feature_set_version`, so the scoring adapter reads it from a passport next to the card (`scorecard_vN.training.json`, also for the hand-set `scorecard_v0`). Proposed: a shared-folder PR adding `feature_set_version` to `Scorecard`; then `load_scorecard_model` (`apps/api/scoring.py`) reads it from the card. (from T10, T15)
