# Handoff notes between tasks

Everything one task leaves for another lives here, filed under the task that has to act on it.

## Rules

- **Deferring something** ("left for T14", "T19 should decide", a known limitation someone will hit): add a note under the task that should act, in the same commit. If no task fits, put it under **Unassigned** and tell the user.
- **Starting a task:** read your task's section and **Any task**. Also run `git grep "TODO(T16)"` with your ID.
- **Finishing a task:** delete the notes your PR handled. Notes you decided not to handle stay, with one line saying why.
- A note is 1–3 lines: what, where in the code, what to do. Long explanations belong in the component's docs; the note links to them.
- Optional: mark the exact spot in code with `# TODO(T14): …`. The note here is still required.

## Any task

- `q`, `archetype`, `account_profile`, `pd_12m` and `default_12m` are hidden truth of the synthetic world. They must never reach features, the engine or the UI; only training labels (T10) and validation (T11) may read the outcome, and only the persona selection (T12) reads the archetype and the account profile. (from T01–T05, T12, `apps/world/README.md`)
- New data in the world gets its own `company_rng` stream; hash tests pin what T01–T05 generate. (from T01, `apps/world/README.md`)

## T15 — scoring adapter

- T14 emits `source_unavailable_fns`, `source_unavailable_fssp` and `source_unavailable_arbitration` flags. Apply a score penalty for each degraded source after the econ team defines penalty values; no such values exist in the current policy. (`apps/api/stop_factors.py`)
  - Not handled in T15: the values belong in `config/` (shared-folder PR, econ team). Then apply them in `Scorer.score` (`apps/api/scoring.py`) and show each penalty as its own row in `ScoreResult.contributions`, so points still add up. (T15)


## T25 — ИНН entry and consents

- `POST /api/v2/applications` still accepts any request ИНН from any logged-in client: the new registry lookup is not a user → company link. Add ownership binding before relying on demo profiles or exposing company-specific pre-approval. (from T19)
- The pre-approval banner (T29) uses the company selected in the current tab and requires its three saved application consents, but this is only a UI guard: `GET /api/v2/preapproval?inn=…` still accepts any ИНН and must enforce user → company ownership server-side (`apps/api/main.py`, `preapproval_v2`). The batch assumes a standing 218-ФЗ consent given at account opening (`# MOCK:` in `standing_request`, `apps/api/preapproval.py`); T25's application checkboxes are transient and do not create that consent register. (from T22)
- T25 removes the v1 persona chooser from the client start path but does not expose T12 persona shortcuts. If the demo needs selectable profiles, expose only curated fields from `apps/world/demo/personas.json`; never show `archetype`. (from T12, `apps/world/PERSONAS.md`)

## T29 — signing → disbursed, pre-approval banner

## T30 — staff dossier

- `ScoreResult.contributions` has one row per card feature but not the base points: show `points - sum(contributions)` as «базовые баллы». The challenger (`Decision.challenger`) is for staff only, labelled as a shadow model that didn't affect the decision. (from T15)
- Limit breakdown: every `LimitConstraint` carries its `inputs` and `note`; the `leverage` note says when the revenue came from our account because ФНС has none. `PriceBreakdown` components add up to the rate. (from T16, `apps/api/LIMITS.md`)
- Our own БКИ request doesn't appear in the report. If the dossier should show it, ask track 1 to add it in the БКИ adapter (`apps/sources/`). (from T06)
- "Почему заявка пришла сюда" is `Decision.route_reasons[].staff_text`, one row per condition; the code says which matrix row fired (`apps/api/ROUTING.md`). `Decision` doesn't store each reason's own route (`Routing.routes` does): derive it from the code table if the dossier needs it. (from T18)
- Staff actions are in the audit log as `review`, `proposal` and `signature` events with the reason code, comment, position and `override`; `pending_review` is a committee decision waiting for its second signature (`apps/api/STAFF_ACTIONS.md`). Staff reasons are `Decision.reasons` with code `staff_<reason code>`. (from T20)
- Statements (T23): `statements` in the staff view (parsed months, file name, SHA-256), facts with source `statement_upload` / `client_document` next to our account's, flags `statement_period` and `statement_inflows_above_fns`, route reason `route_statement_upload`. `DataQuality` is with the statements; the tier on our account alone is `own_account_tier` in the run's `action` event. (from T23, `apps/api/STATEMENTS.md`)
- `GET /api/v2/applications/{id}` as staff returns `decision`, `data_quality`, `sources` (envelope per source: status, timestamps, latency, attempts, error), `facts` (fact store) and the full `events`; `GET .../events` is the audit log alone (`apps/api/WORKFLOW.md`). Full source payloads are in `LoanApplication.enrichment` if the dossier needs them. (from T19)
- Signing, conditions and payout are `closing` in the staff view: `signature`, `conditions[].detail` (pledge registration number, invoice check, failure reason), `payout` (account or supplier, payment id). Code issues, wrong codes and condition checks are `action` events in the audit log (`apps/api/DISBURSEMENT.md`). (from T21)

