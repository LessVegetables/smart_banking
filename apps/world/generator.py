"""Synthetic world generator: companies and registry data (SPEC §14, T01), loans and
account turnovers (T02, `loans.py` and `turnovers.py`), credit-bureau histories (T03,
`bki.py`), fraud archetypes and clients with incomplete data (T04, `archetypes.py`),
default outcomes and the train/holdout split (T05, `outcomes.py`), the client card,
ФССП, арбитраж / Федресурс and 115-ФЗ answers (T06, `registries.py`).

Every company has a hidden quality q ~ N(0, 1), a hidden archetype and a hidden
default outcome that follows from both.

Reproducibility: the output depends only on (seed, n, as_of) and on the outcome
parameters in config/world_outcomes.json, which meta.json records. Each company draws
from its own random streams, derived from the seed, its id and a stream name
(`company_rng`). A later task adds its own stream, so it never shifts the values
already generated here. Only the standard library is used.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from apps.api.contracts import (AccountsPayload, AmlPayload, ArbitrationPayload, BkiPayload, ClientCardPayload,
                                EgrulPayload, FnsPayload, Founder, FsspPayload, GirBoReport, LegalForm, Person, TaxReturn)

from .archetypes import (Archetype, account_profile, declared_revenue, draw_data_gap, draw_fraud, shell_accounts,
                         shell_registry)
from .bki import add_late_payments, credit_report
from .industries import BY_KEY, INDUSTRIES, Industry
from .loans import WorldLoan, draw_loans
from .outcomes import OutcomeConfig, Sample, draw_outcome, load_outcome_config
from .registries import aml, arbitration, client_card, fssp
from .turnovers import generate_accounts

GENERATOR_VERSION = 'world-t06-1'
DEFAULT_SEED = 42
DEFAULT_N = 5000
DEFAULT_AS_OF = date(2026, 10, 1)
MAX_N = 100_000  # keeps generated INN and OGRN serials unique

# 209-ФЗ size limits (SPEC §3): (max employees, max annual revenue).
SIZE_LIMITS = {'micro': (15, 120_000_000), 'small': (100, 800_000_000)}


class Company(BaseModel):
    """One synthetic company. Internal to the world, not a cross-track contract."""
    model_config = ConfigDict(extra='forbid')

    company_id: str
    q: float  # hidden quality, never shown to the engine
    archetype: Archetype  # hidden fraud or data-gap archetype (T04), never shown to the engine
    industry: str
    legal_form: LegalForm
    size: Literal['micro', 'small']
    employees: int
    annual_revenue: int  # true revenue over the 12 months before as_of
    revenue_growth: float  # true yearly growth, used for trends in T02
    declared_annual_revenue: int  # what the client would declare in an application (T04)
    egrul: EgrulPayload
    fns: FnsPayload
    loans: list[WorldLoan]  # company loans with their late payments; the account and the bureau follow them
    accounts: AccountsPayload
    bki: BkiPayload  # credit-bureau reports on the company and its owner (T03)
    # The other sources (T06)
    client_card: ClientCardPayload
    fssp: FsspPayload
    arbitration: ArbitrationPayload
    aml: AmlPayload
    # Hidden outcome (T05), never shown to the engine
    pd_12m: float  # true probability of default within 12 months after as_of
    default_12m: bool  # 90+ days overdue within 12 months after as_of
    sample: Sample  # train or holdout, drawn independently of the outcome

    @property
    def inn(self) -> str:
        return self.egrul.inn


class WorldMeta(BaseModel):
    model_config = ConfigDict(extra='forbid')

    generator_version: str
    seed: int
    n: int
    as_of: date
    outcomes: OutcomeConfig  # the config the default outcomes were drawn with (T05)


class World(BaseModel):
    model_config = ConfigDict(extra='forbid')

    meta: WorldMeta
    companies: list[Company]


def company_rng(seed: int, company_id: str, stream: str) -> random.Random:
    """Independent, reproducible random stream for one company and one purpose."""
    return random.Random(f'{seed}:{company_id}:{stream}')


def generate_world(seed: int = DEFAULT_SEED, n: int = DEFAULT_N, as_of: date = DEFAULT_AS_OF,
                   outcomes: OutcomeConfig | None = None) -> World:
    """`outcomes` defaults to config/world_outcomes.json."""
    if not 1 <= n <= MAX_N:
        raise ValueError(f'n must be between 1 and {MAX_N}')
    outcomes = outcomes or load_outcome_config()
    companies = [_company(seed, idx, as_of, outcomes) for idx in range(n)]
    return World(meta=WorldMeta(generator_version=GENERATOR_VERSION, seed=seed, n=n, as_of=as_of,
                                outcomes=outcomes),
                 companies=companies)


# --- Identifiers --------------------------------------------------------------------
# SIMPLIFICATION: INN and OGRN follow the real layout and check-digit rules, but
# region and tax office codes come from a short list and serials are sequential.

def _check(digits: str, weights: tuple[int, ...]) -> str:
    return str(sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10)


_INN10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def company_inn(prefix: str, serial: int) -> str:
    """10 digits: 4-digit tax office code, 5-digit serial, check digit."""
    body = f'{prefix}{serial:05d}'
    return body + _check(body, _INN10)


def person_inn(prefix: str, serial: int) -> str:
    """12 digits (a person or an ИП): 4-digit tax office code, 6-digit serial, two check digits."""
    body = f'{prefix}{serial:06d}'
    body += _check(body, _INN11)
    return body + _check(body, _INN12)


def valid_inn(inn: str) -> bool:
    if len(inn) == 10:
        return _check(inn, _INN10) == inn[9]
    if len(inn) == 12:
        return _check(inn, _INN11) == inn[10] and _check(inn, _INN12) == inn[11]
    return False


def ogrn(registered_at: date, region_code: str, serial: int) -> str:
    """13 digits: sign 1, year, region, 7-digit record number, check digit."""
    body = f'1{registered_at.year % 100:02d}{region_code}{serial:07d}'
    return body + str(int(body) % 11 % 10)


def ogrnip(registered_at: date, region_code: str, serial: int) -> str:
    """15 digits: sign 3, year, region, 9-digit record number, check digit."""
    body = f'3{registered_at.year % 100:02d}{region_code}{serial:09d}'
    return body + str(int(body) % 13 % 10)


def valid_ogrn(value: str) -> bool:
    if len(value) == 13:
        return int(value[:12]) % 11 % 10 == int(value[12])
    if len(value) == 15:
        return int(value[:14]) % 13 % 10 == int(value[14])
    return False


# --- Synthetic people and places -------------------------------------------------

# (region name, region code, city, tax office codes, weight)
REGIONS = (
    ('Москва', '77', 'Москва', ('03', '15', '27'), 22),
    ('Санкт-Петербург', '78', 'Санкт-Петербург', ('02', '10', '25'), 10),
    ('Московская область', '50', 'Подольск', ('36', '74'), 9),
    ('Новосибирская область', '54', 'Новосибирск', ('06', '07'), 7),
    ('Свердловская область', '66', 'Екатеринбург', ('70', '85'), 7),
    ('Республика Татарстан', '16', 'Казань', ('55', '59'), 7),
    ('Краснодарский край', '23', 'Краснодар', ('09', '12'), 9),
    ('Нижегородская область', '52', 'Нижний Новгород', ('57', '60'), 6),
    ('Ростовская область', '61', 'Ростов-на-Дону', ('63', '64'), 6),
    ('Самарская область', '63', 'Самара', ('15', '17'), 5),
    ('Республика Башкортостан', '02', 'Уфа', ('76', '78'), 5),
    ('Пермский край', '59', 'Пермь', ('02', '04'), 4),
    ('Воронежская область', '36', 'Воронеж', ('62', '64'), 3),
)

STREETS = ('Центральная', 'Садовая', 'Заводская', 'Лесная', 'Молодёжная', 'Строителей',
           'Набережная', 'Полевая', 'Школьная', 'Новая', 'Луговая', 'Советская', 'Мира')

MALE_FIRST = ('Алексей', 'Андрей', 'Дмитрий', 'Сергей', 'Иван', 'Михаил', 'Николай', 'Павел',
              'Олег', 'Артём', 'Виктор', 'Егор', 'Роман', 'Игорь', 'Константин')
FEMALE_FIRST = ('Мария', 'Анна', 'Елена', 'Ольга', 'Татьяна', 'Наталья', 'Ирина', 'Светлана',
                'Екатерина', 'Юлия', 'Дарья', 'Ксения', 'Марина', 'Алина', 'Вера')
PATRONYMIC_STEMS = ('Алексее', 'Андрее', 'Сергее', 'Ивано', 'Михайло', 'Николае', 'Павло',
                    'Петро', 'Викторо', 'Олего', 'Романо', 'Игоре', 'Дмитрие')
SURNAME_BASE = ('Иванов', 'Петров', 'Смирнов', 'Кузнецов', 'Попов', 'Соколов', 'Волков',
                'Морозов', 'Новиков', 'Фёдоров', 'Егоров', 'Козлов', 'Орлов', 'Лебедев',
                'Комаров', 'Зайцев', 'Павлов', 'Семёнов', 'Голубев', 'Виноградов')


def _full_name(rng: random.Random) -> str:
    female = rng.random() < 0.4
    surname = rng.choice(SURNAME_BASE) + ('а' if female else '')
    first = rng.choice(FEMALE_FIRST if female else MALE_FIRST)
    patronymic = rng.choice(PATRONYMIC_STEMS) + ('вна' if female else 'вич')
    return f'{surname} {first} {patronymic}'


def _weighted(rng: random.Random, items, weights):
    return rng.choices(items, weights=weights, k=1)[0]


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _round_to(value: float, step: int) -> int:
    return int(round(value / step)) * step


# --- Company --------------------------------------------------------------------

def _company(seed: int, idx: int, as_of: date, outcomes: OutcomeConfig) -> Company:
    company_id = f'C{idx:05d}'
    rng = company_rng(seed, company_id, 'registry')
    q = rng.gauss(0, 1)

    industry: Industry = _weighted(rng, INDUSTRIES, [i.weight for i in INDUSTRIES])
    legal_form = LegalForm.IP if rng.random() < industry.ip_share else LegalForm.OOO
    region, region_code, city, offices, _ = _weighted(rng, REGIONS, [r[4] for r in REGIONS])
    prefix = region_code + rng.choice(offices)

    # Age: median about 3.5 years, better companies tend to be older; about 1 in 7 is under a year.
    age_days = int(round(365 * math.exp(rng.gauss(math.log(3.5) + 0.15 * q, 1.1))))
    age_days = min(max(age_days, 30), 25 * 365)
    registered_at = as_of - timedelta(days=age_days)

    small_p = (0.15 if legal_form == LegalForm.OOO else 0.03) * math.exp(0.3 * q)
    size = 'small' if age_days > 2 * 365 and rng.random() < small_p else 'micro'
    max_employees, max_revenue = SIZE_LIMITS[size]

    # SIMPLIFICATION: revenue is lognormal around the industry median, shifted by q;
    # young companies are smaller. A real portfolio would be calibrated on bank data.
    median = industry.revenue_median * (6 if size == 'small' else 1)
    if legal_form == LegalForm.IP:
        median *= 0.5
    ramp = min(1.0, 0.35 + age_days / 730)
    revenue = median * ramp * math.exp(0.25 * q + rng.gauss(0, 0.6))
    revenue = min(max(revenue, 600_000), max_revenue)
    annual_revenue = _round_to(revenue, 1000)

    min_employees = 16 if size == 'small' else 0
    per_employee = industry.revenue_per_employee * (1.5 if size == 'small' else 1)  # scale economies
    employees = round(annual_revenue / per_employee * math.exp(rng.gauss(0, 0.3)))
    if legal_form == LegalForm.IP and size == 'micro':
        employees = 0 if rng.random() < 0.55 else min(employees, 10)
    employees = min(max(employees, min_employees), max_employees)
    if legal_form == LegalForm.OOO and size == 'micro':
        employees = max(employees, 1)

    revenue_growth = round(min(max(rng.gauss(0.05 + 0.08 * q, 0.12), -0.5), 0.8), 4)

    archetype_rng = company_rng(seed, company_id, 'archetype')
    archetype = draw_fraud(archetype_rng, q=q, legal_form=legal_form, size=size, age_days=age_days)

    egrul = _egrul(rng, idx, industry, legal_form, region, region_code, city, prefix, registered_at, as_of, q)
    fns = _fns(rng, legal_form, industry, q, annual_revenue, revenue_growth, employees, registered_at, as_of)
    loans = []  # a shell borrows nothing from us
    if archetype == 'shell':
        egrul, fns = shell_registry(archetype_rng, egrul, fns)
        employees = fns.employees
    else:
        loans = draw_loans(company_rng(seed, company_id, 'loans'), company_id, q, industry, legal_form,
                           annual_revenue, registered_at, as_of)
    bki_rng = company_rng(seed, company_id, 'bki')
    loans = add_late_payments(bki_rng, loans, q, as_of)  # before the account, which follows them
    bki = credit_report(bki_rng, company_id=company_id, q=q, egrul=egrul, annual_revenue=annual_revenue,
                        loans=loans, as_of=as_of)
    if archetype == 'regular':
        archetype = draw_data_gap(archetype_rng, q=q, age_days=age_days, loans=loans, bki=bki)

    turnovers_rng = company_rng(seed, company_id, 'turnovers')
    if archetype == 'shell':
        accounts = shell_accounts(turnovers_rng, annual_revenue=annual_revenue, registered_at=registered_at,
                                  as_of=as_of)
    else:
        accounts = generate_accounts(turnovers_rng, q=q, industry=industry, legal_form=legal_form,
                                     employees=employees, annual_revenue=annual_revenue,
                                     revenue_growth=revenue_growth, registered_at=registered_at, fns=fns,
                                     loans=loans, as_of=as_of, profile=account_profile(archetype_rng, archetype, as_of))
    card = client_card(company_rng(seed, company_id, 'client_card'), archetype=archetype, legal_form=legal_form,
                       accounts=accounts, bki=bki, loans=loans, as_of=as_of)
    proceedings = fssp(company_rng(seed, company_id, 'fssp'), q=q, egrul=egrul, fns=fns, bki=bki,
                       annual_revenue=annual_revenue, as_of=as_of)
    lawsuits = arbitration(company_rng(seed, company_id, 'arbitration'), q=q, industry=industry, egrul=egrul, bki=bki,
                           annual_revenue=annual_revenue, as_of=as_of)
    aml_check = aml(company_rng(seed, company_id, 'aml'), q=q, archetype=archetype, egrul=egrul)
    q = round(q, 6)  # the stored q, so that pd_12m can be recomputed from the company alone
    pd_12m, default_12m, sample = draw_outcome(company_rng(seed, company_id, 'outcome'), outcomes, q=q,
                                               industry=industry, archetype=archetype)
    return Company(company_id=company_id, q=q, archetype=archetype, industry=industry.key,
                   legal_form=legal_form, size=size, employees=employees, annual_revenue=annual_revenue,
                   revenue_growth=revenue_growth,
                   declared_annual_revenue=declared_revenue(archetype_rng, archetype, annual_revenue),
                   egrul=egrul, fns=fns, loans=loans, accounts=accounts, bki=bki,
                   client_card=card, fssp=proceedings, arbitration=lawsuits, aml=aml_check,
                   pd_12m=pd_12m, default_12m=default_12m, sample=sample)


def _egrul(rng, idx, industry, legal_form, region, region_code, city, prefix, registered_at, as_of, q) -> EgrulPayload:
    extra = sorted(rng.sample(industry.okved_extra, k=rng.randint(0, len(industry.okved_extra))))
    address = f'г. {city}, ул. {rng.choice(STREETS)}, д. {rng.randint(1, 120)}'
    # Applicants are clients of the bank, so almost everyone is active.
    status = 'liquidating' if q < -1.5 and rng.random() < 0.03 else 'active'

    if legal_form == LegalForm.IP:
        inn = person_inn(prefix, idx)
        name = _full_name(rng)
        owner = Person(full_name=name, inn=inn, since=registered_at)
        return EgrulPayload(inn=inn, ogrn=ogrnip(registered_at, region_code, idx), legal_form=legal_form,
                            name=f'ИП {name}', status=status, registered_at=registered_at,
                            okved_main=industry.okved_main, okved_extra=extra, region=region,
                            address=address, director=owner, founders=[])

    # Persons behind an ООО get serials above those of ИП, so INNs never collide.
    def person(k: int, since: date) -> Person:
        return Person(full_name=_full_name(rng), inn=person_inn(prefix, 300_000 + idx * 6 + k), since=since)

    shares = _shares(rng)
    founders = [Founder(**person(k, registered_at).model_dump(), share=s) for k, s in enumerate(shares)]
    if rng.random() < 0.7:
        director = Person(**founders[0].model_dump(exclude={'share'}))
    else:
        director = person(5, registered_at)
    # Director changes are more frequent at weaker companies; the date stays visible to the engine.
    if (as_of - registered_at).days > 60 and rng.random() < _sigmoid(-1.8 - 0.5 * q):
        changed = registered_at + timedelta(days=rng.randint(30, (as_of - registered_at).days))
        director = person(4, changed)

    name = f'ООО «{rng.choice(industry.name_words)}{_name_suffix(rng)}»'
    return EgrulPayload(inn=company_inn(prefix, idx), ogrn=ogrn(registered_at, region_code, idx),
                        legal_form=legal_form, name=name, status=status, registered_at=registered_at,
                        okved_main=industry.okved_main, okved_extra=extra, region=region,
                        address=address, director=director, founders=founders)


def _name_suffix(rng: random.Random) -> str:
    return rng.choice(('', '', '', '+', ' Плюс', ' Групп', '-Сервис', ' Про'))


def _shares(rng: random.Random) -> list[float]:
    count = _weighted(rng, (1, 2, 3), (0.6, 0.3, 0.1))
    if count == 1:
        return [1.0]
    cuts = sorted(rng.randint(10, 90) for _ in range(count - 1))
    parts = [b - a for a, b in zip([0, *cuts], [*cuts, 100])]
    parts = [max(p, 5) for p in parts]
    parts[0] += 100 - sum(parts)
    return [p / 100 for p in sorted(parts, reverse=True)]


def _fns(rng, legal_form, industry, q, annual_revenue, growth, employees, registered_at, as_of) -> FnsPayload:
    # Tax debt is likelier at weaker companies (about 1 in 12 overall).
    tax_debt = 0
    if rng.random() < _sigmoid(-2.6 - 1.0 * q):
        tax_debt = _round_to(annual_revenue * rng.uniform(0.001, 0.02), 100)
    suspended = tax_debt > 0 and rng.random() < 0.15
    disqualified = legal_form == LegalForm.OOO and q < -1.0 and rng.random() < 0.02

    # SIMPLIFICATION: the last filed year is always the previous calendar year; a
    # company registered in the current year has no filing yet.
    year = as_of.year - 1
    year_start, year_end = date(year, 1, 1), date(year, 12, 31)
    financials = tax_return = None
    if registered_at <= year_end:
        active_share = min(1.0, ((year_end - max(registered_at, year_start)).days + 1) / 365)
        # Mid-year of the filed year is about 1.25 years before as_of: deflate by growth.
        lag_years = (as_of - date(year, 7, 1)).days / 365
        filed = annual_revenue / (1 + growth) ** lag_years * active_share * rng.uniform(0.95, 1.05)
        filed = _round_to(filed, 1000)
        if legal_form == LegalForm.OOO:
            financials = _gir_bo(rng, industry, q, year, filed)
        else:
            regime = _weighted(rng, ('usn_income', 'usn_income_expense', 'patent', 'osno'), (60, 20, 15, 5))
            tax_return = TaxReturn(year=year, regime=regime, income=filed)

    return FnsPayload(tax_debt=tax_debt, accounts_suspended=suspended,
                      mass_address=False,  # only a shell company sits at a mass address (T04)
                      director_disqualified=disqualified, employees=employees,
                      financials=financials, tax_return=tax_return)


def _gir_bo(rng, industry, q, year, revenue) -> GirBoReport:
    cost = revenue * industry.cogs_share * rng.uniform(0.9, 1.1)
    margin = min(max(rng.gauss(0.04 + 0.03 * q, 0.04), -0.2), 0.25)
    assets = revenue * industry.assets_to_revenue * math.exp(rng.gauss(0, 0.25))
    equity_ratio = min(max(rng.gauss(0.35 + 0.1 * q, 0.15), -0.3), 0.9)
    total_assets = _round_to(assets, 1000)
    equity = _round_to(total_assets * equity_ratio, 1000)
    return GirBoReport(year=year, revenue=revenue, cost_of_sales=_round_to(cost, 1000),
                       net_profit=_round_to(revenue * margin, 1000), total_assets=total_assets,
                       equity=equity, liabilities=total_assets - equity)


def industry_of(company: Company) -> Industry:
    return BY_KEY[company.industry]
