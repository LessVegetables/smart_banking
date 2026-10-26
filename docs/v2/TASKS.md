# Conveyor v2 — tasks

What we build and why: [SPEC.md](SPEC.md) (in Russian). Contracts between tracks: [CONTRACTS.md](CONTRACTS.md). Handoffs between tasks: [NOTES.md](NOTES.md).

## How to use this file

- One task = one branch named after its ID (`t16-limit-pricing`) = one PR into `main`.
- Update **only your own rows**: set `Status` and `Owner / PR` when you start, open a PR, and finish.
- Status values: `todo` → `doing` → `review` → `done`, or `cut`.
- A task can start when everything in `Depends on` is `done`, **or** when it can run on the fixtures from T00 (see "Unblocking").
- Anything you leave for a later task goes into [NOTES.md](NOTES.md) under that task, in the same commit. Before starting, read your task's section there; when finishing, delete the notes you handled.
- A new decision goes into the Decisions log with a date. Don't reopen a logged decision in code or in a PR; raise it with the team instead.

## Decisions log

| Date | Decision |
|---|---|
| 2026-10-24 | SPEC v2 is the working direction for the conveyor. |
| 2026-10-24 | Tasks are tracked in this file. We don't use GitHub Issues. |
| 2026-10-24 | Docs that only described v1 logic were removed (`docs/IMPLEMENTATION.md`, credit sections of `ARCHITECTURE.md`). They remain in git history. |
| 2026-10-24 | Docs are written in Russian. This file, file names, identifiers and code are in English. |
| 2026-10-24 | Being a bank client ≠ having reliable data. Account data gets a quality tier (`FULL` / `PARTIAL` / `THIN`, SPEC §5.1) that drives limit, routing and pre-approval. |
| 2026-10-24 | The "news feed" line on page 7 of the case PDF is an organizer copy-paste error. Ignore it. |
| 2026-10-24 | Contracts v1.0 (T00): money in whole rubles, rates/shares/PD/LGD as fractions, `null` = no data. The client's request (`ApplicationRequest`) is a contract too. Thresholds stay in `config/`. |
| 2026-10-24 | The synthetic world's default outcomes are tuned in `config/world_outcomes.json` (T05): target default rate, effect of `q`, per-industry offsets, shell PD, holdout share. The econ team owns the values. |
| 2026-10-24 | Feature-library policy is in `config/features.json` (T08): overdue-rule thresholds, inquiry and change windows, ОКВЭД risk groups. Its `version` is the feature-set version; changing it means retraining. The econ team owns the values. |
| 2026-10-24 | Scorecard training lives in `apps/ml/` (T10). The trained card is `apps/ml/scorecard_v1.json`; its `feature_set_version` and training data are in `scorecard_v1.training.json`, because the `Scorecard` contract has no field for them. The card trains on every company of the world's training sample, with no reject inference. |
| 2026-10-24 | Data-quality thresholds are in `config/data_quality.json` (T09), separate from `config/features.json`: changing them doesn't change features or require retraining. Its `version` is `DataQuality.policy_version`. The econ team owns the values. |
| 2026-10-24 | Scorecard validation (T11) is `apps/ml/scorecard_v1.validation.json`, regenerated with the card by `python -m apps.ml`. PSI compares the holdout with the training sample: the world is one snapshot, so there is no later population. PSI thresholds 0.1 / 0.25 and 95% intervals are conventional and live in `apps/ml/validation.py`; the econ team confirms them. |
| 2026-10-24 | Scoring (T15): the trained `apps/ml/scorecard_v1.json` decides; the hand-set `apps/api/scorecards/scorecard_v0.json` runs as the shadow challenger, logged and stored in `Decision.challenger` only. Both are set in `.env` (`SCORECARD_CHAMPION`, `SCORECARD_CHALLENGER`). A card's feature-set version is in its passport `scorecard_vN.training.json`; the adapter refuses to score other features. v0 weights: the econ team confirms them. |
| 2026-10-24 | Limit and pricing policy is in `config/credit_policy.json` (T16): DSCR by grade (E = 2.0, not in SPEC), leverage and collateral multipliers, rate components, LGD table, processing costs, the 30% cap (rate + МСП guarantee fee), product ranges. Its `version` is the credit policy version. The econ team owns the values. Limit inputs never use the declared revenue: ФНС revenue, else our account's customer inflows of the last 12 months, else no leverage room. Equipment pledge limit = min(70% of price, price − down payment). |
| 2026-10-24 | Offer generator (T17, `apps/api/offers.py`): variants in SPEC §10 order, at most 3, each priced at the route of its own amount. Standard collateral: owner guarantee for ООО working capital and investment loans, equipment pledge for investment loans, none for ИП, overdraft and credit line. The МСП guarantee is offered only when it gives the requested amount or a rate under the cap. Offers are valid 10 days; with no offer, reapply after 90 days. These values are code constants until a shared-folder PR moves them to `config/credit_policy.json`. |
| 2026-10-24 | Routing (T18, `apps/api/routing.py`): the SPEC §11 matrix with every condition that applies as its own reason; the route is the strictest. A stop factor is an automatic decline; a required source unavailable goes to the underwriter. Grade E is routed like D. Flag reasons are `route_flag_<flag code>`; the client sees only who reviews and how long, or `DataQuality.client_message` for the tier. Thresholds (1M, 5M, 15M, grades A–C) are code constants in `RoutingPolicy` until a shared-folder PR moves them to `config/credit_policy.json`. |
| 2026-10-24 | Workflow (T19, `apps/api/workflow.py`): v2 applications are `loan_applications` with an append-only audit log `application_events`; the fact store now points at them. Statuses change only through `transition` (SPEC §13 table). Company not found → `declined`; a required source unavailable or the card not fitting the features → `in_review`; auto route with offers → `offered`, without → `declined`; underwriter / committee → `in_review` with the offers kept. The application takes the strictest route among its offers. Features and stop factors are computed on the world's `as_of`; offers are dated from the real decision day. `Decision.policy_version` = `credit=…;data_quality=…;features=…`. v1 code stays until the UI tasks replace it. |
| 2026-10-24 | Staff actions (T20, `apps/api/staff_actions.py`): approve, change, decline, request data and re-run on `in_review`, each with a reason code and a comment. Approved or changed offers are re-routed by their own amount; a committee route needs an underwriter's and a risk manager's signatures, two different users. Decline and data requests always take one signature. Staff can't go above the computed limit (`Offer.amount <= limit.limit`). Staff position (`underwriter` / `risk_manager`) is picked at login with the one `STAFF_PASSWORD`. An override is approving only some offers, changing them, or declining when there were offers. Reason codes are code constants until a shared-folder PR moves them to `config/`. |
| 2026-10-24 | Statement upload (T23, `apps/api/statements.py`): `THIN` without a statement → `needs_data` with a statement request; staff `request_data` is answered the same way. The client uploads a mock-parsed JSON statement (or goes on without one); `needs_data → enriching` re-runs on the stored source answers (no second БКИ inquiry). Statements join our account month by month for data quality and the limit only; features, stop factors and the score stay on our own data. An application with a statement always goes to the underwriter (`route_statement_upload`). A `PARTIAL` client with automatic offers can't upload (no `offered → enriching` in SPEC §13). |