## T31 — staff actions UI

- API: `POST /api/v2/applications/{id}/review` and `.../review/sign`, codes from `GET /api/v2/staff/reason-codes` (per action, plus `reject` for turning a committee proposal down), stats from `GET /api/v2/staff/overrides` (`apps/api/STAFF_ACTIONS.md`). Send `revision`; 409 = stale or waiting for a signature, 422 = text to show as is (e.g. "сумма выше лимита, доступно до …"). (from T20)
- `outcome: proposed` means the committee: the application stays `in_review` with `pending_review` (proposal, `decision` to sign, `signatures`). The second signer must be the other position and another user. Staff log in with `POST /api/auth/staff` `{"password", "position": "underwriter" | "risk_manager"}`; `GET /api/auth/me` returns `staff_position`. To demo both signatures, use two browser profiles: the session is one cookie. (from T20)
- The v1 staff screen (`/api/staff/reviews`) still serves v1 applications only; the v2 queue is `GET /api/v2/applications?status=in_review`. (from T20)
- No persona can demo the committee's two signatures: the `committee` persona (ИНН `3664000050`) reaches the committee route with no offers, because grade D–E companies get a zero limit and grades A–C reach the committee only above the 15M ₽ product maximum. Staff can only decline or request data, which takes one signature. Waiting on the econ team (`things_for_the_econ_team_todo.md`, T12 §3); if they lower the committee bound or DSCR, regenerate the personas. (from T12)

## T32 — risk-manager page

- To compare the champion with the shadow challenger, `validate_scorecard(world, card)` (`apps/ml/validate.py`) also works for `apps/api/scorecards/scorecard_v0.json`; holdout Gini is 0.32 for v0 vs 0.46 for v1 (`apps/api/SCORING.md`). Optional. (from T15)
- The page shows `apps/ml/scorecard_v1.validation.json`, regenerated by `python -m apps.ml` with the card. Format: `ValidationReport` in `apps/ml/validation.py` (imports only the contracts, so the API can load it without the world). No API endpoint serves it yet: add a read-only GET in `apps/api/` (agree with track 2). (from T11, `apps/ml/README.md`, «Валидация»)
- Show `limitations` next to the metrics, and the true-PD ceiling next to the holdout Gini. `predicted_vs_actual: below` means the card underestimates risk in that grade (grade C now). (from T11)
- `ValidationReport` is not a contract. If the UI wants generated types for it, propose a shared-folder PR moving it into `apps/api/contracts.py`. (from T11)

## T33 — golden tests

- Expected offer sets per persona come from `generate_offers` (`apps/api/offers.py`). The fixture `decision_offered.json` says 890k for the "without the current loan" hint; the engine gives 900k because it re-prices the new amount. Use engine numbers in the expected-outcomes table, not the fixture's. (from T17)
- The expected-outcomes table is `apps/world/demo/personas.json` (`read()` in `apps/world/personas.py`), built by running each persona through `decide` with mock enrichment (`Conveyor`). `tests/test_world_personas.py` already checks it stays in sync at the `decide` level; T33's job is the run through the API and worker: submit, statement upload, staff actions. Personas need the default world (seed 42, 5000 companies). (from T12, `apps/world/PERSONAS.md`)

## T34 — Docker

