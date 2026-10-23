# Conveyor v2 — tasks

What we build and why: [SPEC.md](SPEC.md) (in Russian). Contracts between tracks: [CONTRACTS.md](CONTRACTS.md).

## How to use this file

- One task = one branch named after its ID (`t16-limit-pricing`) = one PR into `main`.
- Update **only your own rows**: set `Status` and `Owner / PR` when you start, open a PR, and finish.
- Status values: `todo` → `doing` → `review` → `done`, or `cut`.
- A task can start when everything in `Depends on` is `done`, **or** when it can run on the fixtures from T00 (see "Unblocking").
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
| 1 — World & sources | Synthetic world, mock sources, enrichment, features, ML | `apps/world/`, `apps/sources/`, `apps/api/features/` | _ |
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
| T04 | Fraud archetypes (shell, inflater), declared-vs-real noise, incomplete-data clients (main account elsewhere, new account, inflated by own transfers) | S | T02, T03 | review | |
| T05 | Default outcomes + train/holdout split | S | T01–T04 | todo | |
| T06 | Mock source service: ЕГРЮЛ/ЕГРИП, ФНС (+ГИР БО), БКИ, ФССП, арбитраж/Федресурс, 115-ФЗ, internal accounts; configurable latency and failures | M | T00 (fixtures), T01–T04 (real data) | todo | |
| T07 | Source adapters + enrichment orchestrator: parallel calls, timeouts, retries, per-source failure policy (SPEC §5) | M | T06 | todo | |
| T08 | Shared feature-extraction library, used by training **and** the live engine; includes the MVP overdue rules | M | T00 | todo | |
| T09 | Account data-quality metrics and tier `FULL` / `PARTIAL` / `THIN` (SPEC §5.1) | S | T08 | todo | |
| T10 | Scorecard training: WoE binning, logistic regression, points scaling, export `scorecard_v1.json` | M | T05, T08 | todo | |
| T11 | Validation metrics: Gini on holdout, calibration by grade, PSI → JSON for the UI | S | T10 | todo | |
| T12 | Demo personas picked from the world (one per outcome, ИП and ООО, fraud, each data-quality tier) + expected-outcomes table with the econ team | S | T05, T18 | todo | |

### Track 2 — Engine & workflow

| ID | Task | Size | Depends on | Status | Owner / PR |
|---|---|---|---|---|---|
| T13 | Fact store and data model: declared vs verified value, source, trust level, timestamp; DB migrations | M | T00 | todo | |
| T14 | Stop-factors and flags (SPEC §7) | S–M | T08, T13 | todo | |
| T15 | Scoring adapter: loads scorecard JSON (`scorecard_v0.json` hand-set first), model versioning, optional challenger in shadow mode | S | T00 | todo | |
| T16 | Limit (CFADS, DSCR, caps, data-quality tier) + risk-based pricing (SPEC §9) | M | T09, T15 | todo | |
| T17 | Offer generator + counterfactual explanations (SPEC §10) | M | T16 | todo | |
| T18 | Routing by authority matrix, including data-quality tier (SPEC §11) | S | T09, T14, T15 | todo | |
| T19 | Application state machine v2 + events + audit log (SPEC §13) | M | T13 | todo | |
| T20 | Staff actions API: review, override with reason code, committee with two signatures | M | T18, T19 | todo | |
| T21 | Rework documents, signing, pre-disbursement conditions, disbursement (mock; payout to supplier for equipment loans) | S | T19 | todo | |
| T22 | Overdraft pre-approval batch (`FULL` tier only) | S | T09, T16 | todo | |
| T23 | Statement upload for `PARTIAL` / `THIN` clients: mock parsing into a lower-trust source, then recompute | S–M | T09, T19 | todo | |
| T24 | LLM adapter: OpenAI-compatible, only `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`, no hard-coded provider. **Hard constraint** — the current `apps/api/assistant.py` calls `api.openai.com` | S | — | todo | |

### Track 3 — Interfaces

| ID | Task | Size | Depends on | Status | Owner / PR |
|---|---|---|---|---|---|
| T25 | Client: ИНН entry → pre-filled company card → consents (БКИ 218-ФЗ, ПДн 152-ФЗ) | M | T00 | todo | |
| T26 | Need intake: 3–6 questions + product recommendation (replaces the 43-question survey) | S–M | T00 | todo | |
| T27 | "Analyzing your business" screen with live per-source status | S | T07, T19 | todo | |
| T28 | Offer menu: trade-offs, binding limit, counterfactuals, decline screen, data-quality message | M | T17 | todo | |
| T29 | Signing → disbursed happy end + pre-approval banner | S | T21, T22 | todo | |
| T30 | Staff dossier: sources, declared vs verified, data-quality metrics, flags, score contributions, limit and price breakdowns, audit log | L | T14–T19 | todo | |
| T31 | Staff actions UI + committee second signature | M | T20 | todo | |
| T32 | Risk-manager page with model validation results | S | T11 | todo | |

### Shared

| ID | Task | Size | Depends on | Status | Owner / PR |
|---|---|---|---|---|---|
| T33 | Golden engine tests per persona (expected-outcomes table) + one end-to-end smoke test | M | T12, T17 | todo | |
| T34 | Docker compose, OpenAPI, request collection, `THIRD_PARTY_NOTICES` | S | — | todo | |
| T35 | Docs in Russian: process model, state diagram, data model, SLA table, simplification registry; rewrite `README.md` and `ARCHITECTURE.md` for v2 | M | — | todo | |

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
