"""Company loans of the synthetic world (T02).

The loans are drawn here because their payments go through the account. T03 builds
the credit-bureau report of the company from the same loans and adds their late
payments; the account follows the actual payments (`WorldLoan.payments`), so the
account and the bureau agree. Each company draws its loans from its own `loans` stream.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from apps.api.contracts import LegalForm

from .industries import Industry
from .months import month_index, month_start

LEASING_INDUSTRIES = {'transport', 'construction', 'agriculture', 'manufacturing'}
MAX_LOAN_AGE_DAYS = 5 * 365  # older loans are closed and leave no trace in the 24-month window


def due_date(opened_at: date, k: int) -> date:
    """Due date of the k-th monthly payment (from 1): the opening day of the month, at most the 28th."""
    # SIMPLIFICATION: a real schedule sets its own payment day.
    return month_start(month_index(opened_at) + k).replace(day=min(opened_at.day, 28))


class LatePayment(BaseModel):
    """A late scheduled payment (T03). Internal to the world, not a cross-track contract."""
    model_config = ConfigDict(extra='forbid')

    payment: int  # number of the first late payment, from 1
    repaid_at: date | None  # when the overdue amount was paid; None: still overdue on as_of
    # None: whole payments were missed, every one due before repaid_at is paid on that day.
    # A number: only this part of the payment was late, the rest was paid on time.
    shortfall: int | None


def missed_payments(opened_at: date, term: int | None, late: LatePayment, as_of: date) -> list[int]:
    """Numbers of the payments a late payment covers: every payment that fell due while it
    was overdue, or the one payment with a shortfall. term None: a revolving product."""
    if late.shortfall is not None:
        return [late.payment]
    end = late.repaid_at or as_of
    missed = [late.payment]
    while (term is None or missed[-1] < term) and due_date(opened_at, missed[-1] + 1) < end:
        missed.append(missed[-1] + 1)
    return missed


class WorldLoan(BaseModel):
    """One company loan. Internal to the world, not a cross-track contract."""
    model_config = ConfigDict(extra='forbid')

    loan_id: str
    kind: Literal['loan', 'leasing']  # leasing pays the supplier, so there is no inflow
    opened_at: date
    term_months: int
    amount: int
    rate: float  # yearly
    monthly_payment: int
    late_payments: list[LatePayment] = []  # drawn by T03 (`bki.py`), sorted, never overlapping

    @property
    def first_payment_month(self) -> int:
        return month_index(self.opened_at) + 1

    @property
    def last_payment_month(self) -> int:
        return month_index(self.opened_at) + self.term_months

    def pays_in(self, month: int) -> bool:
        """The schedule: a payment is due in this month."""
        return self.first_payment_month <= month <= self.last_payment_month

    def due_date(self, k: int) -> date:
        return due_date(self.opened_at, k)

    def missed(self, late: LatePayment, as_of: date) -> list[int]:
        return missed_payments(self.opened_at, self.term_months, late, as_of)

    def payments(self) -> list[tuple[date, int]]:
        """What was actually paid and when: the schedule moved by the late payments.
        Payments still overdue are left out."""
        # SIMPLIFICATION: a late payment is repaid in one go, without penalty interest,
        # and the rest of the schedule doesn't move.
        paid = {k: [(self.due_date(k), self.monthly_payment)] for k in range(1, self.term_months + 1)}
        for late in self.late_payments:
            if late.shortfall is not None:
                due, amount = paid[late.payment][0]
                paid[late.payment] = [(due, amount - late.shortfall)]
                if late.repaid_at:
                    paid[late.payment].append((late.repaid_at, late.shortfall))
                continue
            # Payments still overdue on as_of are never paid here: the account ends before as_of.
            for k in self.missed(late, date.max):
                paid[k] = [(late.repaid_at, self.monthly_payment)] if late.repaid_at else []
        return sorted(p for parts in paid.values() for p in parts)

    def balance_after(self, payments: int) -> int:
        """Principal left after a number of scheduled payments."""
        payments = min(max(payments, 0), self.term_months)
        r = self.rate / 12
        grown = (1 + r) ** payments
        return max(0, round(self.amount * grown - self.monthly_payment * (grown - 1) / r))


def payments_by_month(loans: list[WorldLoan]) -> Counter[int]:
    """Loan payments actually made, by month index: what the account shows."""
    paid = Counter()
    for loan in loans:
        for day, amount in loan.payments():
            paid[month_index(day)] += amount
    return paid


def annuity(amount: int, rate: float, term_months: int) -> int:
    r = rate / 12
    return round(amount * r / (1 - (1 + r) ** -term_months))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def draw_loans(rng, company_id: str, q: float, industry: Industry, legal_form: LegalForm,
               annual_revenue: int, registered_at: date, as_of: date) -> list[WorldLoan]:
    # SIMPLIFICATION: only term loans and leasing, all serviced through this account.
    # Their late payments are drawn by T03; credit lines, overdrafts and cards elsewhere
    # appear only in the bureau report (T03).
    earliest = max(registered_at + timedelta(days=90), as_of - timedelta(days=MAX_LOAN_AGE_DAYS))
    latest = as_of - timedelta(days=15)
    # Weaker companies borrow more often (about 40% of companies at q = 0).
    if earliest >= latest or rng.random() >= _sigmoid(-0.4 - 0.4 * q):
        return []
    count = 1 + (rng.random() < 0.3) + (rng.random() < 0.1)
    leasing_p = 0.45 if industry.key in LEASING_INDUSTRIES else 0.1
    loans = []
    for _ in range(count):
        opened_at = earliest + timedelta(days=rng.randint(0, (latest - earliest).days))
        kind = 'leasing' if legal_form == LegalForm.OOO and rng.random() < leasing_p else 'loan'
        term = rng.choice((36, 48, 60) if kind == 'leasing' else (12, 18, 24, 36))
        rate = round(min(max(0.19 + 0.02 * max(0.0, -q) + rng.uniform(-0.02, 0.04), 0.15), 0.32), 4)
        amount = annual_revenue * math.exp(rng.gauss(math.log(0.12) - 0.15 * q, 0.5))
        # Keep one payment under 10% of the average monthly revenue.
        cap = 0.1 * annual_revenue / 12 / (annuity(1_000_000, rate, term) / 1_000_000)
        amount = int(round(min(max(min(amount, cap), 100_000), 15_000_000) / 10_000)) * 10_000
        loans.append((opened_at, kind, term, amount, rate))
    return [WorldLoan(loan_id=f'{company_id}-L{k + 1}', kind=kind, opened_at=opened_at, term_months=term,
                      amount=amount, rate=rate, monthly_payment=annuity(amount, rate, term))
            for k, (opened_at, kind, term, amount, rate) in enumerate(sorted(loans))]
