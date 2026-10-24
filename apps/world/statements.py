"""Statements from other banks (T12): what a client with incomplete data would upload (T23, SPEC §5.1).

Only the world knows what runs through other banks, so the statement comes from the company's hidden
account profile (T04), in its own stream `statement`:

- `main_bank_elsewhere`: the other `1 - our_share` of the non-cash business, the same 24 months as ours. Loans
  are paid from our account (T02), so the other bank's statement has no loan payments;
- `new_account`: the old bank's account before ours was opened, then dormant (zero months) up to the last full
  month, so the statement is recent enough for the checks of T23.

Other archetypes get no statement: a regular client has nothing elsewhere, and the own transfers of
`own_transfers` come from the owner's personal accounts, not a business account. The format is the parsed
JSON statement of `apps/api/STATEMENTS.md` (`Statement` in `apps/api/statements.py`).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from apps.api.contracts import Inflows, MonthlyTurnover, Outflows

from .generator import Company, company_rng
from .industries import BY_KEY
from .months import first_full_month, last_full_month, month_index, month_name
from .turnovers import HISTORY_MONTHS, AccountProfile, generate_accounts

# SIMPLIFICATION: one made-up bank for every statement; the name only labels the file.
OTHER_BANK = 'Банк «Северная звезда»'


def _empty(month: str) -> MonthlyTurnover:
    zero_in = Inflows(customers=0, own_transfers=0, loans_received=0, founder_contributions=0, other=0)
    zero_out = Outflows(suppliers=0, payroll=0, taxes=0, rent=0, loan_payments=0, cash_withdrawals=0,
                        own_transfers=0, other=0)
    return MonthlyTurnover(month=month, inflows=zero_in, outflows=zero_out, average_balance=0)


def _index(m: MonthlyTurnover) -> int:
    return month_index(date.fromisoformat(m.month + '-01'))


def other_bank_statement(seed: int, company: Company, as_of: date) -> dict[str, Any] | None:
    """The company's statement from its other bank, or None when it has no business account elsewhere."""
    profile = company.account_profile
    if company.archetype == 'main_bank_elsewhere':
        other = AccountProfile(our_share=round(1 - profile.our_share, 3))
        loans = []
    elif company.archetype == 'new_account':
        other = AccountProfile()  # the old account, opened right after registration
        loans = company.loans  # paid from the old account until ours was opened
    else:
        return None
    accounts = generate_accounts(
        company_rng(seed, company.company_id, 'statement'), q=company.q, industry=BY_KEY[company.industry],
        legal_form=company.legal_form, employees=company.employees, annual_revenue=company.annual_revenue,
        revenue_growth=company.revenue_growth, registered_at=company.egrul.registered_at, fns=company.fns,
        loans=loans, as_of=as_of, profile=other)
    months = list(accounts.months)
    if company.archetype == 'new_account':
        # SIMPLIFICATION: the business moved to us in full when our account was opened; the old account stays
        # open with no turnover. A real client would keep some payments there for a while.
        ours = first_full_month(company.accounts.account_opened_at)
        months = [m for m in months if _index(m) < ours]
        months += [_empty(month_name(m)) for m in range(ours, last_full_month(as_of) + 1)]
        months = months[-HISTORY_MONTHS:]
    if not months:
        return None
    return {'bank_name': OTHER_BANK, 'inn': company.inn, 'months': [m.model_dump(mode='json') for m in months]}
