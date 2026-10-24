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

## T08 — feature extraction

- Declared revenue covers the last 12 months; ФНС `annual_revenue` is the last filed calendar year, and only part of a year for companies registered last year. Compared head-on, the >30% gap appears for 21% of honest companies with a full year and 82% of those registered last year. Annualize part-year reports and/or compare with `operating_inflows_12m`. (from T04/T07, `apps/world/README.md` «Известные ограничения»)
- The SPEC §5 overdue rules (technical delays, small amounts, weight of old episodes) are applied here, not in the world. The world has cases for every rule (`test_world_has_cases_for_every_overdue_rule`). (from T03)

## T09 — data-quality tier

- Sanity check: with SPEC §5.1 thresholds, regular companies come out at about 75% `FULL`, 18% `PARTIAL`, 7% `THIN` (seed 7, 3000 companies). `THIN` is mostly young companies. (from T02, `apps/world/README.md`)

## T10 — scorecard training

- Every company in the world has an outcome, as if the bank had lent to all of them. Real banks only know outcomes of issued loans (reject inference). Decide which companies go into training and say so in the docs. (from T05)
- The train/holdout split isn't stratified by outcome: default rates differ by chance (9.9% vs 7.2%, seed 7, 3000 companies). Stratify at training time if needed; the world doesn't have to change. (from T05)

## T11 — validation metrics

- Holdout is a random split on one snapshot, not out-of-time; list this as a limitation on the validation page. (from T05)

## T12 — demo personas

- Few young companies: about 4% are under 6 months, 13% under a year. If personas need more, shift the age distribution in `_company` (`apps/world/`) or add a young-company group. (from T04)
- Every archetype has candidates among both ИП and ООО; the shell archetype is ООО only. (from T04)
- Per-persona source failures (e.g. ФССП down) go in `MOCK_SOURCES_SCENARIOS`; the world doesn't change. For sampling without latency use `payload(company, source)`. (from T06/T07, `apps/sources/README.md`)

## T13 — fact store

- `Enrichment` round-trips through `model_dump_json` / `model_validate_json`; its facts are contract `Fact` objects. (from T07)

## T14 — stop-factors and flags

- Revenue mismatch flag: see the T08 note. Head-on comparison will flag far too many honest clients.
- "3+ credit inquiries in 30 days" must count other banks' inquiries only; our application leaves no inquiry in the report. (from T06)
- `Enrichment.degraded` lists optional sources that didn't answer (ФНС, ФССП, арбитраж): each needs the "source unavailable" flag and a penalty. (from T07)

## T19 — state machine

- `company_not_found` → the application can't be processed; enrichment not complete (`unavailable_required`) → underwriter queue with reason "source unavailable"; otherwise → stop-factors and scoring. (from T07, `apps/sources/README.md`)

## T25 — ИНН entry and consents

- Collect both 218-ФЗ consents: `bki_company` with the company's ИНН and `bki_owner` with the main owner's ИНН (for ИП: the entrepreneur's ИНН). Without them БКИ isn't queried and the application can't go past the underwriter. (from T07)
- ИНН/ОГРН in `tests/fixtures/` fail checksum validation; generated ones pass. If the UI validates checksums, fix the fixtures through a shared-folder PR. (from T01)

## T27 — "analyzing" screen

- Use `enrich(..., on_event=handler)`: `SourceEvent(source, attempt, status, final, error)`. The mock service's HTTP router isn't mounted in `apps/api/main.py`; the orchestrator calls the service in-process by default. (from T06/T07)

## T30 — staff dossier

- Our own БКИ request doesn't appear in the report. If the dossier should show it, ask track 1 to add it in the БКИ adapter (`apps/sources/`). (from T06)

## T34 — Docker

- `MOCK_SOURCES_*` and `WORLD_*` are in `.env.example`. So the API doesn't rebuild the world on every start, write it to `apps/world/out` when building the image. (from T07)

## Unassigned

_(empty)_