- The API has no logging config of its own: `init_db` runs Alembic, whose `alembic.ini` sets the root logger to WARN, so INFO lines (e.g. the champion-vs-challenger line from `apps/api/scoring.py`) don't reach `docker logs`. Configure app logging (level from `.env`) at startup. (from T15)
- The Dockerfile copies only `apps/api` and `config`: add `apps/ml/` so the scoring adapter (`apps/api/scoring.py` imports `apps.ml`) finds `scorecard_v1.json`, its training passport and `apps.ml.score` in the image, and the risk-manager page (T32) finds `scorecard_v1.validation.json`. `apps.ml.train` needs `apps/world/`, but nothing imports it at runtime. The challenger card `apps/api/scorecards/` is already copied with `apps/api`. Set `SCORECARD_*` from `.env.example` in compose. (from T10, T15)
- `MOCK_SOURCES_*` and `WORLD_*` are in `.env.example`. So the API doesn't rebuild the world on every start, write it to `apps/world/out` when building the image. (from T07)
- The v2 worker runs in the API process (`WORKER_ENABLED`, `worker.loop` → `workflow.tick`) and loads the world on the first v2 application (`default_pipeline`, ~5 s without `apps/world/out`). Mock source latency (`MOCK_SOURCES_*`) applies to every demo run. (from T19)
- The `source_down` persona needs `MOCK_SOURCES_SCENARIOS={"2309002984": {"bki": "error"}}` in `.env.example` and compose. Personas are picked from the default world, so leave `WORLD_SEED` / `WORLD_N` / `WORLD_AS_OF` at their defaults. If the UI serves the demo statement files, copy `apps/world/demo/` into the image. (from T12)

## T35 — docs

- The simplification registry should take the T21 list from `apps/api/DISBURSEMENT.md`, «Упрощения»: code shown on screen instead of SMS, one code signs the guarantee too, mock pledge registry / supplier invoice / МСП guarantee, payout without real postings. (from T21)
- Simplification registry: statement upload has no recognition (`# MOCK:`, the file is already parsed JSON), transfers between our account and the other bank aren't netted, the statement's authenticity isn't checked, check thresholds are code constants (`apps/api/STATEMENTS.md`, «Упрощения»). (from T23)
- Simplification registry: the LLM assistant's timeout (30 s) and answer limit (500 tokens) are code constants in `apps/api/assistant.py`; tests check the request format and the template fallback, never a live model (`README.md`, «Языковая модель»). (from T24)
- Simplification registry: demo personas are picked by the world's hidden archetype; the other bank's statements come from one made-up bank; a client with a new account moved its whole business to us at once (`apps/world/PERSONAS.md`, «Выписки из другого банка»; `apps/world/README.md`, «Упрощения»). (from T12)

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
- Staff reason codes are `# SIMPLIFICATION:` constants `REASON_CODES` / `REJECT_REASON_CODES` in `apps/api/staff_actions.py`. Proposed: a shared-folder PR moving them into `config/` as a versioned directory (econ team and risk owners agree the list). (from T20)
- Pre-approval values are `# SIMPLIFICATION:` constants in `apps/api/preapproval.py`: `VALID_DAYS = 30`, `BATCH_EVERY_SECONDS` (a day). Proposed: move them with the T17 offer values into an `offers` block of `config/credit_policy.json` (shared-folder PR, econ team owns the values). `PreApproval` is not a contract either: if the UI wants generated types, propose moving it into `apps/api/contracts.py`. (from T22)
- Staff can't approve more than the computed limit: `Offer` requires `amount <= limit.limit`, so `change` refuses it (422). If underwriters should be able to go above the limit with a reason code, that needs a contract change (e.g. an override field on `Offer` or `LimitBreakdown`). (from T20)
- A failed pre-disbursement condition (today only a supplier invoice that doesn't match the loan) leaves the application in `conditions` for good: `TRANSITIONS` has only `conditions → disbursed`, and no staff or back-office action exists (SPEC §12 calls back office optional). Decide on a path (staff fixes and re-checks, or `conditions → declined` / `withdrawn`), then add it in `apps/api/disbursement.py`. (from T21)
- Signing has no deadline: documents can be signed after the offer's `valid_until`, and there's no `documents → expired` transition. If the bank wants one, add it to `TRANSITIONS` and check it in `disbursement.sign`. (from T21)
- The uploaded statement format (`Statement` in `apps/api/statements.py`: bank, ИНН, `MonthlyTurnover` months) is a client → pipeline exchange but not a contract. Proposed: a shared-folder PR moving it into `apps/api/contracts.py` with a fixture in `tests/fixtures/`. (from T23)
- Statement policy is `# SIMPLIFICATION:` constants in `apps/api/statements.py`: `STALE_MONTHS = 3`, `FNS_EXCESS = 0.30`, `MAX_FILES = 5`, `MAX_FILE_BYTES`. Proposed: a shared-folder PR adding them to `config/data_quality.json` (econ team owns the values). (from T23)
- An application in `needs_data` waits for the client forever: SPEC §13 has no timeout for it, only `withdrawn`. If a data request should expire (e.g. after the 10 days an offer lasts), agree the term and add the transition. (from T23)
