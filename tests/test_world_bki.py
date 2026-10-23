"""Credit-bureau histories of the synthetic world (T03)."""
import hashlib
import random
import statistics
from datetime import date, timedelta

import pytest

from apps.api import contracts as c
from apps.world import generate_world
from apps.world.bki import draw_late_payments
from apps.world.loans import due_date, payments_by_month
from apps.world.months import month_index

AS_OF = date(2026, 10, 1)
SINCE_24M = AS_OF - timedelta(days=730)
REVOLVING = {'overdraft', 'credit_line', 'card'}


@pytest.fixture(scope='module')
def world():
    return generate_world(seed=7, n=3000, as_of=AS_OF)


def _products(x):
    return [loan for report in x.bki.reports for loan in report.loans]


def _records(x, since=date.min):
    return [d for loan in _products(x) for d in loan.delinquencies if d.started_at >= since]


def _worst_24m(x):
    return max((d.days for d in _records(x, SINCE_24M)), default=0)


def _current_overdue(x):
    return max((loan.current_overdue_days for loan in _products(x)), default=0)


def _inquiries(x, days):
    return max(sum(i.inquired_at >= AS_OF - timedelta(days=days) for i in r.inquiries) for r in x.bki.reports)


def _utilization(x):
    lines = [loan for loan in _products(x) if loan.kind in REVOLVING and loan.closed_at is None]
    return sum(loan.balance for loan in lines) / sum(loan.amount for loan in lines) if lines else None


def _unmoved(x):
    """The late payments of this company's loans moved no payment out of its month in the account."""
    paid = payments_by_month(x.loans)
    months = [month_index(date.fromisoformat(m.month + '-01')) for m in x.accounts.months]
    return all(paid[m] == sum(loan.monthly_payment for loan in x.loans if loan.pays_in(m)) for m in months)


def test_reports_match_contract(world):
    for x in world.companies:
        payload = c.BkiPayload.model_validate(x.bki.model_dump(mode='json'))
        assert payload == x.bki


def test_report_subjects(world):
    for x in world.companies:
        reports = x.bki.reports
        if x.legal_form == c.LegalForm.IP:
            assert [(r.subject, r.subject_inn) for r in reports] == [('owner', x.inn)]
        else:
            assert [(r.subject, r.subject_inn) for r in reports] == \
                [('company', x.inn), ('owner', x.egrul.founders[0].inn)]
        ids = [loan.loan_id for loan in _products(x)]
        assert len(set(ids)) == len(ids)
        for r in reports:
            assert r.loans == sorted(r.loans, key=lambda loan: (loan.opened_at, loan.loan_id))
            assert all(AS_OF - timedelta(days=365) <= i.inquired_at < AS_OF for i in r.inquiries)
            assert [i.inquired_at for i in r.inquiries] == sorted(i.inquired_at for i in r.inquiries)


def test_company_loans_are_in_the_bureau(world):
    for x in world.companies:
        business = x.bki.reports[0]  # the company report, or the only report of an ИП
        by_id = {loan.loan_id: loan for loan in business.loans}
        for loan in x.loans:
            assert loan.loan_id in by_id
            bki = by_id[loan.loan_id]
            assert (bki.kind, bki.opened_at, bki.amount) == (loan.kind, loan.opened_at, loan.amount)


def test_bureau_agrees_with_the_account(world):
    for x in world.companies:
        by_id = {loan.loan_id: loan for loan in _products(x)}
        for loan in x.loans:
            bki = by_id[loan.loan_id]
            due = [k for k in range(1, loan.term_months + 1) if loan.due_date(k) < AS_OF]
            paid = sum(amount for day, amount in loan.payments() if day < AS_OF)
            # Whatever fell due and wasn't paid through the account is overdue in the bureau.
            assert loan.monthly_payment * len(due) - paid == bki.current_overdue_amount
            unpaid = bki.current_overdue_amount // loan.monthly_payment
            if bki.closed_at:
                assert len(due) == loan.term_months and not bki.current_overdue_days
                assert bki.closed_at == max(day for day, _ in loan.payments()) < AS_OF
                assert bki.balance == bki.monthly_payment == 0
            else:
                assert bki.balance == loan.balance_after(len(due) - unpaid)
                assert bki.monthly_payment == loan.monthly_payment


def test_payments_follow_the_schedule_without_late_payments(world):
    for x in world.companies:
        for loan in x.loans:
            if not loan.late_payments:
                assert loan.payments() == [(loan.due_date(k), loan.monthly_payment)
                                           for k in range(1, loan.term_months + 1)]


def test_overdue_loans_stop_paying_through_the_account(world):
    overdue = [(x, loan) for x in world.companies for loan in x.loans
               if loan.late_payments and loan.late_payments[-1].repaid_at is None]
    assert len(overdue) > 20
    for x, loan in overdue:
        first_missed = loan.due_date(loan.late_payments[-1].payment)
        assert all(day < first_missed for day, _ in loan.payments())


