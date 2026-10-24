"""Demo personas (T12, SPEC §14): one synthetic company per outcome the demo shows, picked from the world.

Each persona is a target: which companies may play it (legal form, hidden archetype), the application it
submits and what the real conveyor must decide for it. The selection runs every candidate, in world order,
through the same stages as an application (enrichment by the mock sources → data quality → `decide`, and the
statement re-run of T23 where the persona uploads one) and takes the first company whose decision passes the
target's check. So the expected outcomes are the engine's own, never written by hand.

    python -m apps.world.personas            # writes apps/world/demo/personas.json and the statements

The selection reads the hidden archetype to cast the fraud and incomplete-data stories, and nothing else of
the hidden truth (`q`, PD, the default outcome). The archetype stays in the persona file for the demo script
only; the engine and the UI never read it. Retraining the card or changing the world or `config/` moves the
outcomes: regenerate, and `tests/test_world_personas.py` fails until you do. See apps/world/PERSONAS.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from apps.api.contracts import ApplicationRequest, ApplicationStatus as S, Decision, LegalForm, Route
from apps.api.preapproval import preapprove, standing_request
from apps.api.scoring import Scorer, default_scorer
from apps.api.statements import parse_statements
from apps.api.workflow import data_quality_for, decide, policy_version
from apps.sources import Enrichment, MockSourceService, RetryPolicy, Settings, enrich
from apps.sources.enrichment import MSK
from apps.sources.service import parse_scenarios

from .generator import (DEFAULT_AS_OF, DEFAULT_N, DEFAULT_SEED, MAX_N, Company, World, company_inn, generate_world,
                        person_inn)
from .statements import other_bank_statement

DEMO_DIR = Path(__file__).parent / 'demo'
PERSONAS_FILE = DEMO_DIR / 'personas.json'
STATEMENTS_DIR = DEMO_DIR / 'statements'

# The demo's clock: the day the client applies. Offers are dated from it, features from the world's as_of.
DECIDED_AT = datetime(2026, 10, 24, 10, 15, tzinfo=MSK)
# A failing source fails at once: the selection doesn't wait on retries.
RETRY = RetryPolicy(timeout_seconds=0.2, attempts=3, backoff_seconds=0)
# An ИНН the world never has (serials stop at MAX_N - 1 only with the largest world), for "company not found".
UNKNOWN_IDX = MAX_N - 1


# --- What a persona file holds -----------------------------------------------------------

class ExpectedOffer(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    kind: str
    product: str
    amount: int
    term_months: int
    rate: float  # rounded to basis points
    collateral: list[str]


class Expected(BaseModel):
    """The decision as the demo checks it: codes and amounts, without texts and dates."""
    model_config = ConfigDict(extra='forbid', frozen=True)

    status: str
    route: str
    route_reasons: list[str]
    data_quality_tier: str | None
    grade: str | None
    stop_factors: list[str]
    flags: list[str]
    reasons: list[str]
    matches_request: bool | None
    offers: list[ExpectedOffer]
    requested_documents: int


class ExpectedPreApproval(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    approved: bool
    amount: int | None
    rate: float | None
    reasons: list[str]


class Persona(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    persona_id: str
    title: str
    story: str  # what the demo shows with this persona, in Russian
    company_id: str | None
    inn: str
    legal_form: str
    archetype: str | None  # hidden truth, for the demo script only: never shown to the engine or the UI
    request: dict[str, Any]  # `ApplicationRequest` without application_id and submitted_at
    scenarios: dict[str, str]  # MOCK_SOURCES_SCENARIOS for this ИНН
    statement_file: str | None  # in apps/world/demo/statements/, uploaded after the first decision
    expected: Expected
    expected_after_statement: Expected | None
    preapproval: ExpectedPreApproval | None  # the overdraft banner (T22), checked for the auto personas


class PersonaFile(BaseModel):
    model_config = ConfigDict(extra='forbid')

    world: dict[str, Any]  # seed, n, as_of, generator version
    scorecard: str  # the champion's model version the outcomes were computed with
    policy_version: str
    decided_at: datetime
    personas: list[Persona]


# --- Running one application -------------------------------------------------------------

def _round_to(value: float, step: int) -> int:
    return max(step, int(value // step) * step)


def base_request(company: Company, **changes: Any) -> ApplicationRequest:
    """The application a client fills in (T25, T26), with every consent its legal form needs."""
    given = DECIDED_AT.isoformat()
    if company.legal_form == LegalForm.OOO:
        owner = max(company.egrul.founders, key=lambda f: f.share).inn
        consents = [('bki_company', company.inn), ('bki_owner', owner), ('personal_data', owner)]
    else:
        consents = [('bki_owner', company.inn), ('personal_data', company.inn)]
    data = dict(application_id='demo', inn=company.inn, legal_form=company.legal_form.value, purpose='inventory',
                amount=_round_to(company.annual_revenue * 0.05, 50_000), term_months=12, needed_by='month',
                preferred_product=None, declared_annual_revenue=company.declared_annual_revenue, equipment=None,
                consents=[{'kind': k, 'subject_inn': inn, 'given_at': given} for k, inn in consents],
                submitted_at=given)
    return ApplicationRequest.model_validate({**data, **changes})


@dataclass(frozen=True)
class Run:
    request: ApplicationRequest
    decision: Decision
    after_statement: Decision | None
    statement: dict[str, Any] | None


class Conveyor:
    """The application stages on one world, with no database and no worker."""

    def __init__(self, world: World, scorer: Scorer | None = None):
        self.world = world
        self.scorer = scorer or default_scorer()

    def _service(self, scenarios: dict[str, dict[str, str]]) -> MockSourceService:
        settings = Settings(latency_scale=0, failures=False, hang_seconds=0.01, scenarios=parse_scenarios(scenarios))
        return MockSourceService(self.world, settings)

    def enrich(self, request: ApplicationRequest, scenarios: dict[str, str] | None = None) -> Enrichment:
        service = self._service({request.inn: scenarios} if scenarios else {})
        return asyncio.run(enrich(request, service, policy=RETRY, now=lambda: DECIDED_AT))

    def run(self, request: ApplicationRequest, scenarios: dict[str, str] | None = None,
            statement: dict[str, Any] | None = None) -> Run:
        as_of = self.world.meta.as_of
        e = self.enrich(request, scenarios)
        dq, _ = data_quality_for(request, e, None, as_of)
        decision = decide(request, e, dq, as_of=as_of, decided_at=DECIDED_AT, scorer=self.scorer)
        after = None
        if statement is not None:
            # T23: the re-run after the upload reuses the source answers.
            files = [('statement.json', json.dumps(statement, ensure_ascii=False).encode())]
            uploaded = parse_statements(files, request, DECIDED_AT)
            dq, _ = data_quality_for(request, e, uploaded, as_of)
            after = decide(request, e, dq, as_of=as_of, decided_at=DECIDED_AT, scorer=self.scorer,
                           statements=uploaded)
        return Run(request, decision, after, statement)

    def preapproval(self, company: Company) -> ExpectedPreApproval:
        request = standing_request(company.inn, company.egrul, DECIDED_AT)
        result = preapprove(request, self.enrich(request), as_of=self.world.meta.as_of, computed_at=DECIDED_AT,
                            scorer=self.scorer)
        return ExpectedPreApproval(approved=result.approved, amount=result.amount,
                                   rate=None if result.rate is None else round(result.rate, 4),
                                   reasons=[r.code for r in result.reasons])


def expected(d: Decision) -> Expected:
    return Expected(
        status=d.status.value, route=d.route.value, route_reasons=[r.code for r in d.route_reasons],
        data_quality_tier=d.data_quality.tier.value if d.data_quality else None,
        grade=d.score.grade.value if d.score else None, stop_factors=[r.code for r in d.stop_factors],
        flags=[r.code for r in d.flags], reasons=[r.code for r in d.reasons], matches_request=d.matches_request,
        offers=[ExpectedOffer(kind=o.kind, product=o.product.value, amount=o.amount, term_months=o.term_months,
                              rate=round(o.rate, 4), collateral=[c.value for c in o.collateral]) for o in d.offers],
        requested_documents=len(d.requested_documents))


# --- The targets -------------------------------------------------------------------------

def _tier(d: Decision | None) -> str | None:
    return d.data_quality.tier.value if d and d.data_quality else None


def _codes(reasons) -> set[str]:
    return {r.code for r in reasons}


def _equipment(company: Company, price_share: float, **changes: Any) -> ApplicationRequest:
    price = _round_to(company.annual_revenue * price_share, 100_000)
    down = _round_to(price * 0.3, 10_000)  # the pledge covers up to 70% of the price (T16)
    supplier = company_inn(company.egrul.inn[:4], UNKNOWN_IDX - 1)
    return base_request(company, purpose='equipment', preferred_product='investment', term_months=36,
                        amount=price - down, equipment={'description': 'Производственное оборудование',
                                                        'price': price, 'down_payment': down,
                                                        'supplier_inn': supplier}, **changes)


@dataclass(frozen=True)
class Target:
    persona_id: str
    title: str
    story: str
    legal_form: LegalForm | None
    archetypes: tuple[str, ...]
    request: Callable[[Company], ApplicationRequest | list[ApplicationRequest]]  # a list: tried in order
    check: Callable[[Run], bool]
    scenarios: dict[str, str] = field(default_factory=dict)
    statement: bool = False
    preapproval: bool = False  # the persona must also get the overdraft pre-approval


DEMO_MIN_OFFER = 300_000  # an approval persona's best offer: the product minimum looks like a rounding error


def _best(d: Decision | None) -> int:
    return max((o.amount for o in d.offers), default=0) if d else 0


def _auto(run: Run, *, matches: bool = True) -> bool:
    d = run.decision
    return (d.status == S.OFFERED and d.route == Route.AUTO and d.matches_request is matches
            and _tier(d) == 'FULL' and not d.flags and _best(d) >= DEMO_MIN_OFFER)


def _review(d: Decision, route: Route, codes: set[str]) -> bool:
    return d.status == S.IN_REVIEW and d.route == route and codes <= _codes(d.route_reasons)


def _improves(run: Run) -> bool:
    """The statement lifts the tier or the best offer: the "upload a statement" story pays off."""
    before, after = run.decision, run.after_statement
    if after is None or run.statement is None or after.status != S.IN_REVIEW or not after.offers:
        return False
    if before.flags or after.flags:
        return False  # the story is the missing data alone
    return 'route_statement_upload' in _codes(after.route_reasons) and _best(after) > _best(before)


REGULAR = ('regular',)

TARGETS: tuple[Target, ...] = (
    Target('auto_ooo', 'ООО: автоматическое одобрение',
           'Полные данные по счёту, грейд A–C, флагов нет: предложения сразу, без сотрудника. У этой же компании '
           'есть предодобренный овердрафт (баннер T29).',
           LegalForm.OOO, REGULAR, lambda co: base_request(co), _auto, preapproval=True),
    Target('auto_ip', 'ИП: автоматическое одобрение',
           'Предприниматель с полными данными: оборотный кредит на запрошенных условиях, решение сразу.',
           LegalForm.IP, REGULAR, lambda co: base_request(co), _auto),
    Target('counter_offer', 'Набор предложений вместо запрошенного',
           'Запрошенная сумма больше лимита: клиент видит, что доступно (меньше сумма, дольше срок или другой '
           'продукт), и почему.',
           LegalForm.IP, REGULAR, lambda co: base_request(co, amount=min(3_000_000, _round_to(co.annual_revenue * 0.3, 50_000))),
           lambda run: _auto(run, matches=False)),
    Target('equipment', 'Оборудование в залог',
           'Инвестиционный кредит на оборудование: залог оборудования и поручительство собственника, выдача '
           'поставщику.',
           LegalForm.OOO, REGULAR, lambda co: _equipment(co, 0.1),
           lambda run: _auto(run) and any('equipment_pledge' in [c.value for c in o.collateral]
                                          for o in run.decision.offers)),
    # Grade D and E get no limit without collateral in this world (PERSONAS.md, «Ограничения»): the
    # underwriter's persona buys equipment, so the pledge gives it an offer to approve.
    Target('underwriter', 'Андеррайтер: грейд D–E',
           'Скоринг ниже автоматической границы: заявка уходит андеррайтеру с готовым предложением под залог '
           'оборудования, он одобряет, меняет или отказывает (T20).',
           None, REGULAR, lambda co: [_equipment(co, 0.1), _equipment(co, 0.2)],
           lambda run: _review(run.decision, Route.UNDERWRITER, {'route_grade'}) and bool(run.decision.offers)),
    # No grade D–E company has an offer above 5M ₽, and grades A–C need more than the 15M ₽ product maximum
    # to reach the committee, so the committee persona has no offers to sign (PERSONAS.md, «Ограничения»).
    Target('committee', 'Кредитный комитет',
           'Грейд D–E и сумма больше 5 млн ₽: маршрут комитета. Предложений нет (лимит по денежному потоку '
           'нулевой), поэтому сотрудник может только отказать или запросить данные.',
           LegalForm.OOO, REGULAR, lambda co: [_equipment(co, 0.1), _equipment(co, 0.2)],
           lambda run: _review(run.decision, Route.COMMITTEE, {'route_grade_over_5m'})),
    Target('stop_factor', 'Отказ по стоп-фактору',
           'Просрочка 90+ дней в кредитной истории за 24 месяца: автоматический отказ без скоринга.',
           LegalForm.IP, REGULAR, lambda co: base_request(co),
           lambda run: run.decision.status == S.DECLINED
           and _codes(run.decision.stop_factors) == {'bki_overdue_90_24m'}),
    Target('economic_decline', 'Отказ: бизнес не тянет платёж',
           'Стоп-факторов нет, но денежного потока не хватает даже на минимальную сумму продукта: отказ с '
           'объяснением и датой повторной заявки.',
           LegalForm.OOO, REGULAR, lambda co: base_request(co),
           lambda run: run.decision.status == S.DECLINED and not run.decision.stop_factors
           and _tier(run.decision) == 'FULL' and run.decision.reapply_after is not None),
    Target('shell', 'Мошенничество: «техническая» компания',
           'Транзитные обороты, массовый адрес, почти нет налогов: флаг 115-ФЗ, заявку смотрит андеррайтер.',
           LegalForm.OOO, ('shell',), lambda co: base_request(co),
           lambda run: run.decision.status == S.IN_REVIEW and 'transit_operations' in _codes(run.decision.flags)),
    Target('inflater', 'Мошенничество: завышенная выручка',
           'Заявленная выручка больше подтверждённой ФНС более чем на 30%: флаг, заявку смотрит андеррайтер. '
           'Лимит считается по выручке ФНС, а не по заявленной.',
           LegalForm.IP, ('inflater',), lambda co: base_request(co),
           lambda run: run.decision.status == S.IN_REVIEW
           and _codes(run.decision.flags) == {'declared_revenue_mismatch'} and bool(run.decision.offers)),
    Target('partial_statement', 'PARTIAL: основной счёт в другом банке',
           'Через наш счёт идёт только часть бизнеса: сумма больше 1 млн ₽ уходит андеррайтеру. Он запрашивает '
           'выписку (`request_data`), клиент загружает её, лимит растёт.',
           LegalForm.OOO, ('main_bank_elsewhere',),
           lambda co: base_request(co, amount=min(3_000_000, max(1_500_000, _round_to(co.annual_revenue * 0.1, 50_000)))),
           lambda run: _review(run.decision, Route.UNDERWRITER, {'route_data_partial_over_1m'})
           and _tier(run.decision) == 'PARTIAL' and _improves(run),
           statement=True),
    Target('thin_statement', 'THIN: недавно открытый счёт',
           'Счёт у нас открыт меньше полугода: система сама просит выписку из прежнего банка (`needs_data`). С '
           'выпиской заявка получает предложения и уходит андеррайтеру.',
           None, ('new_account',), lambda co: base_request(co),
           lambda run: run.decision.status == S.NEEDS_DATA and _tier(run.decision) == 'THIN'
           and _improves(run) and _tier(run.after_statement) != 'THIN',
           statement=True),
    Target('source_down', 'Источник недоступен',
           'БКИ недоступен после всех повторов: без обязательного источника решения нет, заявка у андеррайтера. '
           'Без сбоя эта компания получила бы предложения автоматически.',
           LegalForm.OOO, REGULAR, lambda co: base_request(co),
           lambda run: _review(run.decision, Route.UNDERWRITER, {'route_source_unavailable'}),
           # An error, not a timeout: a timeout hangs every attempt (MOCK_SOURCES_HANG_SECONDS) and stalls the demo.
           scenarios={'bki': 'error'}),
)

NOT_FOUND = ('not_found', 'Компания не найдена',
             'ИНН, которого нет в ЕГРЮЛ / ЕГРИП: автоматический отказ сразу после проверки реестра.')


# --- Selection ---------------------------------------------------------------------------

def _persona(target: Target, company: Company, run: Run, conveyor: Conveyor) -> Persona:
    request = run.request.model_dump(mode='json', exclude={'application_id', 'submitted_at'})
    return Persona(
        persona_id=target.persona_id, title=target.title, story=target.story, company_id=company.company_id,
        inn=company.inn, legal_form=company.legal_form.value, archetype=company.archetype, request=request,
        scenarios=dict(target.scenarios), statement_file=f'{target.persona_id}.json' if target.statement else None,
        expected=expected(run.decision),
        expected_after_statement=expected(run.after_statement) if run.after_statement else None,
        preapproval=conveyor.preapproval(company) if target.preapproval else None)


def _candidate(target: Target, company: Company, conveyor: Conveyor) -> Persona | None:
    if target.legal_form is not None and company.legal_form != target.legal_form:
        return None
    if company.archetype not in target.archetypes:
        return None
    statement = None
    if target.statement:
        statement = other_bank_statement(conveyor.world.meta.seed, company, conveyor.world.meta.as_of)
        if statement is None:
            return None
    requests = target.request(company)
    for request in requests if isinstance(requests, list) else [requests]:
        run = conveyor.run(request, target.scenarios, statement)
        if target.check(run):
            break
    else:
        return None
    if target.preapproval and not conveyor.preapproval(company).approved:
        return None
    if target.persona_id == 'source_down' and not _auto(conveyor.run(run.request)):
        return None  # without the failure the same application is decided automatically
    return _persona(target, company, run, conveyor)


def _not_found(world: World, conveyor: Conveyor) -> Persona:
    prefix = world.companies[0].inn[:4]
    inn = company_inn(prefix, UNKNOWN_IDX)
    owner = person_inn(prefix, 999_999)  # nobody in the world either
    given = DECIDED_AT.isoformat()
    request = ApplicationRequest.model_validate(dict(
        application_id='demo', inn=inn, legal_form='ooo', purpose='inventory', amount=500_000, term_months=12,
        needed_by='month', preferred_product=None, declared_annual_revenue=10_000_000, equipment=None,
        consents=[{'kind': k, 'subject_inn': s, 'given_at': given}
                  for k, s in (('bki_company', inn), ('bki_owner', owner), ('personal_data', owner))],
        submitted_at=given))
    run = conveyor.run(request)
    persona_id, title, story = NOT_FOUND
    return Persona(persona_id=persona_id, title=title, story=story, company_id=None, inn=inn, legal_form='ooo',
                   archetype=None, request=request.model_dump(mode='json', exclude={'application_id', 'submitted_at'}),
                   scenarios={}, statement_file=None, expected=expected(run.decision), expected_after_statement=None,
                   preapproval=None)


class PersonaNotFound(LookupError):
    pass


def select_personas(world: World, scorer: Scorer | None = None) -> PersonaFile:
    """One persona per target, the first company in world order that passes; each company plays one."""
    conveyor = Conveyor(world, scorer)
    used: set[str] = set()
    personas = []
    for target in TARGETS:
        persona = next((p for co in world.companies if co.company_id not in used
                        if (p := _candidate(target, co, conveyor)) is not None), None)
        if persona is None:
            raise PersonaNotFound(f'no company in the world plays {target.persona_id!r}')
        used.add(persona.company_id)
        personas.append(persona)
    personas.append(_not_found(world, conveyor))
    return PersonaFile(
        world={'seed': world.meta.seed, 'n': world.meta.n, 'as_of': world.meta.as_of.isoformat(),
               'generator_version': world.meta.generator_version},
        scorecard=conveyor.scorer.champion.version, policy_version=policy_version(None), decided_at=DECIDED_AT,
        personas=personas)


def statements_of(world: World, personas: PersonaFile) -> dict[str, dict[str, Any]]:
    by_id = {co.company_id: co for co in world.companies}
    return {p.statement_file: other_bank_statement(world.meta.seed, by_id[p.company_id], world.meta.as_of)
            for p in personas.personas if p.statement_file}


def write(world: World, personas: PersonaFile, out: Path = DEMO_DIR) -> None:
    (out / 'statements').mkdir(parents=True, exist_ok=True)
    (out / 'personas.json').write_text(personas.model_dump_json(indent=2) + '\n', encoding='utf-8')
    for name, statement in statements_of(world, personas).items():
        (out / 'statements' / name).write_text(json.dumps(statement, ensure_ascii=False, indent=2) + '\n',
                                              encoding='utf-8')


def read(path: Path = PERSONAS_FILE) -> PersonaFile:
    return PersonaFile.model_validate_json(path.read_text(encoding='utf-8'))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Pick the demo personas from the default world')
    parser.add_argument('--out', type=Path, default=DEMO_DIR)
    args = parser.parse_args(argv)
    world = generate_world(DEFAULT_SEED, DEFAULT_N, DEFAULT_AS_OF)
    personas = select_personas(world)
    write(world, personas, args.out)
    for p in personas.personas:
        e = p.expected
        print(f'{p.persona_id:18} {p.company_id or "-":7} {p.legal_form:3} {e.status:10} {e.route:11} '
              f'{e.grade or "-"} {e.data_quality_tier or "-":7} {[o.amount for o in e.offers]}')


if __name__ == '__main__':
    main()
