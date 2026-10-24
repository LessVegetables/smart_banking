"""The rest of the sources (T06): the client card, ФССП, арбитраж / Федресурс and 115-ФЗ.

T01-T04 made the registry, tax, bureau and account data. These four sources are drawn
from them, each from the company's own stream (`client_card`, `fssp`, `arbitration`,
`aml`), so adding them changes nothing generated before:

- the client card follows the account and the loans with us;
- enforcement proceedings (ФССП) follow tax debt, long bureau overdues and q;
- lawsuits and bankruptcy notices follow q, the industry and the overdues;
- the 115-ФЗ answer follows the hidden archetype: the AML system catches most shells.

Each stop factor and flag of SPEC §7 that reads these sources has cases in the world.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from apps.api.contracts import (AccountsPayload, AmlListMatch, AmlPayload, ArbitrationCase, ArbitrationPayload,
                                BankProduct, BankruptcyNotice, BkiPayload, ClientCardPayload, EgrulPayload, FnsPayload,
                                FsspPayload, FsspProceeding, LegalForm)

from .industries import Industry
from .loans import WorldLoan
from .turnovers import RETAIL_CUSTOMERS

# Arbitration court codes (А40 is Moscow) by region, for case numbers.
COURTS = {
    'Москва': '40', 'Санкт-Петербург': '56', 'Московская область': '41', 'Новосибирская область': '45',
    'Свердловская область': '60', 'Республика Татарстан': '65', 'Краснодарский край': '32',
    'Нижегородская область': '43', 'Ростовская область': '53', 'Самарская область': '55',
    'Республика Башкортостан': '07', 'Пермский край': '50', 'Воронежская область': '14',
}
CASE_HISTORY_DAYS = 3 * 365  # lawsuits filed in the last three years
CASE_DECIDED_DAYS = 180  # a case filed earlier than this is usually decided
FSSP_HISTORY_DAYS = 2 * 365  # proceedings still open, opened in the last two years
COURT_OVERDUE_DAYS = 90  # a lender goes to court and then to the bailiffs after this
AML_LISTS = ('Перечень Росфинмониторинга (экстремизм и терроризм)',
             'Перечень лиц, причастных к распространению оружия массового уничтожения',
             'Решение МВК о замораживании активов')


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _round_to(value: float, step: int) -> int:
    return int(round(value / step)) * step


def _between(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max(0, (end - start).days)))


def _poisson(rng: random.Random, mean: float) -> int:
    k, p, limit = 0, rng.random(), math.exp(-mean)
    while p > limit:
        k += 1
        p *= rng.random()
    return k


def _lognormal(rng: random.Random, median: float, sigma: float) -> float:
    return median * math.exp(rng.gauss(0, sigma))


def _long_overdue(bki: BkiPayload) -> list[tuple[str, int]]:
    """(subject, debt) of open bureau products overdue for COURT_OVERDUE_DAYS or more."""
    return [(report.subject, loan.balance + loan.current_overdue_amount)
            for report in bki.reports for loan in report.loans
            if loan.closed_at is None and loan.current_overdue_days >= COURT_OVERDUE_DAYS]


# --- Client card -----------------------------------------------------------------------

STOP_LIST_P = 0.002
SHELL_STOP_LIST_P = 0.15  # our own compliance has already caught some shells
BUSINESS_CARD_P = 0.5


def client_card(rng: random.Random, *, archetype: str, legal_form: LegalForm, accounts: AccountsPayload,
                bki: BkiPayload, loans: list[WorldLoan], as_of: date) -> ClientCardPayload:
    """Products with us: the account, open loans from `loans`, a business card, a deposit."""
    opened_at = accounts.account_opened_at
    balance = max(accounts.months[-1].average_balance, 0) if accounts.months else None
    products = [BankProduct(kind='account', opened_at=opened_at, amount=None, balance=balance, monthly_payment=None)]

    # The bureau already knows each loan's balance on as_of; leasing comes from a leasing
    # company, so only term loans are products of the bank.
    ours = {loan.loan_id for loan in loans if loan.kind == 'loan'}
    for loan in bki.reports[0].loans:
        if loan.loan_id in ours and loan.closed_at is None:
            products.append(BankProduct(kind='loan', opened_at=loan.opened_at, amount=loan.amount,
                                        balance=loan.balance, monthly_payment=loan.monthly_payment))

    if archetype != 'shell' and rng.random() < BUSINESS_CARD_P:
        card_at = min(opened_at + timedelta(days=rng.randint(0, 120)), as_of - timedelta(days=1))
        products.append(BankProduct(kind='card', opened_at=card_at, amount=None, balance=None, monthly_payment=None))

    # SIMPLIFICATION: an ООО moves its surplus to a deposit with us (T02); the deposit holds
    # exactly what went out and never came back, without interest.
    if legal_form == LegalForm.OOO and archetype in ('regular', 'inflater', 'new_account'):
        out = [(m.month, m.outflows.own_transfers) for m in accounts.months if m.outflows.own_transfers]
        held = sum(x for _, x in out) - sum(m.inflows.own_transfers for m in accounts.months)
        if out and held >= 10_000:
            year, month = map(int, out[0][0].split('-'))
            products.append(BankProduct(kind='deposit', opened_at=date(year, month, 1) + timedelta(days=rng.randint(0, 27)),
                                        amount=None, balance=_round_to(held, 1000), monthly_payment=None))

    stop_p = SHELL_STOP_LIST_P if archetype == 'shell' else STOP_LIST_P
    # SIMPLIFICATION: no company groups in the world: every company has its own people.
    return ClientCardPayload(client_since=opened_at, products=sorted(products, key=lambda p: (p.opened_at, p.kind)),
                             internal_stop_list=rng.random() < stop_p, related_companies=[])


# --- ФССП --------------------------------------------------------------------------------

TAX_PROCEEDING_P = 0.35  # unpaid tax debt goes to the bailiffs
SUSPENDED_TAX_PROCEEDING_P = 0.8
CREDIT_PROCEEDING_P = 0.5


def fssp(rng: random.Random, *, q: float, egrul: EgrulPayload, fns: FnsPayload, bki: BkiPayload,
         annual_revenue: int, as_of: date) -> FsspPayload:
    """Open enforcement proceedings against the company and its owner (for an ИП, the owner)."""
    company = 'owner' if egrul.legal_form == LegalForm.IP else 'company'
    start = max(egrul.registered_at + timedelta(days=60), as_of - timedelta(days=FSSP_HISTORY_DAYS))
    found: list[tuple[date, str, str, int, float]] = []  # opened_at, subject, kind, amount, paid share

    def add(subject: str, kind: str, amount: float, earliest: date = start) -> None:
        if earliest < as_of:
            paid = rng.uniform(0, 0.6) if rng.random() < 0.4 else 0.0
            found.append((_between(rng, earliest, as_of - timedelta(days=1)), subject, kind,
                          max(_round_to(amount, 100), 500), paid))

    if fns.tax_debt and rng.random() < (SUSPENDED_TAX_PROCEEDING_P if fns.accounts_suspended else TAX_PROCEEDING_P):
        add(company, 'Налоги и сборы', fns.tax_debt * rng.uniform(1.0, 1.15),  # with penalties
            max(start, as_of - timedelta(days=180)))
    for subject, debt in _long_overdue(bki):
        if rng.random() < CREDIT_PROCEEDING_P:
            add(subject, 'Задолженность по кредитным платежам', debt, max(start, as_of - timedelta(days=120)))
    # Unpaid suppliers, utilities and fines: rarer at stronger companies.
    for _ in range(_poisson(rng, 0.025 * math.exp(-0.9 * q))):
        add(company, 'Задолженность по договору', _lognormal(rng, annual_revenue * 0.01, 0.9))
    for _ in range(_poisson(rng, 0.012 * math.exp(-0.6 * q))):
        add(company, 'Коммунальные платежи', _lognormal(rng, 40_000, 0.8))
    for _ in range(_poisson(rng, 0.01 * math.exp(-0.5 * q))):
        add('owner', 'Штраф', _lognormal(rng, 3_000, 0.7))

    region = egrul.inn[:2]
    proceedings = []
    for opened_at, subject, kind, amount, paid in sorted(found):
        number = f'{rng.randint(1000, 299_999)}/{opened_at.year % 100:02d}/{region}{rng.randint(1, 40):03d}-ИП'
        proceedings.append(FsspProceeding(number=number, subject=subject, opened_at=opened_at, kind=kind,
                                          amount=amount, remaining=max(_round_to(amount * (1 - paid), 100), 100)))
    return FsspPayload(proceedings=proceedings)


# --- Арбитраж and Федресурс ------------------------------------------------------------------

BANKRUPTCY_INTENT_P = 0.4  # a lender in court for long overdues announces its intent on Федресурс


def arbitration(rng: random.Random, *, q: float, industry: Industry, egrul: EgrulPayload, bki: BkiPayload,
                annual_revenue: int, as_of: date) -> ArbitrationPayload:
    """Lawsuits filed in the last three years and bankruptcy notices on Федресурс."""
    start = max(egrul.registered_at + timedelta(days=30), as_of - timedelta(days=CASE_HISTORY_DAYS))
    last = as_of - timedelta(days=1)
    years = max(0.0, (as_of - start).days / 365)
    b2b = industry.customers < RETAIL_CUSTOMERS
    raw: list[tuple[date, str, int]] = []
    if years:
        # Suing customers that don't pay is routine in B2B.
        for _ in range(_poisson(rng, years * (0.2 if b2b else 0.03))):
            raw.append((_between(rng, start, last), 'plaintiff', _lognormal(rng, annual_revenue * 0.01, 0.8)))
        # Being sued is likelier at weaker companies, and the claims are bigger: some pass
        # 10% of revenue, the SPEC §7 flag.
        for _ in range(_poisson(rng, years * 0.02 * math.exp(-0.9 * q))):
            raw.append((_between(rng, start, last), 'defendant',
                        _lognormal(rng, annual_revenue * 0.04 * math.exp(-0.3 * q), 1.0)))
        if rng.random() < years * 0.01:
            raw.append((_between(rng, start, last), 'third_party', _lognormal(rng, annual_revenue * 0.02, 0.8)))
    overdue = _long_overdue(bki)
    for _, debt in overdue:
        if rng.random() < 0.5:
            raw.append((_between(rng, max(start, as_of - timedelta(days=90)), last), 'defendant', debt))

    court = COURTS.get(egrul.region, '40')
    cases = []
    for filed_at, role, claim in sorted(raw):
        age = (as_of - filed_at).days
        status = ('pending' if age < CASE_DECIDED_DAYS and rng.random() < 0.8
                  else rng.choices(('decided', 'dismissed', 'pending'), (75, 20, 5))[0])
        cases.append(ArbitrationCase(number=f'А{court}-{rng.randint(1, 99_999):05d}/{filed_at.year}', role=role,
                                     filed_at=filed_at, claim_amount=max(_round_to(claim, 1000), 1000),
                                     status=status))

    # A bankruptcy notice is a stop factor (SPEC §7): rare, and mostly where a lender is
    # already owed for months or the company is being wound up.
    notices = []
    p = _sigmoid(-7 - 1.2 * q) + (BANKRUPTCY_INTENT_P if overdue else 0) + (0.3 if egrul.status == 'liquidating' else 0)
    if start < last and rng.random() < p:
        published = _between(rng, max(start, as_of - timedelta(days=120)), last)
        notices.append(BankruptcyNotice(published_at=published, kind='creditor_intent'))
        if rng.random() < 0.3 and published < as_of - timedelta(days=16):
            # The claim can be filed 15 days after the intent is published.
            notices.append(BankruptcyNotice(published_at=_between(rng, published + timedelta(days=16), last),
                                            kind='filing'))
    return ArbitrationPayload(cases=cases, bankruptcy_notices=notices)


# --- 115-ФЗ ---------------------------------------------------------------------------------

SHELL_TRANSIT_P = 0.85  # the bank's AML monitoring catches most shells, not all
OWN_TRANSFERS_TRANSIT_P = 0.05  # money that comes and leaves in the same month looks like transit
TRANSIT_FALSE_ALARM_P = 0.005
LIST_MATCH_P = 0.002
SHELL_LIST_MATCH_P = 0.05


def aml(rng: random.Random, *, q: float, archetype: str, egrul: EgrulPayload) -> AmlPayload:
    """The 115-ФЗ check: ЦБ ЗСК zone, list matches and transit operations.

    MOCK: there is no ЗСК platform or list screening here. The answer is drawn from the
    hidden archetype, the way a real AML system's verdict would follow the real behaviour.
    """
    if archetype == 'shell':
        transit = rng.random() < SHELL_TRANSIT_P
        zone = rng.choices(('red', 'yellow', 'green'), (50, 35, 15))[0]
        match_p = SHELL_LIST_MATCH_P
    else:
        transit = rng.random() < (OWN_TRANSFERS_TRANSIT_P if archetype == 'own_transfers' else TRANSIT_FALSE_ALARM_P)
        zone = 'yellow' if rng.random() < (0.1 if transit else _sigmoid(-4.8 - 0.5 * q)) else 'green'
        match_p = LIST_MATCH_P

    matches = []
    if rng.random() < match_p:
        people = [('director', egrul.director.full_name)] + [('founder', f.full_name) for f in egrul.founders]
        subject, name = rng.choice([('company', egrul.name), *people] if egrul.legal_form == LegalForm.OOO
                                   else [('director', egrul.director.full_name)])
        matches.append(AmlListMatch(list_name=rng.choice(AML_LISTS), subject=subject, matched_name=name))
    return AmlPayload(zsk_zone=zone, list_matches=matches, transit_operations=transit)