def test_t02_accounts_unchanged_where_payments_did_not_move():
    # Hash of the T02 accounts (code on main) of the companies whose loan payments stay in their months.
    world = generate_world(seed=11, n=300, as_of=AS_OF)
    kept = [x for x in world.companies if _unmoved(x)]
    assert 0.8 * len(world.companies) < len(kept) < len(world.companies)
    dump = '\n'.join(x.accounts.model_dump_json() for x in kept)
    assert hashlib.sha256(dump.encode()).hexdigest() == \
        'acafbc7cefe89eb423b2e78ba7a658786fba2f04fb958eda53ed9d963498bbd9'


def test_delinquency_records(world):
    for x in world.companies:
        for loan in _products(x):
            for d in loan.delinquencies:
                assert loan.opened_at < d.started_at < AS_OF
                assert d.started_at + timedelta(days=d.days) <= (loan.closed_at or AS_OF)
                assert 0 < d.amount and 0 < d.payment_due
            starts = [d.started_at for d in loan.delinquencies]
            assert starts == sorted(starts)
            assert (loan.current_overdue_days > 0) == (loan.current_overdue_amount > 0)
            if loan.current_overdue_days:
                # The open episode is the last record, counted up to as_of.
                last = loan.delinquencies[-1]
                assert (last.days, last.amount) == (loan.current_overdue_days, loan.current_overdue_amount)
                assert last.started_at + timedelta(days=last.days) == AS_OF
            if loan.closed_at:
                assert loan.balance == loan.monthly_payment == loan.current_overdue_days == 0
            if loan.kind in REVOLVING:
                assert loan.balance <= loan.amount


@pytest.mark.parametrize('discipline', [-3.0, 0.0])
def test_late_payments_never_overlap(discipline):
    rng = random.Random(5)
    for _ in range(300):
        opened_at = date(2021, 1, 1) + timedelta(days=rng.randint(0, 1800))
        term = rng.choice((12, 36, None))
        still_open = rng.random() < 0.7
        late = draw_late_payments(rng, discipline, opened_at, term, 50_000, AS_OF, still_open)
        open_ = [x for x in late if x.repaid_at is None]
        assert len(open_) <= 1 and (not open_ or (still_open and late[-1] is open_[0]))
        for prev, nxt in zip(late, late[1:]):
            assert due_date(opened_at, nxt.payment) >= prev.repaid_at
        for x in late:
            assert x.repaid_at is None or due_date(opened_at, x.payment) < x.repaid_at < AS_OF
            assert term is None or x.payment <= term


def test_world_has_cases_for_every_overdue_rule(world):
    # SPEC §5: technical delays, small amounts and old episodes are in the data for T08 to discard or weigh.
    records = [d for x in world.companies for d in _records(x)]
    assert sum(d.days <= 5 for d in records) > 500
    assert sum(d.days > 5 and d.amount < 1000 for d in records) > 50
    assert sum(1000 <= d.amount < 0.01 * d.payment_due for d in records) > 0
    assert sum(AS_OF - timedelta(days=1095) <= d.started_at < SINCE_24M for d in records) > 100
    assert sum(d.started_at < AS_OF - timedelta(days=1095) for d in records) > 100
    # SPEC §7 stop-factors and flags, on the company and on the owner.
    for subject in ('company', 'owner'):
        reports = [r for x in world.companies for r in x.bki.reports if r.subject == subject]
        loans = [loan for r in reports for loan in r.loans]
        assert sum(any(d.days >= 90 and d.started_at >= SINCE_24M for d in loan.delinquencies) for loan in loans) > 20
        assert sum(loan.current_overdue_days > 30 for loan in loans) > 10
    assert sum(_inquiries(x, 30) >= 3 for x in world.companies) > 50


def test_quality_drives_credit_history(world):
    weak = [x for x in world.companies if x.q < -1]
    strong = [x for x in world.companies if x.q > 1]

    def share(pred, group):
        return sum(map(pred, group)) / len(group)

    assert share(lambda x: _worst_24m(x) >= 30, weak) > 5 * share(lambda x: _worst_24m(x) >= 30, strong)
    assert share(lambda x: _worst_24m(x) >= 90, weak) > 0.1
    assert share(lambda x: _current_overdue(x) > 30, weak) > 5 * share(lambda x: _current_overdue(x) > 30, strong)
    assert statistics.mean(_inquiries(x, 91) for x in weak) > 2 * statistics.mean(_inquiries(x, 91) for x in strong)
    weak_use = [u for u in map(_utilization, weak) if u is not None]
    strong_use = [u for u in map(_utilization, strong) if u is not None]
    assert statistics.mean(weak_use) > statistics.mean(strong_use) + 0.2
    # Overall rates stay plausible for applicants who already bank with us.
    everyone = world.companies
    assert 0.02 < share(lambda x: _worst_24m(x) >= 90, everyone) < 0.08
    assert 0.005 < share(lambda x: _current_overdue(x) > 30, everyone) < 0.04
    assert 0.02 < share(lambda x: _inquiries(x, 30) >= 3, everyone) < 0.1