| 2026-10-24 | Overdraft pre-approval (T22, `apps/api/preapproval.py`): a daily batch over every client runs the application's stages without an application: `FULL` tier only, no stop factors and no flags, the champion's score, a 12-month overdraft `quote` at the auto route; the amount's own route must be auto, at least the product minimum and under the rate cap. The bureau is queried on a standing 218-ФЗ consent assumed from account opening (`# MOCK:`). The latest result per ИНН is in `preapprovals`, valid 30 days; the application is still decided anew. |
| 2026-10-25 | LLM adapter (T24, `apps/api/assistant.py`): any OpenAI-compatible server through `{LLM_BASE_URL}/chat/completions`; the model is on when `LLM_BASE_URL` and `LLM_MODEL` are set, `LLM_API_KEY` is optional (local servers need none). No other switch: `ASSISTANT_LLM_ENABLED` and `OPENAI_*` are gone. No model is bundled in compose; the README shows an open-weights example (Qwen2.5-7B-Instruct via Ollama or vLLM). Timeout 30 s and 500 tokens are code constants. |
| 2026-10-25 | Demo personas (T12, `apps/world/personas.py`): one company per demo outcome from the default world (seed 42, 5000), the first in world order whose decision passes the target's check; expected outcomes are the conveyor's own, in `apps/world/demo/personas.json`, with other-bank statements for the `PARTIAL` and `THIN` personas in `apps/world/demo/statements/`. Selection reads the hidden archetype and account profile (now stored on `Company`, generator `world-t12-1`), nothing else hidden. The `committee` persona has no offers: no company reaches the committee with one on this world. The econ team confirms the table. |
| 2026-10-25 | T25 company-card lookup uses `GET /api/v2/companies/{inn}` backed only by the synthetic EGRUL/EGRIP source. It returns public company-card fields and the main owner's INN required to identify the BKI consent subject; it does not bind the logged-in user to that company. |
| 2026-10-25 | Demo access uses a stable profile per entered name and offers every current T12 persona company. The catalog is intersected with the current synthetic world; if no persona remains, one current company is exposed as a fallback, rebuilding the default world only if the current world itself is empty. A demo profile may submit for any company in that catalog, so changing companies does not strand the demo user. |
| 2026-10-26 | Analytics docs for the built system live in `docs/` (T35): `PROCESS.md`, `DATA_MODEL.md`, `SLA.md`, `SIMPLIFICATIONS.md` (the simplification registry; new `# MOCK:` / `# SIMPLIFICATION:` markers get a row there). `docs/v2/` stays the planning set. `README.md`, `ARCHITECTURE.md` and `VPS.md` describe v2 only; v1 leftovers are to be deleted, not documented. |
| 2026-10-26 | The client types the company ИНН by hand; the UI no longer lists the demo persona catalog, so the cabinet looks like a real one. The API still limits a demo profile to the T12 catalog (2026-10-25); the UI shows that 403 as "company not linked to your profile". `GET /api/v2/demo/companies` stays for company names in the applications list. |

