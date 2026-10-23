"""Credit-bureau histories of the synthetic world (SPEC §5, §14; T03).

The result has the `BkiPayload` contract format: an ООО gets a company report and a
report on its main owner, an ИП gets one owner report that also holds the business
loans (SPEC §3).

The company's own loans come from `loans.py`. They are paid through our account, so
their late payments are drawn first (`add_late_payments`) and the account (T02)
follows them. The bureau also sees what our account doesn't: an overdraft or a credit
line at another bank, and the owner's personal cards and consumer loans.

A weaker company (low q) and its owner pay late more often and for longer, use more of
their limits and ask more lenders for credit. Each company draws from its own `bki`
stream.

The overdue rules of SPEC §5 (ignore technical delays and tiny amounts, weight old
episodes) belong to feature extraction (T08). The world only makes sure every rule
has cases to act on.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from apps.api.contracts import BkiInquiry, BkiLoan, BkiPayload, BkiReport, Delinquency, EgrulPayload, LegalForm

from .loans import LatePayment, WorldLoan, annuity, due_date, missed_payments

OWNER_LINK = 0.7  # correlation between the company's q and its owner's payment discipline
# SIMPLIFICATION: the reports carry the inquiries of the last year and the owner's products
# opened in the last 7 years; a real report keeps a longer history.
PERSONAL_HISTORY_DAYS = 7 * 365
INQUIRY_DAYS = 365
SHOPPING_DAYS = 28  # a burst of inquiries right before applying to us, inside the 30-day flag window (SPEC §7)
LINE_RATE = 0.24  # yearly interest on overdrafts and credit lines elsewhere: their required payment
CARD_MIN_PAYMENT = 0.05  # required card payment, share of the balance
MAX_DELINQUENCY_DAYS = 720
LENDERS = ('bank', 'mfo', 'leasing', 'other')


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _round_to(value: float, step: int) -> int:
    return int(round(value / step)) * step


def _between(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max(0, (end - start).days)))


def _poisson(rng: random.Random, mean: float) -> int:
    count, product, limit = 0, rng.random(), math.exp(-mean)
    while product > limit:
        count += 1
        product *= rng.random()
    return count


# --- Late payments -------------------------------------------------------------------

def _days(rng: random.Random, median: float, discipline: float, low: int) -> int:
    """Length of a delinquency: lognormal, longer when discipline is low."""
    return min(max(round(math.exp(rng.gauss(math.log(median) - 0.5 * discipline, 1.0))), low), MAX_DELINQUENCY_DAYS)


def draw_late_payments(rng: random.Random, discipline: float, opened_at: date, term: int | None, payment: int,
                       until: date, still_open: bool) -> list[LatePayment]:
    """Late payments of one product, all due before `until` (as_of, or the closing date).

    discipline: the borrower's q. term None: a revolving product with a required payment
    every month. still_open: the product may be overdue right now.
    """
    if payment <= 0:
        return []

    def due(k: int) -> date:
        return due_date(opened_at, k)

    due_count = 0
    while (term is None or due_count < term) and due(due_count + 1) < until:
        due_count += 1
    if due_count == 0:
        return []

    # Overdue right now: the payments stopped some days before as_of.
    current = None
    limit, stop = due_count + 1, until  # earlier episodes end before the current one starts
    if still_open and rng.random() < _sigmoid(-5.0 - 1.3 * discipline):
        days = _days(rng, 25, discipline, low=1)
        k = max((j for j in range(1, due_count + 1) if due(j) <= until - timedelta(days=days)), default=1)
        current = LatePayment(payment=k, repaid_at=None, shortfall=None)
        limit, stop = k, due(k)

    p_technical = _sigmoid(-3.8 - 0.5 * discipline)
    p_shortfall = _sigmoid(-6.0 - 0.3 * discipline)
    p_real = _sigmoid(-5.3 - 1.2 * discipline)
    late, k = [], 1
    while k < limit:
        r = rng.random()
        shortfall = None
        if r < p_technical:
            days = rng.randint(1, 5)  # the money came a few days late
        elif r < p_technical + p_shortfall:
            # A small residual left unpaid: under 1,000 ₽ or under 1% of the payment (SPEC §5).
            days = rng.randint(6, 60)
            shortfall = min(max(_round_to(rng.uniform(0.05, 1) * max(999, payment * 0.0099), 10), 10), payment - 1)
        elif r < p_technical + p_shortfall + p_real:
            days = _days(rng, 20, discipline, low=6)
        else:
            k += 1
            continue
        repaid_at = min(due(k) + timedelta(days=days), stop - timedelta(days=1))
        if repaid_at <= due(k) or (shortfall is not None and shortfall <= 0):
            break
        late.append(LatePayment(payment=k, repaid_at=repaid_at, shortfall=shortfall))
        k += 1
        while k < limit and due(k) < repaid_at:  # covered by this episode, or paid while it was open
            k += 1
    return late + ([current] if current else [])


def add_late_payments(rng: random.Random, loans: list[WorldLoan], q: float, as_of: date) -> list[WorldLoan]:
    """Late payments of the company's own loans. Drawn before the account, which follows them."""
    return [_with_late_payments(rng, loan, q, as_of) for loan in loans]


