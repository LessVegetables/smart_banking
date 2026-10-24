"""Shared feature library (T08): scorecard features, overdue rules, verified revenue."""
import json
from datetime import date
from pathlib import Path

import pytest

from apps.api import contracts as c
from apps.api.contracts import FEATURE_NAMES
from apps.api.features import (SourcePayloads, account_features, default_policy, extract_features,
                               features_from_responses, fns_revenue, load_policy, months_before, subject_history)
from apps.api.features.credit import credit_features
from apps.api.features.policy import CONFIG_PATH, FeaturePolicy
from apps.world import generate_world

FIXTURES = Path(__file__).parent / 'fixtures'
AS_OF = date(2026, 10, 1)
RULES = default_policy().overdue


def response(name):
    return c.SOURCE_RESPONSE.validate_python(json.loads((FIXTURES / 'sources' / f'{name}.json').read_text('utf-8')))


@pytest.fixture(scope='module')
def world():
    return generate_world(seed=7, n=1500, as_of=AS_OF)


@pytest.fixture(scope='module')
def features(world):
    return [(x, extract_features(SourcePayloads.from_company(x), AS_OF)) for x in world.companies]


# --- The fixture persona ------------------------------------------------------------

def test_source_fixtures_give_the_features_fixture():
    expected = c.Features.model_validate_json((FIXTURES / 'features.json').read_text('utf-8'))
    responses = [response(n) for n in ('accounts', 'egrul', 'fns', 'bki', 'client_card', 'aml', 'fssp_timeout')]
    assert features_from_responses(responses) == expected  # as of the day the sources answered, 2026-10-24


def test_failed_source_gives_no_data():
    responses = [response(n) for n in ('accounts', 'egrul', 'bki')]  # ФНС didn't answer
    f = features_from_responses(responses)
    assert f.margin is None and f.negative_equity is None and f.debt_to_revenue is None
    assert f.inflow_cv_12m is not None and f.business_age_months == 36


def test_ip_fixture():
    f = features_from_responses([response('egrip_ip'), response('fns_ip')], as_of=date(2026, 10, 24))
    assert f.legal_form == c.LegalForm.IP
    assert f.okved_risk_group == 'medium'  # 96.02
    assert f.business_age_months is not None
    # ИП: no company report, no ГИР БО; no account or bureau answer here either.
    assert f.worst_delinquency_days_24m_company is None and f.margin is None and f.negative_equity is None
    assert f.inflow_cv_12m is None and f.worst_delinquency_days_24m_owner is None


def test_feature_set_version_comes_from_config():
    assert default_policy().version == json.loads(CONFIG_PATH.read_text('utf-8'))['version'] == 'features_v0'


# --- Overdue rules (SPEC §5) ----------------------------------------------------------

def loan(*delinquencies, payment=10_000, current_days=0, current_amount=0, closed=False):
    return c.BkiLoan(loan_id='L1', kind='loan', opened_at=date(2020, 1, 10),
                     closed_at=date(2026, 1, 10) if closed else None, amount=500_000,
                     balance=0 if closed else 100_000, monthly_payment=0 if closed else payment,
                     current_overdue_days=current_days, current_overdue_amount=current_amount,
                     delinquencies=list(delinquencies))


def late(months_ago, days=20, amount=10_000, payment=10_000):
    return c.Delinquency(started_at=months_before(AS_OF, months_ago), days=days, amount=amount, payment_due=payment)


def history(*loans, subject='owner'):
    report = c.BkiReport(subject=subject, subject_inn='540600000001', loans=list(loans), inquiries=[])
    return subject_history(report, AS_OF, RULES)


def test_technical_delay_is_ignored():
    assert history(loan(late(3, days=5))).worst_weighted_days == 0
    assert history(loan(late(3, days=6))).worst_weighted_days == 6


def test_small_amount_is_ignored():
    assert history(loan(late(3, amount=999))).worst_weighted_days == 0
    assert history(loan(late(3, amount=1000))).worst_weighted_days == 20
    # under 1% of a big payment
    assert history(loan(late(3, amount=1999, payment=200_000))).worst_weighted_days == 0
    assert history(loan(late(3, amount=2000, payment=200_000))).worst_weighted_days == 20


def test_old_episodes_weigh_less_then_not_at_all():
    assert history(loan(late(24, days=100))).worst_weighted_days == 100
    h = history(loan(late(25, days=100)))
    assert h.worst_weighted_days == 50 and h.worst_days_24m == 0  # not a "90+ in 24 months" stop factor
    assert history(loan(late(36, days=100))).worst_weighted_days == 50
    assert history(loan(late(37, days=100))).episodes == ()


def test_worst_episode_wins():
    h = history(loan(late(30, days=120), late(10, days=40), late(2, days=3)))
    assert h.worst_weighted_days == 60 and h.worst_days_24m == 40
    assert [e.days for e in h.episodes] == [120, 40]


def test_current_overdue_follows_the_rules():
    assert history(loan(current_days=40, current_amount=10_000)).current_overdue_days == 40
    assert history(loan(current_days=4, current_amount=10_000)).current_overdue_days == 0
    assert history(loan(current_days=40, current_amount=500)).current_overdue_days == 0
    assert history(loan(current_days=40, current_amount=10_000)).worst_weighted_days == 40


