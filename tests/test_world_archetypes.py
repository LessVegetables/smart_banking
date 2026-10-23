"""Fraud archetypes and clients with incomplete data in the synthetic world (T04)."""
import statistics
from collections import Counter
from datetime import date, timedelta

import pytest

from apps.api import contracts as c
from apps.world import generate_world
from apps.world.archetypes import AUTHORISED_CAPITAL, DATA_GAPS, FRAUD, MASS_BUILDINGS

AS_OF = date(2026, 10, 1)


@pytest.fixture(scope='module')
def world():
    return generate_world(seed=7, n=3000, as_of=AS_OF)


def _group(world, archetype):
    return [x for x in world.companies if x.archetype == archetype]


def _last12(x):
    return x.accounts.months[-12:]


def _receipts(months):
    return sum(m.inflows.customers for m in months)


def _operating_share(x):
    """SPEC §5.1: receipts from customers / all inflows, last 12 months."""
    inflows = sum(m.inflows.total for m in _last12(x))
    return _receipts(_last12(x)) / inflows if inflows else None


def _tax_share(x):
    months = _last12(x)
    return sum(m.outflows.taxes for m in months) / sum(m.inflows.total for m in months)


def _balance_days(x):
    months = _last12(x)
    return statistics.mean(m.average_balance for m in months) / (sum(m.outflows.total for m in months) / 365)


def _line_elsewhere(x):
    return any(loan.kind in ('overdraft', 'credit_line') and loan.closed_at is None for loan in x.bki.reports[0].loans)


def test_every_archetype_is_in_the_world(world):
    shares = {k: v / len(world.companies) for k, v in Counter(x.archetype for x in world.companies).items()}
    assert set(shares) == {'regular', *FRAUD, *DATA_GAPS}
    assert 0.8 < shares['regular'] < 0.88
    assert 0.007 < shares['shell'] < 0.03
    assert 0.04 < shares['main_bank_elsewhere'] < 0.1
    for archetype in ('inflater', 'new_account', 'own_transfers'):
        assert 0.015 < shares[archetype] < 0.05
    # Personas need ИП and ООО for every case (T12); a shell is always an ООО.
    for archetype in ('inflater', *DATA_GAPS):
        assert {x.legal_form for x in _group(world, archetype)} == {c.LegalForm.IP, c.LegalForm.OOO}


def test_shell_registry(world):
    shells = _group(world, 'shell')
    assert all(x.fns.mass_address == (x.archetype == 'shell') for x in world.companies)
    buildings = Counter()
    for x in shells:
        assert x.legal_form == c.LegalForm.OOO and x.size == 'micro'
        assert 182 <= (AS_OF - x.egrul.registered_at).days <= 3 * 365, 'young, but past the 6-month stop-factor'
        assert x.employees == x.fns.employees == 1
        building = x.egrul.address.rsplit(', оф. ', 1)[0]
        assert building.split(', ', 1)[1] in MASS_BUILDINGS
        buildings[building] += 1
        f = x.fns.financials
        if f:
            assert f.cost_of_sales > 0.95 * f.revenue and 0 <= f.net_profit < 0.006 * f.revenue
            assert f.equity == AUTHORISED_CAPITAL + f.net_profit
    assert max(buildings.values()) >= 3, 'several shells share one building'
    others = [x for x in world.companies if x.archetype != 'shell']
    assert not any(b in x.egrul.address for x in others for b in MASS_BUILDINGS)


def test_shell_money_passes_through(world):
    for x in _group(world, 'shell'):
        months = _last12(x)
        inflows = sum(m.inflows.total for m in months)
        assert inflows == _receipts(months) > 0
        assert 0.98 < sum(m.outflows.total for m in months) / inflows < 1.02, 'what comes in goes out'
        assert all(m.outflows.payroll == m.outflows.rent == m.outflows.loan_payments == 0 for m in x.accounts.months)
        assert _tax_share(x) < 0.004
        assert _balance_days(x) < 5
        assert not x.loans and {loan.kind for loan in x.bki.reports[0].loans} <= {'overdraft', 'credit_line'}
        cp = x.accounts.counterparties_12m
        assert 2 <= cp.customers_count <= 8 and cp.top_customer_share >= 0.3
    regular = [x for x in _group(world, 'regular') if len(x.accounts.months) >= 12]
    assert statistics.median(map(_tax_share, regular)) > 0.04
    assert statistics.median(map(_balance_days, regular)) > 10


def test_declared_revenue(world):
    for x in world.companies:
        assert len(str(x.declared_annual_revenue).rstrip('0')) <= 2, 'stated with two significant digits'

    def ratio(x):
        return x.declared_annual_revenue / x.annual_revenue

    inflaters = _group(world, 'inflater')
    assert all(1.4 < ratio(x) < 2.1 for x in inflaters)
    honest = [x for x in world.companies if x.archetype != 'inflater']
    assert 0.97 < statistics.median(map(ratio, honest)) < 1.03
    # Some honest clients miss by more than 30% too, so the SPEC §7 flag is not proof of fraud.
    assert 0.01 < sum(not 0.7 <= ratio(x) <= 1.3 for x in honest) / len(honest) < 0.08
    # A weaker business overstates more often.
    assert statistics.mean(x.q for x in inflaters) < statistics.mean(x.q for x in _group(world, 'regular')) - 0.2


def test_main_bank_elsewhere(world):
    group = [x for x in _group(world, 'main_bank_elsewhere') if len(x.accounts.months) >= 12]
    # Revenue coverage under 70% of the real revenue: PARTIAL at best (SPEC §5.1).
    assert all(_receipts(_last12(x)) / x.annual_revenue < 0.7 for x in group)
    regular = [x for x in _group(world, 'regular') if len(x.accounts.months) >= 12]
    assert statistics.median(_receipts(_last12(x)) / x.annual_revenue for x in regular) > 0.85

    def share(companies):
        return sum(x.archetype == 'main_bank_elsewhere' for x in companies) / len(companies)

    # A company with a credit line at another bank more often banks there.
    with_line = [x for x in world.companies if _line_elsewhere(x)]
    assert share(with_line) > 3 * share([x for x in world.companies if not _line_elsewhere(x)])


def test_new_account(world):
    for x in _group(world, 'new_account'):
        assert (AS_OF - x.egrul.registered_at).days >= 400 and not x.loans
        assert AS_OF - timedelta(days=330) <= x.accounts.account_opened_at <= AS_OF - timedelta(days=20)
        assert len(x.accounts.months) < 12, 'under 12 months of history: PARTIAL at best (SPEC §5.1)'


def test_own_transfers(world):
    group = _group(world, 'own_transfers')
    shares = [s for s in map(_operating_share, group) if s is not None]
    # Operating inflows under 80%: PARTIAL at best (SPEC §5.1).
    assert sum(s < 0.8 for s in shares) / len(shares) > 0.95
    regular = [s for s in map(_operating_share, _group(world, 'regular')) if s is not None]
    assert statistics.median(regular) > 0.95

    def owner_money(x):  # over the last 4 months, all of them inflated
        months = x.accounts.months[-4:]
        came = sum(m.inflows.own_transfers + m.inflows.founder_contributions for m in months)
        return came / _receipts(months), sum(m.outflows.own_transfers for m in months) / came

    came, left = zip(*map(owner_money, group))
    assert min(came) > 0.25
    assert statistics.median(left) > 0.9, 'the money leaves again'
    founder_loans = [x for x in group if any(m.inflows.founder_contributions for m in x.accounts.months[-4:])]
    assert founder_loans and all(x.legal_form == c.LegalForm.OOO for x in founder_loans)
