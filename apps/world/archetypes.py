"""Fraud archetypes and clients with incomplete data (SPEC §1.2, §5.1, §14; T04).

Every company gets one hidden archetype. Like q, it is ground truth for the world, the
default outcomes (T05) and the personas (T12); the engine never sees it and has to find
the signs in the data:

- `shell`: a "technical" company. Money passes through the account within days, it pays
  next to no taxes, has no real staff and sits at a mass address;
- `inflater`: a real business that declares 1.5-2 times its real revenue;
- `main_bank_elsewhere`: only part of the business runs through our account;
- `new_account`: an established business that opened its account with us recently;
- `own_transfers`: receipts inflated by money from the owner's other accounts and by
  founder loans, which leaves again;
- `regular`: everyone else, exactly as T01-T03 made them.

Fraud archetypes are drawn first, from the registry data. Data gaps are drawn after the
credit-bureau report: a company with a credit line at another bank more often keeps its
main account there. All draws come from the company's own `archetype` stream; the shell
company's account uses the `turnovers` stream like any other account.

Every company also gets the annual revenue it would declare in an application
(`ApplicationRequest.declared_annual_revenue`): its real revenue, stated from memory.

SIMPLIFICATION: a company has one archetype at most. In reality an inflater can also
bank mostly elsewhere, and a shell can inflate its turnover with own transfers.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import Literal

from apps.api.contracts import (AccountsPayload, BkiPayload, CounterpartySummary, EgrulPayload, FnsPayload, GirBoReport,
                                Inflows, LegalForm, MonthlyTurnover, Outflows)

from .loans import WorldLoan
from .months import first_full_month, last_full_month, month_name
from .turnovers import HISTORY_MONTHS, REGULAR, AccountProfile, _noise, _rub

Archetype = Literal['regular', 'shell', 'inflater', 'main_bank_elsewhere', 'new_account', 'own_transfers']
FRAUD = ('shell', 'inflater')
DATA_GAPS = ('main_bank_elsewhere', 'new_account', 'own_transfers')

# Shells are young micro ООО, old enough to pass the 6-month stop-factor (SPEC §7).
SHELL_P = 0.07
SHELL_AGE_DAYS = (182, 3 * 365)
SHELL_EMPLOYEES = 1  # the nominee director
AUTHORISED_CAPITAL = 10_000
# SIMPLIFICATION: two buildings per city where shells rent a mailbox. A real mass address
# comes from the ФНС list of addresses where many companies are registered.
MASS_BUILDINGS = ('ул. Промышленная, д. 7', 'ул. Складская, д. 3, стр. 2')

MAIN_ELSEWHERE_P = 0.04
MAIN_ELSEWHERE_WITH_LINE_P = 0.2  # with an overdraft or a credit line at another bank (T03)
NEW_ACCOUNT_P = 0.06
NEW_ACCOUNT_MIN_AGE_DAYS = 400
OVERSTATE_P = 0.06  # honest clients who count cash in hand, VAT or own transfers as revenue


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _round_to(value: float, step: int) -> int:
    return int(round(value / step)) * step


# --- Archetypes ----------------------------------------------------------------------

def draw_fraud(rng: random.Random, *, q: float, legal_form: LegalForm, size: str, age_days: int) -> Archetype:
    """`shell`, `inflater` or `regular`, from the registry data alone."""
    # SIMPLIFICATION: fixed shares of fraud; a real portfolio's shares are unknown and change over time.
    young = SHELL_AGE_DAYS[0] <= age_days <= SHELL_AGE_DAYS[1]
    if legal_form == LegalForm.OOO and size == 'micro' and young and rng.random() < SHELL_P:
        return 'shell'
    # A weaker business more often overstates its revenue to get a bigger loan.
    if rng.random() < _sigmoid(-3.6 - 0.5 * q):
        return 'inflater'
    return 'regular'


def draw_data_gap(rng: random.Random, *, q: float, age_days: int, loans: list[WorldLoan],
                  bki: BkiPayload) -> Archetype:
    """`main_bank_elsewhere`, `new_account`, `own_transfers` or `regular` for a company that is no fraud."""
    business = bki.reports[0]  # the company report, or the only report of an ИП
    line_elsewhere = any(loan.kind in ('overdraft', 'credit_line') and loan.closed_at is None
                         for loan in business.loans)
    chances = (
        ('main_bank_elsewhere', MAIN_ELSEWHERE_WITH_LINE_P if line_elsewhere else MAIN_ELSEWHERE_P),
        # A client who has just moved to us has no loans with us yet.
        ('new_account', NEW_ACCOUNT_P if age_days >= NEW_ACCOUNT_MIN_AGE_DAYS and not loans else 0.0),
        # Window dressing before applying is likelier at a weaker business.
        ('own_transfers', _sigmoid(-3.5 - 0.4 * q)),
    )
    r = rng.random()
    for archetype, p in chances:
        if r < p:
            return archetype
        r -= p
    return 'regular'


def account_profile(rng: random.Random, archetype: Archetype, as_of: date) -> AccountProfile:
    if archetype == 'main_bank_elsewhere':
        return AccountProfile(our_share=round(rng.uniform(0.1, 0.6), 3))
    if archetype == 'new_account':
        return AccountProfile(opened_at=as_of - timedelta(days=rng.randint(20, 330)))
    if archetype == 'own_transfers':
        months = rng.randint(4, 12)
        # 3.5 to 12 months' worth of receipts in all, so operating inflows fall under 80% (SPEC §5.1).
        return AccountProfile(inflated_months=months, inflation=round(rng.uniform(3.5, 12) / months, 3))
    return REGULAR


# --- Declared revenue ----------------------------------------------------------------

def _stated(value: float) -> int:
    """How people state revenue: two significant digits, 12 345 678 → 12 000 000."""
    step = 10 ** max(0, math.floor(math.log10(value)) - 1)
    return int(round(value / step)) * step


def declared_revenue(rng: random.Random, archetype: Archetype, annual_revenue: int) -> int:
    """The annual revenue the client would declare in an application."""
    if archetype == 'inflater':
        factor = rng.uniform(1.5, 2.0)
    elif rng.random() < OVERSTATE_P:
        factor = rng.uniform(1.15, 1.5)
    else:
        factor = math.exp(rng.gauss(0, 0.1))
    return _stated(annual_revenue * factor)


# --- Shell company -------------------------------------------------------------------

def shell_registry(rng: random.Random, egrul: EgrulPayload, fns: FnsPayload) -> tuple[EgrulPayload, FnsPayload]:
    """A mass address, one employee and statements that show next to no profit."""
    city = egrul.address.split(', ')[0]
    address = f'{city}, {rng.choice(MASS_BUILDINGS)}, оф. {rng.randint(1, 99)}'
    financials = fns.financials
    if financials:
        revenue = financials.revenue
        profit = _round_to(revenue * rng.uniform(0.001, 0.005), 1000)
        total_assets = max(_round_to(revenue * rng.uniform(0.03, 0.1), 1000), AUTHORISED_CAPITAL + profit)
        equity = AUTHORISED_CAPITAL + profit
        financials = GirBoReport(year=financials.year, revenue=revenue,
                                 cost_of_sales=_round_to(revenue * rng.uniform(0.96, 0.99), 1000), net_profit=profit,
                                 total_assets=total_assets, equity=equity, liabilities=total_assets - equity)
    return (egrul.model_copy(update={'address': address}),
            fns.model_copy(update={'mass_address': True, 'employees': SHELL_EMPLOYEES, 'financials': financials}))


def shell_accounts(rng: random.Random, *, annual_revenue: int, registered_at: date, as_of: date) -> AccountsPayload:
    """Money comes in from a few "customers" and leaves within days to "suppliers" and in
    cash. A token tax payment, no payroll, no rent, no loans: the transit flag of SPEC §7."""
    opened_at = min(registered_at + timedelta(days=rng.randint(0, 30)), as_of - timedelta(days=1))
    last = last_full_month(as_of)
    first = max(last - HISTORY_MONTHS + 1, first_full_month(opened_at))
    volume = annual_revenue / 12  # for a shell, annual_revenue is the yearly transit volume
    tax_share = rng.uniform(0.0005, 0.003)  # enough to look alive to ФНС
    cash_share = rng.uniform(0.02, 0.4)  # some shells cash out a large part
    hold_days = rng.uniform(0.5, 2)  # how long the money stays on the account
    balance, months = 0.0, []
    for m in range(first, last + 1):
        ramp = min(1.0, 0.4 + (m - first) / 5)
        inflow = _rub(volume * ramp * _noise(rng, 0.5))
        available = balance + inflow
        spent = available * rng.uniform(0.97, 0.995)
        taxes = _rub(inflow * tax_share)
        cash = _rub(spent * cash_share)
        suppliers = _rub((spent - taxes - cash) * rng.uniform(0.7, 0.95))
        other = max(0, math.floor((spent - taxes - cash - suppliers) / 100) * 100)
        outflows = Outflows(suppliers=suppliers, payroll=0, taxes=taxes, rent=0, loan_payments=0,
                            cash_withdrawals=cash, own_transfers=0, other=other)
        end = max(0.0, available - outflows.total)
        months.append(MonthlyTurnover(
            month=month_name(m),
            inflows=Inflows(customers=inflow, own_transfers=0, loans_received=0, founder_contributions=0, other=0),
            outflows=outflows, average_balance=_rub((balance + end) / 2 + inflow * hold_days / 30)))
        balance = end

    customers = rng.randint(2, 8) if months else 0
    counterparties = CounterpartySummary(
        customers_count=customers, suppliers_count=rng.randint(3, 15) if months else 0,
        top_customer_share=round(max(rng.uniform(0.3, 0.8), 1 / customers), 3) if customers else None)
    return AccountsPayload(account_opened_at=opened_at, months=months, counterparties_12m=counterparties)
