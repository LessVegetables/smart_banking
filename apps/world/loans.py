"""Company loans of the synthetic world (T02).

The loans are drawn here because their payments go through the account. T03 builds
the credit-bureau report of the company from the same loans, so the account and the
bureau agree. Each company draws them from its own `loans` stream.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from apps.api.contracts import LegalForm

from .industries import Industry
from .months import month_index

LEASING_INDUSTRIES = {'transport', 'construction', 'agriculture', 'manufacturing'}
MAX_LOAN_AGE_DAYS = 5 * 365  # older loans are closed and leave no trace in the 24-month window


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

    @property
    def first_payment_month(self) -> int:
        return month_index(self.opened_at) + 1

    @property
    def last_payment_month(self) -> int:
        return month_index(self.opened_at) + self.term_months

    def pays_in(self, month: int) -> bool:
        return self.first_payment_month <= month <= self.last_payment_month

    def balance_after(self, payments: int) -> int:
        """Principal left after a number of scheduled payments."""
        payments = min(max(payments, 0), self.term_months)
        r = self.rate / 12
        grown = (1 + r) ** payments
        return max(0, round(self.amount * grown - self.monthly_payment * (grown - 1) / r))


def annuity(amount: int, rate: float, term_months: int) -> int:
    r = rate / 12
    return round(amount * r / (1 - (1 + r) ** -term_months))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def draw_loans(rng, company_id: str, q: float, industry: Industry, legal_form: LegalForm,
               annual_revenue: int, registered_at: date, as_of: date) -> list[WorldLoan]:
    # SIMPLIFICATION: only term loans and leasing, all serviced on schedule through this
    # account. Credit lines, overdrafts, cards and delinquencies come with T03.
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
