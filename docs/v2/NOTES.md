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

## T20 — staff actions

- An underwriter approves only within their authority (SPEC §11): re-route every staff-changed offer with `route_application(new_amount, ...)` (`apps/api/routing.py`) and require the committee's two signatures when it returns `committee` (e.g. above 15M ₽, or grade D/E above 5M ₽). (from T18)
- Change statuses only through `transition(db, app, to, actor=actor_of(user), message=comment)` (`apps/api/workflow.py`, `apps/api/WORKFLOW.md`): it checks SPEC §13 and writes the audit event. Put a staff-built `Decision` through `store_decision(db, app, decision, actor=...)`; it sets `offer_valid_until` when the status is `offered`. Re-date `Offer.valid_until` to the approval day: the engine dates offers from its own run. (from T19)
- `in_review` comes in three shapes: offers computed on an underwriter/committee route (approve or change them); no offers (grade D, flags, THIN, score unavailable); `decision = null` after three failed automatic runs (`error` events in the log). (from T19)
- `route_source_unavailable` needs a "re-run enrichment" action, but `in_review → submitted` isn't in `TRANSITIONS`. Add it (with an audit reason) or go through `needs_data → enriching`; `run_application` expects a claimed `enriching` application. (from T19)

## T21 — documents, signing, disbursement

- `accepted → documents → signed → conditions → disbursed` is in `TRANSITIONS` (`apps/api/workflow.py`); move through `transition(...)`. The chosen offer is `LoanApplication.selected_offer_id`, its terms in `decision['offers']`. Withdrawal is allowed up to `documents`, not after `signed`. (from T19)

## T22 — overdraft pre-approval

- "Вам доступно до X": `quote(cf, score, ProductId.OVERDRAFT, 12, Route.AUTO, requested=None)` (`apps/api/limits.py`); `amount` is the limit rounded down to 10 000 ₽, with the turnover, cash-flow and leverage constraints applied. Skip `below_product_minimum` and `within_cap == False`. (from T16)
- The batch has no application: call `data_quality(payloads)` without `declared_revenue`. A company with no ФНС revenue then gets `revenue_coverage = null` → `THIN`, so it is never pre-approved. (from T09)

## T23 — statement upload

- `cash_flow` (`apps/api/limits.py`) reads only our own account. For an uploaded statement to raise the limit (SPEC §5.1), decide how its operating flow joins ours, then recompute through `cash_flow` with the recomputed tier. (from T16)
- `data_quality` reads only our own account statement. Decide how an uploaded statement changes the metrics (most likely coverage: add its operating inflows to ours, at the lower trust level) and recompute through the same function. (from T09)
- `THIN` goes to `in_review` (`route_data_thin`) as a `# SIMPLIFICATION:` in `decide` (`apps/api/workflow.py`). With uploads, return status `needs_data` with `requested_documents` instead, and after the upload move `needs_data → enriching` and recompute; `run_application` expects a claimed `enriching` application, so add a claim path for it. (from T18, T19)

## T25 — ИНН entry and consents