def _with_late_payments(rng: random.Random, loan: WorldLoan, discipline: float, as_of: date) -> WorldLoan:
    late = draw_late_payments(rng, discipline, loan.opened_at, loan.term_months, loan.monthly_payment, as_of,
                              still_open=loan.due_date(loan.term_months) >= as_of)
    return loan.model_copy(update={'late_payments': late})


def _delinquencies(opened_at: date, term: int | None, payment: int, late: list[LatePayment],
                   as_of: date) -> tuple[list[Delinquency], int, int]:
    """Bureau records of the late payments, and the current overdue days and amount.
    An episode still open on as_of is a record too, with its days up to as_of."""
    records, current_days, current_amount = [], 0, 0
    for x in late:
        started_at = due_date(opened_at, x.payment)
        days = ((x.repaid_at or as_of) - started_at).days
        amount = x.shortfall if x.shortfall is not None else payment * len(missed_payments(opened_at, term, x, as_of))
        records.append(Delinquency(started_at=started_at, days=days, amount=amount, payment_due=payment))
        if x.repaid_at is None:
            current_days, current_amount = days, amount
    return records, current_days, current_amount


# --- Products ------------------------------------------------------------------------

def _from_loan(loan: WorldLoan, as_of: date) -> BkiLoan:
    """A term loan or leasing in the bureau: the same loan, its balance on as_of and its late payments."""
    records, overdue_days, overdue_amount = _delinquencies(loan.opened_at, loan.term_months, loan.monthly_payment,
                                                           loan.late_payments, as_of)
    due_count = sum(loan.due_date(k) < as_of for k in range(1, loan.term_months + 1))
    if due_count == loan.term_months and not overdue_days:
        closed_at = max(day for day, _ in loan.payments())
        balance = monthly_payment = 0
    else:
        closed_at = None
        current = next((x for x in loan.late_payments if x.repaid_at is None), None)
        unpaid = len(loan.missed(current, as_of)) if current else 0
        balance, monthly_payment = loan.balance_after(due_count - unpaid), loan.monthly_payment
    return BkiLoan(loan_id=loan.loan_id, kind=loan.kind, opened_at=loan.opened_at, closed_at=closed_at,
                   amount=loan.amount, balance=balance, monthly_payment=monthly_payment,
                   current_overdue_days=overdue_days, current_overdue_amount=overdue_amount, delinquencies=records)


def _revolving(rng: random.Random, loan_id: str, kind: str, discipline: float, opened_at: date,
               closed_at: date | None, limit: int, payment_share: float, as_of: date) -> BkiLoan:
    """An overdraft, a credit line or a card: amount is the limit, balance the part in use."""
    # SIMPLIFICATION: the part in use and the required payment are taken as constant over
    # the product's life; a real revolving product moves every month.
    used = _round_to(limit * _sigmoid(rng.gauss(-0.7 - 0.9 * discipline, 1.0)), 100)
    payment = _round_to(used * payment_share, 100)
    until = closed_at or as_of
    late = draw_late_payments(rng, discipline, opened_at, None, payment, until, still_open=closed_at is None)
    records, overdue_days, overdue_amount = _delinquencies(opened_at, None, payment, late, until)
    is_open = closed_at is None
    return BkiLoan(loan_id=loan_id, kind=kind, opened_at=opened_at, closed_at=closed_at, amount=limit,
                   balance=used if is_open else 0, monthly_payment=payment if is_open else 0,
                   current_overdue_days=overdue_days, current_overdue_amount=overdue_amount, delinquencies=records)


def _closing(rng: random.Random, opened_at: date, as_of: date, p: float) -> date | None:
    """Some revolving products older than a year are already closed."""
    if (as_of - opened_at).days > 400 and rng.random() < p:
        return _between(rng, opened_at + timedelta(days=365), as_of - timedelta(days=1))
    return None


def _credit_line(rng: random.Random, loan_id: str, q: float, annual_revenue: int, registered_at: date,
                 as_of: date) -> list[BkiLoan]:
    # SIMPLIFICATION: an overdraft or a credit line at another bank runs on an account
    # there, so our account doesn't see its flows. Its required payment is the interest.
    if rng.random() >= _sigmoid(-1.6 - 0.5 * q):
        return []
    earliest = max(registered_at + timedelta(days=180), as_of - timedelta(days=5 * 365))
    latest = as_of - timedelta(days=30)
    if earliest >= latest:
        return []
    opened_at = _between(rng, earliest, latest)
    closed_at = _closing(rng, opened_at, as_of, 0.3)
    kind = 'overdraft' if rng.random() < 0.6 else 'credit_line'
    limit = _round_to(min(max(annual_revenue * math.exp(rng.gauss(math.log(0.06), 0.5)), 100_000), 30_000_000), 10_000)
    return [_revolving(rng, loan_id, kind, q, opened_at, closed_at, limit, LINE_RATE / 12, as_of)]