## Open questions

| Question | Default if nobody decides |
|---|---|
| Deadline (sets where we stop on the cut list) | — |
| Overdraft as the pre-approved fast lane (T22, part of T29)? | Yes |
| Statement upload for `PARTIAL` / `THIN` clients (T23)? | Stretch |
| Trained scorecard (T10) or hand-set weights only? | Start hand-set (`scorecard_v0.json`), switch to trained when T10 is done |
| Thresholds: DSCR minimums, LGD table, grade bounds, 30% rate cap, data-quality tiers | Econ team owns these and confirms them in `config/` |

## Tracks and folder ownership

| Track | Scope | Owns | Owner |
|---|---|---|---|
| 1 — World & sources | Synthetic world, mock sources, enrichment, features, ML | `apps/world/`, `apps/sources/`, `apps/api/features/`, `apps/ml/` | _ |
| 2 — Engine & workflow | Facts, decision engine, statuses, staff API | `apps/api/engine/`, `apps/api/` workflow modules | _ |
| 3 — Interfaces | Client and staff UI | `apps/web/`, `apps/mini/` | _ |
| Shared | Changes need a PR approved by all three | `apps/api/contracts.py`, `tests/fixtures/`, `config/` | all |
| Econ team | Thresholds, persona outcomes, analytics docs | `config/` values, `docs/` | _ |

