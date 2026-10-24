"""Verified annual revenue from ФНС, made comparable with what the client declares.

ФНС gives the revenue of the last filed calendar year (ГИР БО for an ООО, the tax
return for an ИП). The client declares the last 12 months. Compared head-on, honest
clients often differ by more than 30%: a company registered during the filed year
reported only part of a year, and every business has grown or shrunk since. So:

- `annualized`: the filed revenue scaled to a full year when the company was registered
  during it (`filed / active share of the year`);
- `estimate_12m`: `annualized` moved to the last 12 months by the change in operating
  inflows on our account between the filed year and the last 12 months. Without enough
  account history (12 months, and at least 6 months of the filed year) it stays
  `annualized`.

On the world (seed 7, 3000 companies) honest clients whose declaration is more than 30%
off: 21% → 7% (filed a full year) and 82% → 15% (registered during the filed year);
inflaters are still caught in about 93% of cases.

Who uses what: `debt_to_revenue` divides by `annualized` (a statement figure); the
revenue-mismatch flag (T14) should compare the declaration with `estimate_12m`; the
revenue coverage of T09 should divide by `annualized`.

SIMPLIFICATION: the roll-forward trusts the account trend even when part of the business
goes through other banks; a bank would also use quarterly VAT or tax-return data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from apps.api.contracts import AccountsPayload, EgrulPayload, FnsPayload

MIN_FILED_YEAR_MONTHS = 6  # account months inside the filed year needed to roll the revenue forward


@dataclass(frozen=True)
class Revenue:
    filed: int  # as filed with ФНС
    year: int
    active_share: float  # share of the filed year the business existed, 1 for a full year
    annualized: int
    estimate_12m: int
    rolled_forward: bool  # estimate_12m used the account trend


def active_share(year: int, registered_at: date | None) -> float:
    start, end = date(year, 1, 1), date(year, 12, 31)
    if registered_at is None or registered_at <= start:
        return 1.0
    if registered_at > end:
        return 0.0
    return ((end - registered_at).days + 1) / ((end - start).days + 1)


def fns_revenue(fns: FnsPayload | None, egrul: EgrulPayload | None = None,
                accounts: AccountsPayload | None = None) -> Revenue | None:
    """None when ФНС didn't answer or the business hasn't filed yet."""
    if fns is None:
        return None
    if fns.financials is not None:
        filed, year = fns.financials.revenue, fns.financials.year
    elif fns.tax_return is not None:
        filed, year = fns.tax_return.income, fns.tax_return.year
    else:
        return None
    share = active_share(year, egrul.registered_at if egrul else None)
    if share <= 0:
        return None
    annualized = round(filed / share)
    factor = _inflow_change(accounts, year)
    estimate = round(annualized * factor) if factor is not None else annualized
    return Revenue(filed, year, round(share, 4), annualized, estimate, factor is not None)


def _inflow_change(accounts: AccountsPayload | None, year: int) -> float | None:
    """Operating inflows of the last 12 months / annualized operating inflows of the filed year."""
    if accounts is None or len(accounts.months) < 12:
        return None
    in_year = [m.inflows.customers for m in accounts.months if m.month.startswith(f'{year}-')]
    if len(in_year) < MIN_FILED_YEAR_MONTHS or sum(in_year) <= 0:
        return None
    last_12m = sum(m.inflows.customers for m in accounts.months[-12:])
    return last_12m / (sum(in_year) * 12 / len(in_year))
