"""Account data quality and tier FULL / PARTIAL / THIN (T09, SPEC §5.1)."""
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api import contracts as c
from apps.api.contracts import DataQualityTier as Tier
from apps.api.features import SourcePayloads, data_quality, data_quality_from_responses
from apps.api.features.data_quality import CONFIG_PATH, DataQualityPolicy, default_policy, load_policy
from apps.world import generate_world

FIXTURES = Path(__file__).parent / 'fixtures'
AS_OF = date(2026, 10, 1)
POLICY = default_policy()


def response(name):
    return c.SOURCE_RESPONSE.validate_python(json.loads((FIXTURES / 'sources' / f'{name}.json').read_text('utf-8')))


ACCOUNTS = response('accounts').payload
FNS = response('fns').payload
EGRUL = response('egrul').payload
REVENUE = 12_690_000  # ФНС, a full year


def payloads(accounts=ACCOUNTS, fns=FNS, egrul=EGRUL):
    return SourcePayloads(egrul=egrul, fns=fns, accounts=accounts)


def last(n):
    return ACCOUNTS.model_copy(update={'months': ACCOUNTS.months[-n:]})


# --- The fixture persona ------------------------------------------------------------

def test_source_fixtures_give_the_full_fixture():
    expected = c.DataQuality.model_validate_json((FIXTURES / 'data_quality_full.json').read_text('utf-8'))
    responses = [response(n) for n in ('accounts', 'egrul', 'fns', 'bki', 'client_card', 'aml', 'fssp_timeout')]
    dq = data_quality_from_responses(responses, declared_revenue=12_500_000)
    assert dq.policy_version == POLICY.version
    assert dq.model_copy(update={'policy_version': expected.policy_version}) == expected


def test_short_history():
    eight = data_quality(payloads(last(8)))
    customers = sum(m.inflows.customers for m in ACCOUNTS.months[-8:])
    assert eight.history_months.value == 8 and eight.history_months.tier == Tier.PARTIAL
    assert eight.regularity_months.value == 8 and eight.regularity_months.tier == Tier.PARTIAL
    # Coverage is scaled to 12 months: the short history counts once, in the history metric.
    assert eight.revenue_coverage.value == round(customers * 12 / 8 / REVENUE, 3)
    assert eight.revenue_coverage.tier == Tier.FULL
    assert eight.tier == Tier.PARTIAL and 'счёт открыт недавно' in eight.client_message

    five = data_quality(payloads(last(5)))
    assert five.tier == Tier.THIN and five.history_months.tier == Tier.THIN
    assert five.client_message == ('Мы видим только часть оборотов вашего бизнеса: счёт открыт недавно. '
                                   'Чтобы рассчитать лимит, понадобятся выписки из других банков.')