## Tasks

Sizes: S ≤ 1 day, M 2–3 days, L 4–5 days.

### Day 0 — all together

| ID | Task | Size | Depends on | Status | Owner / PR |
|---|---|---|---|---|---|
| T00 | Contracts: Pydantic models in `apps/api/contracts.py` + example JSON per source in `tests/fixtures/` (source payloads, `Fact`, `DataQuality`, features, engine output) | S | — | done | PR #1 |

### Track 1 — World & sources

| ID | Task | Size | Depends on | Status | Owner / PR |
|---|---|---|---|---|---|
| T01 | World generator: companies, latent quality `q`, industries, registry data (seeded, reproducible) | S | T00 | done | PR #2 |
| T02 | Monthly account turnovers by category (customers, suppliers, payroll, taxes, rent, loans, cash, own transfers), seasonality and trend | M | T01 | done | PR #3 |
| T03 | Credit-bureau histories for company and owner: loans, delinquencies, inquiries | M | T01 | done | PR #4 |
| T04 | Fraud archetypes (shell, inflater), declared-vs-real noise, incomplete-data clients (main account elsewhere, new account, inflated by own transfers) | S | T02, T03 | done | PR #5 |
| T05 | Default outcomes + train/holdout split | S | T01–T04 | done | PR #6 |
| T06 | Mock source service: ЕГРЮЛ/ЕГРИП, ФНС (+ГИР БО), БКИ, ФССП, арбитраж/Федресурс, 115-ФЗ, internal accounts; configurable latency and failures | M | T00 (fixtures), T01–T04 (real data) | done | PR #7 |
| T07 | Source adapters + enrichment orchestrator: parallel calls, timeouts, retries, per-source failure policy (SPEC §5) | M | T06 | done | PR #8 |
| T08 | Shared feature-extraction library, used by training **and** the live engine; includes the MVP overdue rules | M | T00 | done | PR #9 |
| T09 | Account data-quality metrics and tier `FULL` / `PARTIAL` / `THIN` (SPEC §5.1) | S | T08 | done | PR #11 |
| T10 | Scorecard training: WoE binning, logistic regression, points scaling, export `scorecard_v1.json` | M | T05, T08 | done | PR #10 |
| T11 | Validation metrics: Gini on holdout, calibration by grade, PSI → JSON for the UI | S | T10 | done | PR #13 |
| T12 | Demo personas picked from the world (one per outcome, ИП and ООО, fraud, each data-quality tier) + expected-outcomes table with the econ team | S | T05, T18 | done | PR #25 |

### Track 2 — Engine & workflow

| ID | Task | Size | Depends on | Status | Owner / PR |
|---|---|---|---|---|---|
| T13 | Fact store and data model: declared vs verified value, source, trust level, timestamp; DB migrations | M | T00 | done | PR #12 |
| T14 | Stop-factors and flags (SPEC §7) | S–M | T08, T13 | done | PR #14 |
| T15 | Scoring adapter: loads scorecard JSON (`scorecard_v0.json` hand-set first), model versioning, optional challenger in shadow mode | S | T00 | done | PR #15 |
| T16 | Limit (CFADS, DSCR, caps, data-quality tier) + risk-based pricing (SPEC §9) | M | T09, T15 | done | PR #16 |
| T17 | Offer generator + counterfactual explanations (SPEC §10) | M | T16 | done | PR #17 |
| T18 | Routing by authority matrix, including data-quality tier (SPEC §11) | S | T09, T14, T15 | done | PR #18 |
| T19 | Application state machine v2 + events + audit log (SPEC §13) | M | T13 | done | PR #19 |
| T20 | Staff actions API: review, override with reason code, committee with two signatures | M | T18, T19 | done | PR #20 |
| T21 | Rework documents, signing, pre-disbursement conditions, disbursement (mock; payout to supplier for equipment loans) | S | T19 | done | PR #21 |
| T22 | Overdraft pre-approval batch (`FULL` tier only) | S | T09, T16 | done | PR #22 |
| T23 | Statement upload for `PARTIAL` / `THIN` clients: mock parsing into a lower-trust source, then recompute | S–M | T09, T19 | done | PR #23 |
| T24 | LLM adapter: OpenAI-compatible, only `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`, no hard-coded provider. **Hard constraint** — the current `apps/api/assistant.py` calls `api.openai.com` | S | — | done | PR #24 |

