"""Account turnovers of the synthetic world (SPEC §5, §14; T02).

Up to 24 full months before as_of, in the `AccountsPayload` contract format. Customer
receipts follow the company's revenue, its industry's seasonality and its growth
trend. Costs follow the industry's cost structure, loan payments follow the actual
payments of the loans from `loans.py` (late payments included, T03), and the balance
is simulated month by month: a surplus goes to the owner, a shortfall is covered by
the owner.

A weaker company (low q) has noisier receipts, more months without receipts, delayed
payroll and tax payments and a thinner cash cushion, so it more often needs money
from its owner. Each company draws from its own `turnovers` stream.

Fraud archetypes and incomplete-data clients (main account elsewhere, a new account,
receipts inflated by own transfers) are T04. Here every company banks with us from
registration on.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from apps.api.contracts import (AccountsPayload, CounterpartySummary, FnsPayload, Inflows, LegalForm,
                                MonthlyTurnover, Outflows)

from .industries import Industry
from .loans import WorldLoan, payments_by_month
from .months import calendar_month, first_full_month, last_full_month, month_index, month_name

HISTORY_MONTHS = 24
RETAIL_CUSTOMERS = 200  # an industry with this many customers sells to the public
INDEXATION = 1.07  # payroll and rent go up every January
PAYROLL_TAX = 0.3  # insurance contributions on payroll, paid monthly
IP_FIXED_CONTRIBUTIONS = 13_000  # the owner's own contributions, per quarter
TAX_MONTHS = (1, 4, 7, 10)  # revenue tax for the previous quarter

# SIMPLIFICATION: revenue taxes are a flat share of receipts by regime, paid quarterly;
# an ООО pays one blended rate. Real taxes depend on the regime's base, VAT and deductions.
REVENUE_TAX = {'usn_income': 0.05, 'usn_income_expense': 0.02, 'patent': 0.01, 'osno': 0.04, 'ooo': 0.035}


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _rub(value: float) -> int:
    """Whole hundreds of rubles, never negative."""
    return max(0, int(round(value / 100)) * 100)


def _noise(rng: random.Random, sigma: float) -> float:
    """Lognormal with mean 1."""
    return math.exp(rng.gauss(-sigma * sigma / 2, sigma))


def generate_accounts(rng: random.Random, *, q: float, industry: Industry, legal_form: LegalForm,
                      employees: int, annual_revenue: int, revenue_growth: float, registered_at: date,
                      fns: FnsPayload, loans: list[WorldLoan], as_of: date) -> AccountsPayload:
    ip = legal_form == LegalForm.IP
    # SIMPLIFICATION: every company opens its account with us right after registration and
    # runs all non-cash revenue through it. T04 adds clients who bank mostly elsewhere.
    opened_at =min(registered_at + timedelta(days=rng.randint(0, 45)), as_of - timedelta(days=1))
    last = last_full_month(as_of)
    first = max(last - HISTORY_MONTHS + 1, first_full_month(opened_at))
    months = range(first, last + 1)

    # --- Receipts from customers -------------------------------------------------
    # SIMPLIFICATION: cash takings never reach the account; all other revenue does.
    cash_part = min(0.5, industry.cash_share * rng.uniform(0.6, 1.4))
    base = annual_revenue / 12 * (1 - cash_part)  # average monthly receipts over the last 12 months
    growth = (1 + revenue_growth) ** (1 / 12)

    def shape(m: int) -> float:  # seasonality and trend, centred on the last 12 months
        return industry.seasonality[calendar_month(m) - 1] * growth ** (m - (last - 5.5))

    norm = 12 / sum(shape(m) for m in range(last - 11, last + 1))
    registered = month_index(registered_at)
    sigma = min(max(0.1 - 0.04 * q, 0.04), 0.3)
    gap_p = _sigmoid(-5.0 - 1.2 * q)
    level, receipts = {}, {}  # level: what the business earned; receipts: what reached the account
    for m in months:
        # SIMPLIFICATION: a new business ramps up linearly over its first 9 months.
        ramp = min(1.0, 0.5 + (m - registered) / 18)
        level[m] = base * norm * shape(m) * ramp * _noise(rng, sigma)
        receipts[m] = 0 if rng.random() < gap_p else _rub(level[m])

    opened = month_index(opened_at)

    def received(m: int) -> float:  # receipts, estimated for the months before the window
        if m in receipts:
            return receipts[m]
        return base if m >= max(registered, opened) else 0

    # --- Cost structure -----------------------------------------------------------
    # SIMPLIFICATION: costs are fixed shares of revenue per industry, not a company's own cost base.
    expected_staff = annual_revenue / industry.revenue_per_employee
    staffing = min(employees / expected_staff, 1.5) if employees else 0.0
    payroll_base = base * industry.payroll_share * staffing
    rent_base = 0.0 if ip and employees == 0 and rng.random() < 0.4 else base * industry.rent_share  # works from home
    regime = fns.tax_return.regime if fns.tax_return else ('usn_income' if ip else 'ooo')
    tax_share = REVENUE_TAX[regime]
    gir_bo = fns.financials
    if gir_bo and gir_bo.revenue > 0:
        margin = gir_bo.net_profit / gir_bo.revenue
    else:
        margin = rng.gauss(0.04 + 0.03 * q, 0.04) + (0.05 if ip else 0)  # an ИП's margin includes the owner's pay
    other_share = 1 - industry.cogs_share - tax_share - (rent_base + payroll_base * (1 + PAYROLL_TAX)) / base - margin
    other_share = min(max(other_share, 0.02), 0.25)

    # The owner keeps a cushion of a few days to a few months of costs; better companies keep more.
    cushion_days = min(max(math.exp(rng.gauss(math.log(15) + 0.4 * q, 0.4)), 2), 90)
    monthly_costs = (base * (industry.cogs_share + other_share + tax_share) + rent_base
                     + payroll_base * (1 + PAYROLL_TAX))
    target = monthly_costs * cushion_days / 30
    balance = target if opened < first else 0.3 * target

    payroll_delay_p = _sigmoid(-4.5 - 1.2 * q)
    tax_skip_p = _sigmoid(-4.0 - 1.3 * q)
    # A company with tax debt in ФНС stops paying taxes over the last months.
    tax_debt_months = 6 if fns.tax_debt > 0 else 0
    payroll_due = tax_due = 0.0
    loan_paid = payments_by_month(loans)

    result = []
    for m in months:
        index = INDEXATION ** (m // 12 - last // 12)
        prev = level.get(m - 1, level[m])

        payroll_due += payroll_base * index
        tax_due += payroll_base * index * PAYROLL_TAX
        if calendar_month(m) in TAX_MONTHS:
            tax_due += tax_share * sum(received(k) for k in range(m - 3, m)) + (IP_FIXED_CONTRIBUTIONS if ip else 0)
        payroll = 0 if rng.random() < payroll_delay_p else _rub(payroll_due)
        payroll_due = max(0.0, payroll_due - payroll)
        late = m > last - tax_debt_months
        tax = 0 if rng.random() < tax_skip_p + (0.4 if late else 0) else _rub(tax_due)
        tax_due = max(0.0, tax_due - tax)

        outflows = dict(
            suppliers=_rub(industry.cogs_share * (0.6 * level[m] + 0.4 * prev) * _noise(rng, 0.08)),
            payroll=payroll,
            taxes=tax,
            rent=_rub(rent_base * index),
            loan_payments=loan_paid[m],
            cash_withdrawals=_rub(base * rng.uniform(0.005, 0.02)),
            own_transfers=0,
            other=_rub(other_share * (0.5 * base + 0.5 * level[m]) * _noise(rng, 0.1)),
        )
        inflows = dict(
            customers=receipts[m],
            own_transfers=_rub(base * rng.uniform(0.02, 0.1)) if rng.random() < 0.06 else 0,
            loans_received=sum(x.amount for x in loans if x.kind == 'loan' and month_index(x.opened_at) == m),
            founder_contributions=0,
            other=_rub(base * rng.uniform(0.002, 0.015)) if rng.random() < 0.15 else 0,  # refunds, interest
        )

        start = balance
        balance += sum(inflows.values()) - sum(outflows.values())
        # A shortfall is covered by the owner: a contribution to an ООО, a transfer from an ИП's own card.
        floor = 0.1 * target
        if balance < floor:
            top_up = math.ceil((floor - balance + 0.3 * target) / 10_000) * 10_000
            inflows['own_transfers' if ip else 'founder_contributions'] += top_up
            balance += top_up
        # A surplus goes to the owner: an ИП takes it monthly, an ООО moves it to its own deposit now and then.
        excess = balance - target
        if excess > 0 and (ip or rng.random() < 0.5):
            payout = _rub(excess * rng.uniform(0.4, 0.9))
            to_card = _rub(payout * 0.6) if ip else payout
            outflows['own_transfers'] += to_card
            outflows['cash_withdrawals'] += payout - to_card
            balance -= payout

        result.append(MonthlyTurnover(month=month_name(m), inflows=Inflows(**inflows),
                                      outflows=Outflows(**outflows), average_balance=_rub((start + balance) / 2)))

    return AccountsPayload(account_opened_at=opened_at, months=result,
                           counterparties_12m=_counterparties(rng, q, industry, annual_revenue, result))


def _counterparties(rng: random.Random, q: float, industry: Industry, annual_revenue: int,
                    months: list[MonthlyTurnover]) -> CounterpartySummary:
    last12 = months[-12:]
    scale = math.sqrt(annual_revenue / industry.revenue_median)

    def count(typical: int, active: int) -> int:
        if active == 0:
            return 0
        return max(1, round(typical * scale * active / 12 * math.exp(0.15 * q + rng.gauss(0, 0.3))))

    customers = count(industry.customers, sum(m.inflows.customers > 0 for m in last12))
    suppliers = count(industry.suppliers, sum(m.outflows.suppliers > 0 for m in last12))
    if customers == 0:
        top = None
    elif industry.customers >= RETAIL_CUSTOMERS:
        # SIMPLIFICATION: card and СБП receipts are split by the paying person, not lumped
        # under the acquiring bank, so a shop has no dominant customer.
        top = max(rng.uniform(0.005, 0.05), 1 / customers)
    else:
        # Fewer customers mean a bigger largest one; weaker companies depend on one buyer more.
        top = min(max(1.3 / math.sqrt(customers) * math.exp(-0.15 * q + rng.gauss(0, 0.2)), 1 / customers), 1.0)
    return CounterpartySummary(customers_count=customers, suppliers_count=suppliers,
                               top_customer_share=None if top is None else round(top, 3))