def test_own_transfers_lower_the_operating_share():
    months = [m.model_copy(update={'inflows': m.inflows.model_copy(update={'own_transfers': m.inflows.customers // 2})})
              for m in ACCOUNTS.months]
    dq = data_quality(payloads(ACCOUNTS.model_copy(update={'months': months})))
    assert dq.operating_inflow_share.value == pytest.approx(0.667, abs=0.002)
    assert dq.operating_inflow_share.tier == Tier.PARTIAL and dq.revenue_coverage.tier == Tier.FULL
    assert dq.tier == Tier.PARTIAL and 'переводы со своих счетов' in dq.client_message


def test_low_coverage_means_main_bank_elsewhere():
    dq = data_quality(payloads())
    low = data_quality(payloads(fns=FNS.model_copy(update={
        'financials': FNS.financials.model_copy(update={'revenue': REVENUE * 3})})))
    assert dq.tier == Tier.FULL
    assert low.revenue_coverage.value == round(12_881_800 / (REVENUE * 3), 3)
    assert low.revenue_coverage.tier == Tier.PARTIAL and 'другие банки' in low.client_message


def test_part_year_revenue_is_annualized():
    egrul = EGRUL.model_copy(update={'registered_at': date(2025, 7, 1)})
    dq = data_quality(payloads(egrul=egrul))
    assert dq.revenue_coverage.value == round(12_881_800 / round(REVENUE * 365 / 184), 3)


def test_declared_revenue_when_fns_has_none():
    no_filing = FNS.model_copy(update={'financials': None})
    declared = data_quality(payloads(fns=no_filing), declared_revenue=12_500_000)
    assert declared.revenue_basis == 'declared'
    assert declared.revenue_coverage.value == round(12_881_800 / 12_500_000, 3)
    # ФНС didn't answer: the same fallback.
    assert data_quality(payloads(fns=None), declared_revenue=12_500_000) == declared

    nothing = data_quality(payloads(fns=no_filing))
    assert nothing.revenue_basis is None
    assert nothing.revenue_coverage.value is None and nothing.revenue_coverage.tier == Tier.THIN
    assert nothing.tier == Tier.THIN and 'нет данных о выручке' in nothing.client_message


def test_no_account_data():
    dq = data_quality(payloads(accounts=None), declared_revenue=12_500_000)
    assert all(getattr(dq, m).value is None and getattr(dq, m).tier == Tier.THIN
               for m in ('history_months', 'revenue_coverage', 'operating_inflow_share', 'regularity_months'))
    assert dq.revenue_basis == 'fns' and 'нет оборотов по вашему счёту' in dq.client_message


# --- Policy -----------------------------------------------------------------------------

@pytest.mark.parametrize('metric, full, partial', [
    ('history_months', 12, 6),
    ('revenue_coverage', 0.7, 0.3),
    ('operating_inflow_share', 0.8, 0.6),
    ('regularity_months', 10, 7),
])
def test_spec_thresholds(metric, full, partial):
    t = getattr(POLICY, metric)
    assert (t.full, t.partial) == (full, partial)
    step = 1 if metric.endswith('months') else 0.001
    assert t.tier(full) == Tier.FULL and t.tier(full - step) == Tier.PARTIAL
    assert t.tier(partial) == Tier.PARTIAL and t.tier(partial - step) == Tier.THIN
    assert t.tier(None) == Tier.THIN


def test_thresholds_come_from_config():
    raw = json.loads(CONFIG_PATH.read_text('utf-8'))
    assert load_policy().version == raw['version']
    strict = DataQualityPolicy.model_validate(raw | {'version': 'strict',
                                                     'revenue_coverage': {'full': 1.1, 'partial': 0.3}})
    dq = data_quality(payloads(), policy=strict)
    assert dq.revenue_coverage.tier == Tier.PARTIAL and dq.policy_version == 'strict'


def test_policy_validation():
    raw = json.loads(CONFIG_PATH.read_text('utf-8'))
    with pytest.raises(ValidationError):
        DataQualityPolicy.model_validate(raw | {'history_months': {'full': 6, 'partial': 12}})
    with pytest.raises(ValidationError):
        DataQualityPolicy.model_validate(raw | {'typo': 1})


# --- The world ----------------------------------------------------------------------------

@pytest.fixture(scope='module')
def tiers():
    world = generate_world(seed=7, n=1500, as_of=AS_OF)
    by_archetype = {}
    for x in world.companies:
        dq = data_quality(SourcePayloads.from_company(x), x.declared_annual_revenue)
        by_archetype.setdefault(x.archetype, Counter())[dq.tier] += 1
    return by_archetype


def test_world_tiers(tiers):
    regular = tiers['regular']
    n = sum(regular.values())
    # About 75% FULL, 18% PARTIAL, 7% THIN at seed 7 (apps/world/README.md).
    assert 0.68 < regular[Tier.FULL] / n < 0.80
    assert 0.13 < regular[Tier.PARTIAL] / n < 0.25
    assert 0.04 < regular[Tier.THIN] / n < 0.11
    # Incomplete-data clients (T04) are never FULL; main bank elsewhere almost never.
    assert tiers['new_account'][Tier.FULL] == 0 and tiers['own_transfers'][Tier.FULL] == 0
    elsewhere = tiers['main_bank_elsewhere']
    assert elsewhere[Tier.FULL] / sum(elsewhere.values()) < 0.1
