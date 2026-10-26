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

- Non-demo client accounts still lack a persistent user → company link. The built-in demo profile is restricted to the current T12 company catalog in `apps/api/main.py`; add a real ownership binding before applying company-specific access rules to Telegram clients (`company_card_v2`, `submit_v2`, `preapproval_v2`). (from T19, T22, T29a)
- The pre-approval batch assumes a standing 218-ФЗ consent given at account opening (`# MOCK:` in `standing_request`, `apps/api/preapproval.py`); T25's application checkboxes are transient and do not create that consent register. (from T22)

## T29a — stable demo companies and application history

- The curated T12 persona file can become stale after changing `WORLD_SEED`, `WORLD_N`, `WORLD_AS_OF`, the generator, or policy. Regenerate it with `python -m apps.world.personas`; until then the API exposes a fallback company from the current world (`apps/api/demo_companies.py`). (from T12)

## T30 — staff dossier

- The dossier shows the BKI source call and its status. A separate record of our own bureau inquiry is still absent from the adapter payload, so the UI cannot display it as an inquiry event; Track 1 would need to expose it if the team wants that detail. (from T06)

## T31 — staff actions UI

- No persona can demo the committee's two signatures: the `committee` persona (ИНН `3664000050`) reaches the committee route with no offers, because grade D–E companies get a zero limit and grades A–C reach the committee only above the 15M ₽ product maximum. The T31 screen supports the signature flow, but end-to-end demo still needs an application with a committee proposal. (from T12)

## T32 — risk-manager page

- Challenger comparison remains optional and is deferred: the committed validation artifact is generated for the champion only; producing and maintaining a second report is outside T32's screen scope. (`apps/ml/validate.py` can validate v0 when needed.) (from T15)
- Not handled: the web client maintains its API types manually, so T32 does not need a generated shared contract. (`ValidationReport` remains in `apps/ml/validation.py`.) (from T11)

## T36 — policy simulator

- Pass a changed `CreditPolicy` (`load_policy(path)` or `default_policy().model_copy(...)`, `apps/api/credit_policy.py`) into `quote` / `limit_breakdown`; nothing in T16 reads global state besides the default policy. (from T16)
- Routing thresholds are `RoutingPolicy` (`apps/api/routing.py`): pass a changed one to `route_application` / `route_for`, e.g. to see how the auto-decision share moves with the 5M ₽ bound. (from T18)

## Unassigned

- `compose.vps.yaml` passes only an explicit list of variables, so the VPS stack gets none of `MOCK_SOURCES_*`, `SCORECARD_*`, `LOG_LEVEL` or `SOURCE_*`: there the `source_down` persona gets a normal bureau answer and INFO logs stay at the default. Add `env_file` or the variables there if the VPS demo needs them (T34 left the deploy setup alone). The nginx route (`deploy/nginx/compose.nginx.yaml` over `compose.yaml`, `VPS.md`) reads the whole `.env` and doesn't have this gap. (from T34)
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
- The `not_found` persona (`apps/world/demo/personas.json`) can't be shown in the demo: a demo client may submit only ИНН from the catalog (`available_demo_inns`, `apps/api/demo_companies.py`), which holds only companies of the world, so the API answers 422. Let the catalog offer the `not_found` ИНН too, or document it as a demo limitation. `tests/test_golden_personas.py` submits it as a non-demo client. (from T33)
- v1 code is still in the repo though no v2 screen leads to it: `apps/api/engine.py`, `catalog.py`, `personas.py`, `storage.py` (and the `S3_*` / `AWS_*` variables in `.env.example`), the `/api/surveys`, `/api/applications`, `/api/documents`, `/api/staff/reviews`, `/api/catalog`, `/api/personas` routes in `main.py`, the 43-question survey and v1 application screens in `apps/web/src/main.tsx`, the `surveys` / `applications` / `submission_keys` / `documents` / `jobs` tables (needs a migration), `scripts/smoke.py`, `releases/alfa_hack-vps.zip` (+`.sha256`), the committed `dist/` (Docker builds its own), "компоненты опроса" in `apps/mini/README.md`. `config/rules.json` goes in a shared-folder PR. The docs already describe v2 only. (from T35)
- Need intake sends `declared_annual_revenue: null` (`apps/web/src/need-intake.tsx`), so the `inflater` persona's revenue-mismatch flag can't be shown in the UI: there it gets an automatic 590 000 ₽ offer instead of `in_review`. Ask for declared revenue on the need screen, or keep it as the documented demo limitation (`README.md`, «Что нельзя показать через интерфейс»). (from T35)