### Track 3 — Interfaces

| ID | Task | Size | Depends on | Status | Owner / PR |
|---|---|---|---|---|---|
| T25 | Client: ИНН entry → pre-filled company card → consents (БКИ 218-ФЗ, ПДн 152-ФЗ) | M | T00 | done | PR #26 |
| T26 | Need intake: 3–6 questions + product recommendation (replaces the 43-question survey) | S–M | T00 | done | PR #27 |
| T27 | "Analyzing your business" screen with live per-source status | S | T07, T19 | done | PR #28 |
| T28 | Offer menu: trade-offs, binding limit, counterfactuals, decline screen, data-quality message | M | T17 | done | PR #29 |
| T29 | Signing → disbursed happy end + pre-approval banner | S | T21, T22 | done | PR #30 |
| T29a | Stable demo profile, selectable synthetic companies, v2 application and financing history | M | T12, T19, T21, T25 | done | PR #31 |
| T30 | Staff dossier: sources, declared vs verified, data-quality metrics, flags, score contributions, limit and price breakdowns, audit log | L | T14–T19 | done | PR #32 |
| T31 | Staff actions UI + committee second signature | M | T20 | done | PR #35 |
| T32 | Risk-manager page with model validation results | S | T11 | done | PR #36 |

### Shared

| ID | Task | Size | Depends on | Status | Owner / PR |
|---|---|---|---|---|---|
| T33 | Golden engine tests per persona (expected-outcomes table) + one end-to-end smoke test | M | T12, T17 | done | PR #37 |
| T34 | Docker compose, OpenAPI, request collection, `THIRD_PARTY_NOTICES` | S | — | done | PR #38 |
| T35 | Docs in Russian: process model, state diagram, data model, SLA table, simplification registry; rewrite `README.md` and `ARCHITECTURE.md` for v2 | M | — | done | PR #39 |

### Stretch

| ID | Task | Size | Depends on | Status | Owner / PR |
|---|---|---|---|---|---|
| T36 | Policy simulator: move cutoffs or DSCR, see approval rate, expected loss and profit on the synthetic portfolio | M | T10, T16 | todo | |

## Unblocking

- Track 2 doesn't wait for the world: it builds on the T00 fixtures.
- T15 starts with hand-set weights in the same JSON format as the trained file; T10 swaps the file later.
- T08 is the riskiest joint. Training and the live engine must compute features with the same code, so it has one owner.
- Track 3 can build screens against the T00 fixtures before the API is ready.

## Cut order (if time runs short, cut from the top)

1. T36 policy simulator
2. Challenger shadow mode (in T15); committee second signature (in T20, T31)
3. T22 overdraft pre-approval and the banner in T29
4. ФССП and арбитраж sources in T06 (keep ЕГРЮЛ, ФНС, БКИ, accounts, 115-ФЗ)
5. T23 statement upload (`THIN` clients go straight to the underwriter)
6. T10, T11, T32 trained model (keep hand-set weights, describe training in the docs)

### Maintenance

| ID | Task | Size | Depends on | Status | Owner / PR |
|---|---|---|---|---|---|
| T38 | Remove unused v1 runtime, routes, storage dependencies and build artifacts; use v2 assistant context; preserve historical database migrations and data | M | T35 | done | Local cleanup; 474 tests, TypeScript and production build passed |