def test_no_history_is_no_data_clean_history_is_zero():
    def owner_worst(*loans):
        bki = c.BkiPayload(reports=[c.BkiReport(subject='owner', subject_inn='540600000001', loans=list(loans),
                                                inquiries=[])])
        return credit_features(bki, AS_OF, default_policy(), is_ip=True).worst_delinquency_days_24m_owner

    assert owner_worst() is None
    assert owner_worst(loan(closed=True)) == 0


def test_world_overdue_rules_change_features(features):
    """The world has cases for every rule (T03); each one moves some company's feature."""
    def raw_worst(x):
        return max((d.days for r in x.bki.reports for ln in r.loans for d in ln.delinquencies
                    if months_before(AS_OF, 24) <= d.started_at), default=0)

    def worst(f):
        return max(f.worst_delinquency_days_24m_owner or 0, f.worst_delinquency_days_24m_company or 0)

    lowered = [(x, f) for x, f in features if worst(f) < raw_worst(x)]
    assert len(lowered) > 50
    histories = [subject_history(r, AS_OF, RULES) for x, _ in features for r in x.bki.reports]
    assert any(e.weight == 0.5 for h in histories for e in h.episodes)
    assert any(h.worst_days_24m >= 90 for h in histories)


# --- Accounts -------------------------------------------------------------------------

def test_short_account_history():
    accounts = response('accounts').payload
    assert account_features(accounts.model_copy(update={'months': accounts.months[-5:]})) == account_features(None)
    eight = account_features(accounts.model_copy(update={'months': accounts.months[-8:]}))
    assert eight.inflow_trend_12m is None  # needs a full year
    assert eight.tax_payment_months_12m == 12 and eight.months_without_inflows_12m == 0  # scaled to 12 months
    assert eight.inflow_cv_12m is not None


# --- Revenue --------------------------------------------------------------------------

def test_part_year_revenue_is_annualized():
    fns = response('fns').payload
    egrul = response('egrul').payload.model_copy(update={'registered_at': date(2025, 7, 1)})
    r = fns_revenue(fns, egrul)
    assert r.filed == 12_690_000 and r.active_share == round(184 / 365, 4)
    assert r.annualized == round(12_690_000 * 365 / 184) and r.estimate_12m == r.annualized and not r.rolled_forward


def test_revenue_rolls_forward_by_account_trend():
    r = fns_revenue(response('fns').payload, response('egrul').payload, response('accounts').payload)
    assert r.annualized == 12_690_000 and r.rolled_forward
    assert r.estimate_12m == round(12_690_000 * 12_881_800 / 12_440_900)  # last 12 months / inflows of 2025


def test_no_filing_no_revenue():
    fns = response('fns').payload.model_copy(update={'financials': None})
    assert fns_revenue(fns, response('egrul').payload) is None
    assert fns_revenue(None) is None


def test_honest_revenue_gap_shrinks(world):
    """The T04/T07 note: head-on, the >30% gap flags far too many honest clients."""
    head_on = estimated = n = 0
    for x in world.companies:
        r = fns_revenue(x.fns, x.egrul, x.accounts)
        if x.archetype != 'regular' or r is None or not r.filed:
            continue
        n += 1
        head_on += abs(x.declared_annual_revenue - r.filed) / r.filed > 0.3
        estimated += abs(x.declared_annual_revenue - r.estimate_12m) / r.estimate_12m > 0.3
    assert head_on / n > 0.25 and estimated / n < 0.12


# --- World ----------------------------------------------------------------------------

def test_world_features(features):
    for name in FEATURE_NAMES:
        assert any(getattr(f, name) is not None for _, f in features), name
    for x, f in features:
        if x.legal_form == 'ip':
            assert f.worst_delinquency_days_24m_company is None and f.margin is None and f.negative_equity is None
        if len(x.accounts.months) < 6:
            assert f.inflow_cv_12m is None
    assert {f.okved_risk_group for _, f in features} == {'low', 'medium', 'high'}


def test_features_carry_the_outcome(features):
    """Features see the hidden quality only through the data: weaker signals, more defaults."""
    def rate(selected):
        return sum(x.default_12m for x, _ in selected) / len(selected)

    volatile = [(x, f) for x, f in features if (f.inflow_cv_12m or 0) > 0.25]
    steady = [(x, f) for x, f in features if f.inflow_cv_12m is not None and f.inflow_cv_12m < 0.12]
    assert rate(volatile) > 2 * rate(steady)


def test_same_input_same_features(world):
    x = world.companies[0]
    payloads = SourcePayloads.from_company(x)
    assert extract_features(payloads, AS_OF) == extract_features(payloads, AS_OF)


# --- Policy ---------------------------------------------------------------------------

def test_okved_groups_match_by_longest_prefix():
    policy = default_policy().model_copy(update={'okved_risk_groups': {'high': ['41'], 'low': ['41.20']}})
    assert policy.okved_risk_group('41.20') == 'low'
    assert policy.okved_risk_group('41.10') == 'high'
    assert policy.okved_risk_group('410') == 'medium'  # not a dot-boundary prefix: the default
    assert default_policy().okved_risk_group('99.99') == 'medium'


def test_policy_validation():
    raw = json.loads(CONFIG_PATH.read_text('utf-8'))
    assert load_policy() == default_policy()
    raw['okved_risk_groups']['high'].append('10.71')
    with pytest.raises(ValueError):
        FeaturePolicy.model_validate(raw)


def test_months_before():
    assert months_before(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert months_before(date(2026, 1, 15), 1) == date(2025, 12, 15)