def _personal(rng: random.Random, company_id: str, discipline: float, as_of: date) -> list[BkiLoan]:
    """The owner's cards and consumer loans, paid from personal accounts, not from the business account."""
    # SIMPLIFICATION: no mortgages, car loans or guarantees; cards and consumer loans stand in for them.
    products: list[BkiLoan] = []

    def next_id() -> str:
        return f'{company_id}-P{len(products) + 1}'

    earliest = as_of - timedelta(days=PERSONAL_HISTORY_DAYS)
    cards = 0 if rng.random() < 0.4 else 1 + (rng.random() < 0.35) + (rng.random() < 0.1)
    for _ in range(cards):
        opened_at = _between(rng, earliest, as_of - timedelta(days=30))
        closed_at = _closing(rng, opened_at, as_of, 0.25)
        limit = _round_to(min(max(math.exp(rng.gauss(math.log(120_000), 0.7)), 15_000), 1_000_000), 5_000)
        products.append(_revolving(rng, next_id(), 'card', discipline, opened_at, closed_at, limit,
                                   CARD_MIN_PAYMENT, as_of))
    if rng.random() < _sigmoid(-1.2 - 0.5 * discipline):
        for _ in range(1 + (rng.random() < 0.25)):
            opened_at = _between(rng, as_of - timedelta(days=6 * 365), as_of - timedelta(days=15))
            term = rng.choice((12, 24, 36, 48, 60))
            rate = round(min(max(0.22 + 0.03 * max(0.0, -discipline) + rng.uniform(-0.04, 0.06), 0.12), 0.4), 4)
            amount = _round_to(min(max(math.exp(rng.gauss(math.log(300_000), 0.7)), 50_000), 3_000_000), 10_000)
            loan = WorldLoan(loan_id=next_id(), kind='loan', opened_at=opened_at, term_months=term, amount=amount,
                             rate=rate, monthly_payment=annuity(amount, rate, term))
            products.append(_from_loan(_with_late_payments(rng, loan, discipline, as_of), as_of))
    return products


# --- Inquiries -----------------------------------------------------------------------

def _inquiries(rng: random.Random, discipline: float, products: list[BkiLoan], as_of: date) -> list[BkiInquiry]:
    start = as_of - timedelta(days=INQUIRY_DAYS)
    found = []
    # A new product follows its lender's inquiry, often with other lenders' inquiries too.
    for product in products:
        if product.opened_at < start:
            continue
        lender = 'leasing' if product.kind == 'leasing' else 'bank'
        for _ in range(1 + (rng.random() < 0.4) + (rng.random() < 0.15)):
            day = product.opened_at - timedelta(days=rng.randint(0, 30))
            if day >= start:
                found.append((day, lender))
    # Inquiries that led nowhere: about one a year at q = 0, more at weaker borrowers, often by МФО.
    weights = (70, 10 * math.exp(-0.7 * discipline), 10, 10)
    for _ in range(_poisson(rng, 0.8 * math.exp(-0.5 * discipline))):
        found.append((_between(rng, start, as_of - timedelta(days=1)), rng.choices(LENDERS, weights)[0]))
    # Shopping around: several lenders asked within the last month (the SPEC §7 flag).
    if rng.random() < _sigmoid(-3.6 - 0.8 * discipline):
        for _ in range(rng.randint(3, 6)):
            found.append((as_of - timedelta(days=rng.randint(1, SHOPPING_DAYS)), rng.choices(LENDERS, weights)[0]))
    return [BkiInquiry(inquired_at=day, lender_type=lender) for day, lender in sorted(found)]


# --- Report --------------------------------------------------------------------------

def credit_report(rng: random.Random, *, company_id: str, q: float, egrul: EgrulPayload, annual_revenue: int,
                  loans: list[WorldLoan], as_of: date) -> BkiPayload:
    """The bureau reports. `loans` must already carry their late payments (`add_late_payments`)."""
    owner_discipline = OWNER_LINK * q + math.sqrt(1 - OWNER_LINK ** 2) * rng.gauss(0, 1)
    business = [_from_loan(loan, as_of) for loan in loans]
    business += _credit_line(rng, f'{company_id}-B1', q, annual_revenue, egrul.registered_at, as_of)
    personal = _personal(rng, company_id, owner_discipline, as_of)
    if egrul.legal_form == LegalForm.IP:
        products = business + personal
        return BkiPayload(reports=[_report('owner', egrul.inn, products, _inquiries(rng, q, products, as_of))])
    # SIMPLIFICATION: only the main owner (founders are sorted by share) is checked; a bank
    # would also check the other significant founders and the guarantors.
    owner = egrul.founders[0]
    return BkiPayload(reports=[
        _report('company', egrul.inn, business, _inquiries(rng, q, business, as_of)),
        _report('owner', owner.inn, personal, _inquiries(rng, owner_discipline, personal, as_of)),
    ])


def _report(subject: str, inn: str, products: list[BkiLoan], inquiries: list[BkiInquiry]) -> BkiReport:
    return BkiReport(subject=subject, subject_inn=inn, loans=sorted(products, key=lambda x: (x.opened_at, x.loan_id)),
                     inquiries=inquiries)