- Collect both 218-ФЗ consents: `bki_company` with the company's ИНН and `bki_owner` with the main owner's ИНН (for ИП: the entrepreneur's ИНН). Without them БКИ isn't queried and the application can't go past the underwriter. (from T07)
- ИНН/ОГРН in `tests/fixtures/` fail checksum validation; generated ones pass. If the UI validates checksums, fix the fixtures through a shared-folder PR. (from T01)
- Submit with `POST /api/v2/applications` `{"idempotency_key", "request"}`: `request` is `ApplicationRequest` without `application_id` / `submitted_at` (`apps/api/WORKFLOW.md`). The request's ИНН isn't checked against the logged-in user: there is no user → company link yet. Add one if demo profiles stand for companies. (from T19)

## T26 — need intake

- The product recommendation must use the same purpose → product mapping as the offer generator: `PURPOSE_PRODUCT` in `apps/api/offers.py` (cash gap → overdraft, seasonal gap / contract → credit line, equipment → investment, else working capital). Otherwise the client gets an "other product" offer for the product the intake itself recommended. (from T17)

## T27 — "analyzing" screen

- Use `enrich(..., on_event=handler)`: `SourceEvent(source, attempt, status, final, error)`. The mock service's HTTP router isn't mounted in `apps/api/main.py`; the orchestrator calls the service in-process by default. (from T06/T07)
- Poll `GET /api/v2/applications/{id}`: `sources` lists each source step (`source`, `attempt`, `status`, `final`) as enrichment goes, `events` the status changes (`enriching` → `scoring` → decision). Error texts are staff-only. (from T19)

## T28 — offer menu

- `Offer.tradeoff` and `Counterfactual.client_text` are ready to show; `reasons[].client_text` names the binding limit. For `THIN` there's no cash-flow counterfactual (no cash-flow limit at all): show `DataQuality.client_message` as the "how to get more" hint. `rate_above_cap` / `limit_below_product_minimum` reasons explain a decline, `reapply_after` says when to come back. (from T17, `apps/api/OFFERS.md`)
- Several `route_reasons` can carry the same `client_text` ("Заявку проверит специалист банка…"): show distinct texts once. Data-quality route reasons carry `DataQuality.client_message`. (from T18, `apps/api/ROUTING.md`)
- The client gets `client_decision(...)` (`apps/api/workflow.py`), not the raw `Decision`: `route_messages` and `reasons` are distinct client texts, offers without `expected_profit`, no flags, score or challenger. Accept with `POST /api/v2/applications/{id}/accept` `{"offer_id", "revision"}`; 409 with `expired` when the offer ran out. (from T19)

## T30 — staff dossier

- `ScoreResult.contributions` has one row per card feature but not the base points: show `points - sum(contributions)` as «базовые баллы». The challenger (`Decision.challenger`) is for staff only, labelled as a shadow model that didn't affect the decision. (from T15)
- Limit breakdown: every `LimitConstraint` carries its `inputs` and `note`; the `leverage` note says when the revenue came from our account because ФНС has none. `PriceBreakdown` components add up to the rate. (from T16, `apps/api/LIMITS.md`)
- Our own БКИ request doesn't appear in the report. If the dossier should show it, ask track 1 to add it in the БКИ adapter (`apps/sources/`). (from T06)
- "Почему заявка пришла сюда" is `Decision.route_reasons[].staff_text`, one row per condition; the code says which matrix row fired (`apps/api/ROUTING.md`). `Decision` doesn't store each reason's own route (`Routing.routes` does): derive it from the code table if the dossier needs it. (from T18)
- `GET /api/v2/applications/{id}` as staff returns `decision`, `data_quality`, `sources` (envelope per source: status, timestamps, latency, attempts, error), `facts` (fact store) and the full `events`; `GET .../events` is the audit log alone (`apps/api/WORKFLOW.md`). Full source payloads are in `LoanApplication.enrichment` if the dossier needs them. (from T19)

## T32 — risk-manager page

- To compare the champion with the shadow challenger, `validate_scorecard(world, card)` (`apps/ml/validate.py`) also works for `apps/api/scorecards/scorecard_v0.json`; holdout Gini is 0.32 for v0 vs 0.46 for v1 (`apps/api/SCORING.md`). Optional. (from T15)
- The page shows `apps/ml/scorecard_v1.validation.json`, regenerated by `python -m apps.ml` with the card. Format: `ValidationReport` in `apps/ml/validation.py` (imports only the contracts, so the API can load it without the world). No API endpoint serves it yet: add a read-only GET in `apps/api/` (agree with track 2). (from T11, `apps/ml/README.md`, «Валидация»)
- Show `limitations` next to the metrics, and the true-PD ceiling next to the holdout Gini. `predicted_vs_actual: below` means the card underestimates risk in that grade (grade C now). (from T11)
- `ValidationReport` is not a contract. If the UI wants generated types for it, propose a shared-folder PR moving it into `apps/api/contracts.py`. (from T11)

## T33 — golden tests

- Expected offer sets per persona come from `generate_offers` (`apps/api/offers.py`). The fixture `decision_offered.json` says 890k for the "without the current loan" hint; the engine gives 900k because it re-prices the new amount. Use engine numbers in the expected-outcomes table, not the fixture's. (from T17)

## T34 — Docker

- The API has no logging config of its own: `init_db` runs Alembic, whose `alembic.ini` sets the root logger to WARN, so INFO lines (e.g. the champion-vs-challenger line from `apps/api/scoring.py`) don't reach `docker logs`. Configure app logging (level from `.env`) at startup. (from T15)
- The Dockerfile copies only `apps/api` and `config`: add `apps/ml/` so the scoring adapter (`apps/api/scoring.py` imports `apps.ml`) finds `scorecard_v1.json`, its training passport and `apps.ml.score` in the image, and the risk-manager page (T32) finds `scorecard_v1.validation.json`. `apps.ml.train` needs `apps/world/`, but nothing imports it at runtime. The challenger card `apps/api/scorecards/` is already copied with `apps/api`. Set `SCORECARD_*` from `.env.example` in compose. (from T10, T15)
- `MOCK_SOURCES_*` and `WORLD_*` are in `.env.example`. So the API doesn't rebuild the world on every start, write it to `apps/world/out` when building the image. (from T07)
- The v2 worker runs in the API process (`WORKER_ENABLED`, `worker.loop` → `workflow.tick`) and loads the world on the first v2 application (`default_pipeline`, ~5 s without `apps/world/out`). Mock source latency (`MOCK_SOURCES_*`) applies to every demo run. (from T19)

## T36 — policy simulator

- Pass a changed `CreditPolicy` (`load_policy(path)` or `default_policy().model_copy(...)`, `apps/api/credit_policy.py`) into `quote` / `limit_breakdown`; nothing in T16 reads global state besides the default policy. (from T16)
- Routing thresholds are `RoutingPolicy` (`apps/api/routing.py`): pass a changed one to `route_application` / `route_for`, e.g. to see how the auto-decision share moves with the 5M ₽ bound. (from T18)

## Unassigned

- PSI on the live application flow (model monitoring) has no task. T11's PSI compares the holdout with the training sample, so it is ≈ 0 by construction. Applications are now stored (`loan_applications.decision`, T19; card features aren't stored, only `score.contributions`), so `psi(...)` from `apps/ml/validation.py` can compare their grades with the training sample. (from T11, T19)
- SPEC §7 includes a flag when the ОКВЭД does not match the application purpose, but defines no purpose-to-ОКВЭД mapping. Agree and configure that policy before implementing the flag in `apps/api/stop_factors.py`.
- The `Scorecard` contract has no `feature_set_version`, so the scoring adapter reads it from a passport next to the card (`scorecard_vN.training.json`, also for the hand-set `scorecard_v0`). Proposed: a shared-folder PR adding `feature_set_version` to `Scorecard`; then `load_scorecard_model` (`apps/api/scoring.py`) reads it from the card. (from T10, T15)
- Offer policy is hard-coded in `apps/api/offers.py` as `# SIMPLIFICATION:` constants: `MAX_OFFERS = 3`, `OFFER_VALID_DAYS = 10`, `REAPPLY_AFTER_DAYS = 90`, `CREDIT_LINE_UNUSED_FEE = 0.01`, `PURPOSE_PRODUCT`. Proposed: a shared-folder PR adding an `offers` block to `config/credit_policy.json` and `CreditPolicy` (econ team owns the values), then read them from the policy. (from T17)
- `decision_offered.json` has the "without the current loan" counterfactual at 890k with `resulting_rate: null`; the engine gives 900k at 20.61%. Optional shared-folder PR to bring the fixture and `CONTRACTS.md` in line with `generate_offers`. (from T17)
- Reapply date after an economic decline is the same 90 days for every reason. Per-reason dates (THIN: when history reaches the `FULL` bound; leverage: when the loan closes) need policy values from the econ team. After a stop factor `decide` (`apps/api/workflow.py`) sets no reapply date at all, though `decision_declined.json` shows one. (from T17, T19)
- The SPEC §11 authority matrix is hard-coded in `apps/api/routing.py` as the `# SIMPLIFICATION:` `RoutingPolicy` defaults (auto up to 5M ₽ `FULL` / 1M ₽ `PARTIAL`, underwriter up to 15M ₽, auto grades A–C, committee for D/E above 5M ₽). Proposed: a shared-folder PR adding a `routing` block to `config/credit_policy.json` and `CreditPolicy` (econ team owns the values; also confirm grade E = D), then build `RoutingPolicy` from it. (from T18)
- `Decision.policy_version` is one string, but a decision is built on three policies; `decide` writes `credit=<v>;data_quality=<v>;features=<v>` (`policy_version` in `apps/api/workflow.py`). Proposed: a shared-folder PR replacing it with separate fields (or a `{credit, data_quality, features}` object). (from T16, T19)
