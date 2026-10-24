"""Account-behaviour features (SPEC §8) from our own account turnovers.

All of them look at the last 12 full months, or at what there is if the history is
shorter, and need at least `MIN_MONTHS` (6) months: with less the history is THIN
(SPEC §5.1) and the features are "no data". Counts of months are scaled to 12 months,
so 8 months with tax payments out of 8 read as 12. The inflow trend compares two
half-years and needs the full 12 months.

"Operating inflows" are the inflows from customers: own transfers, loans received and
founder contributions are not business income (SPEC §5.1).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from apps.api.contracts import AccountsPayload, MonthlyTurnover

WINDOW = 12
MIN_MONTHS = 6
DAYS_PER_MONTH = 30


@dataclass(frozen=True)
class AccountFeatures:
    inflow_trend_12m: float | None
    inflow_cv_12m: float | None
    months_without_inflows_12m: int | None
    customers_count_12m: int | None
    top_customer_share: float | None
    cash_withdrawal_share: float | None
    balance_days_of_expenses: float | None
    tax_payment_months_12m: int | None
    payroll_payment_months_12m: int | None


NO_ACCOUNT_DATA = AccountFeatures(*[None] * 9)


def window(accounts: AccountsPayload) -> list[MonthlyTurnover]:
    return accounts.months[-WINDOW:]


def _per_12(count: int, months: int) -> int:
    return round(count * WINDOW / months)


def _ratio(numerator: float, denominator: float, digits: int = 3) -> float | None:
    return round(numerator / denominator, digits) if denominator else None


def account_features(accounts: AccountsPayload | None) -> AccountFeatures:
    if accounts is None or len(window(accounts)) < MIN_MONTHS:
        return NO_ACCOUNT_DATA
    months = window(accounts)
    n = len(months)
    inflows = [m.inflows.customers for m in months]
    outflows = [m.outflows.total for m in months]
    mean_inflow = statistics.fmean(inflows)
    first_half, second_half = sum(inflows[:6]), sum(inflows[6:])
    trend = round(second_half / first_half - 1, 3) if n == WINDOW and first_half else None
    top = accounts.counterparties_12m.top_customer_share
    return AccountFeatures(
        inflow_trend_12m=trend,
        inflow_cv_12m=_ratio(statistics.pstdev(inflows), mean_inflow),
        months_without_inflows_12m=_per_12(sum(x == 0 for x in inflows), n),
        customers_count_12m=accounts.counterparties_12m.customers_count,
        top_customer_share=None if top is None else round(top, 3),
        cash_withdrawal_share=_ratio(sum(m.outflows.cash_withdrawals for m in months), sum(outflows)),
        balance_days_of_expenses=_ratio(statistics.fmean(m.average_balance for m in months),
                                        sum(outflows) / n / DAYS_PER_MONTH, digits=1),
        tax_payment_months_12m=_per_12(sum(m.outflows.taxes > 0 for m in months), n),
        payroll_payment_months_12m=_per_12(sum(m.outflows.payroll > 0 for m in months), n),
    )
