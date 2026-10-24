"""T16 limit (SPEC §9.1): cash-flow inputs, the six constraints and `quote` (limit and rate together)."""
import dataclasses
import json
from datetime import date
from pathlib import Path

import pytest

from apps.api.contracts import (SOURCE_RESPONSE, CollateralType, DataQuality, DataQualityTier, Decision,
                                EquipmentInfo, Grade, ProductId, Route, ScoreResult)
from apps.api.credit_policy import CONFIG_PATH, load_policy
from apps.api.features import SourcePayloads, data_quality, extract_features
from apps.api.limits import CashFlow, cash_flow, cash_flow_from_responses, limit_breakdown, quote
from apps.api.scoring import DEFAULT_CHAMPION, load_scorecard_model
from apps.world import generate_world

FIXTURES = Path(__file__).parent / 'fixtures'
GUARANTEE, PLEDGE = CollateralType.OWNER_GUARANTEE, CollateralType.EQUIPMENT_PLEDGE
WC = ProductId.WORKING_CAPITAL


def responses():
    names = ('accounts', 'bki', 'client_card', 'egrul', 'fns')
    return [SOURCE_RESPONSE.validate_python(json.loads((FIXTURES / 'sources' / f'{n}.json').read_text('utf-8')))
            for n in names]


def payloads(**changes) -> SourcePayloads:
    return dataclasses.replace(SourcePayloads.from_responses(responses()), **changes)


def quality(name: str = 'data_quality_full.json', tier: DataQualityTier | None = None) -> DataQuality:
    raw = json.loads((FIXTURES / name).read_text('utf-8'))
    if tier is not None:
        raw['history_months']['tier'] = raw['tier'] = tier.value
    return DataQuality.model_validate(raw)


def decision(name: str) -> Decision:
    return Decision.model_validate_json((FIXTURES / name).read_text('utf-8'))


def score(pd: float = 0.0183, grade: Grade = Grade.B) -> ScoreResult:
    return ScoreResult(pd=pd, points=600.0, grade=grade, contributions=[], model_version='test_v0')


def flow(**changes) -> CashFlow:
    base = CashFlow(tier=DataQualityTier.FULL, months=12, median_operating_flow=150_000, cv=0.3, cfads=105_000,
                    average_monthly_inflows=833_000, current_payments=20_000, current_debt=300_000,
                    annual_revenue=10_000_000, revenue_basis='fns')
    return dataclasses.replace(base, **changes)


def constraint(limit, name):
    return next(c for c in limit.constraints if c.name == name)


# --- Inputs ---------------------------------------------------------------------------

def test_fixture_inputs():
    cf = cash_flow_from_responses(responses(), quality())
    assert (cf.median_operating_flow, cf.cv, cf.cfads) == (136_700, 0.204, 108_800)
    assert (cf.current_payments, cf.current_debt) == (15_270, 186_400)  # the owner's personal card is left out
    assert (cf.annual_revenue, cf.revenue_basis) == (12_690_000, 'fns')
    assert cf.months == 12


def test_thin_has_no_cfads():
    cf = cash_flow(payloads(), quality(tier=DataQualityTier.THIN))
    assert cf.cfads is None and cf.median_operating_flow == 136_700


def test_revenue_from_our_account_without_fns():
    cf = cash_flow(payloads(fns=None), quality())
    assert cf.revenue_basis == 'accounts'
    assert cf.annual_revenue == 12_881_800  # customer inflows of the last 12 months, as they are


def test_no_revenue_at_all():
    cf = cash_flow(payloads(fns=None, accounts=None), quality('data_quality_thin.json'))
    assert cf.annual_revenue is None and cf.cfads is None and cf.months == 0


def test_credit_bureau_is_required():
    with pytest.raises(ValueError):
        cash_flow(payloads(bki=None), quality())


# --- Quotes against the Decision fixtures ---------------------------------------------

@pytest.mark.parametrize('name, route', [('decision_offered.json', Route.AUTO),
                                         ('decision_in_review.json', Route.UNDERWRITER)])
def test_fixture_offers(name, route):
    d = decision(name)
    cf = cash_flow_from_responses(responses(), quality())
    for offer in d.offers:
        q = quote(cf, d.score, offer.product, offer.term_months, route, offer.collateral, requested=1_000_000)
        assert q.amount == offer.amount
        assert q.limit == offer.limit
        assert q.price == offer.price
        assert q.monthly_payment == offer.monthly_payment
        assert q.expected_profit == offer.expected_profit
        assert q.within_cap and not q.below_product_minimum and q.guarantee_fee == 0


def test_tie_names_the_request():
    cf = cash_flow_from_responses(responses(), quality())
    assert quote(cf, score(), WC, 17, Route.AUTO, [GUARANTEE], requested=1_000_000).limit.binding == 'requested'


# --- Constraints ----------------------------------------------------------------------

def test_spec_9_3_example():
    """Revenue 10M, flow 150k, payments 20k, grade C: about 530k for 12 months, 960k for 24 at 22.5%."""
    cf = flow()
    for term, expected in ((12, 530_000), (24, 950_000)):
        c = constraint(limit_breakdown(cf, Grade.C, WC, term, 0.225), 'cash_flow')
        assert c.value == expected  # PV 532 849 and 959 225, rounded down to 10 000
        assert c.inputs['p_max'] == 50_000


def test_dscr_by_grade():
    values = [constraint(limit_breakdown(flow(), g, WC, 12, 0.22), 'cash_flow').value for g in Grade]
    assert values == sorted(values, reverse=True) and values[0] > values[-1]


def test_no_room_for_payments():
    c = constraint(limit_breakdown(flow(cfads=20_000), Grade.A, WC, 12, 0.22), 'cash_flow')
    assert c.value == 0 and c.inputs['p_max'] < 0


def test_thin_limit_without_cash_flow():
    lim = limit_breakdown(flow(tier=DataQualityTier.THIN, cfads=None), Grade.B, WC, 12, 0.22, requested=8_000_000)
    c = constraint(lim, 'cash_flow')
    assert c.value is None and 'THIN' in c.note
    assert lim.binding == 'leverage' and lim.limit == 4_700_000


def test_partial_uses_the_actual_flow():
    cf = cash_flow(payloads(), quality(tier=DataQualityTier.PARTIAL))
    c = constraint(limit_breakdown(cf, Grade.B, WC, 12, 0.2075), 'cash_flow')
    assert c.value == 730_000 and 'PARTIAL' in c.note


def test_leverage():
    c = constraint(limit_breakdown(flow(), Grade.B, WC, 12, 0.22), 'leverage')
    assert c.value == 4_700_000 and c.inputs == {'annual_revenue': 10_000_000, 'multiplier': 0.5, 'current_debt': 300_000}
    inv = constraint(limit_breakdown(flow(), Grade.B, ProductId.INVESTMENT, 36, 0.22), 'leverage')
    assert inv.value == 6_700_000
    assert constraint(limit_breakdown(flow(current_debt=6_000_000), Grade.B, WC, 12, 0.22), 'leverage').value == 0


def test_no_verified_revenue_means_no_leverage_room():
    lim = limit_breakdown(flow(annual_revenue=None, revenue_basis=None), Grade.B, WC, 12, 0.22)
    assert constraint(lim, 'leverage').value == 0 and lim.limit == 0


def test_equipment_pledge():
    eq = EquipmentInfo(description='Печь', price=3_000_000, down_payment=600_000, supplier_inn='5406000002')
    c = constraint(limit_breakdown(flow(), Grade.B, ProductId.INVESTMENT, 36, 0.2, [PLEDGE], equipment=eq), 'collateral')
    assert c.value == 2_100_000  # 70% of the price, below price - down payment (2.4M)
    big_down = eq.model_copy(update={'down_payment': 1_500_000})
    assert constraint(limit_breakdown(flow(), Grade.B, ProductId.INVESTMENT, 36, 0.2, [PLEDGE], equipment=big_down),
                      'collateral').value == 1_500_000
    low_down = eq.model_copy(update={'down_payment': 300_000})
    c = constraint(limit_breakdown(flow(), Grade.B, ProductId.INVESTMENT, 36, 0.2, [PLEDGE], equipment=low_down),
                   'collateral')
    assert c.value == 0 and '15%' in c.note
    with pytest.raises(ValueError):
        limit_breakdown(flow(), Grade.B, ProductId.INVESTMENT, 36, 0.2, [PLEDGE])


def test_no_collateral_constraint_without_a_pledge():
    c = constraint(limit_breakdown(flow(), Grade.B, WC, 12, 0.2, [GUARANTEE]), 'collateral')
    assert c.value is None


def test_turnover_only_for_overdraft():
    assert constraint(limit_breakdown(flow(), Grade.B, WC, 12, 0.2), 'turnover').value is None
    od = limit_breakdown(flow(), Grade.B, ProductId.OVERDRAFT, 12, 0.2)
    assert constraint(od, 'turnover').value == 416_500
    assert 'возобновляемый' in constraint(od, 'cash_flow').note


def test_term_outside_the_product_range():
    with pytest.raises(ValueError):
        limit_breakdown(flow(), Grade.B, ProductId.OVERDRAFT, 6, 0.2)
    with pytest.raises(ValueError):
        quote(flow(), score(), WC, 36, Route.AUTO)


def test_dscr_comes_from_config(tmp_path):
    raw = json.loads(CONFIG_PATH.read_text('utf-8'))
    raw['limit']['dscr_min']['C'] = 2.5
    path = tmp_path / 'credit_policy.json'
    path.write_text(json.dumps(raw), 'utf-8')
    c = constraint(limit_breakdown(flow(), Grade.C, WC, 12, 0.225, policy=load_policy(path)), 'cash_flow')
    assert c.inputs['dscr_min'] == 2.5 and c.inputs['p_max'] == 22_000


# --- Quote ----------------------------------------------------------------------------

def test_without_request_the_limit_is_rounded_down():
    q = quote(flow(), score(0.04, Grade.C), WC, 24, Route.AUTO, [GUARANTEE])
    assert constraint(q.limit, 'requested').value is None
    assert q.amount == q.limit.limit and q.amount % 10_000 == 0
    assert constraint(q.limit, 'cash_flow').inputs['rate'] == q.price.rate


def test_nothing_to_lend():
    q = quote(flow(cfads=10_000), score(), WC, 12, Route.AUTO, requested=1_000_000)
    assert q.amount == 0 and q.price is None and q.limit.limit == 0 and q.below_product_minimum


def test_over_the_cap():
    q = quote(flow(), score(0.3, Grade.E), WC, 12, Route.AUTO, requested=500_000)
    assert q.price.rate > 0.30 and not q.within_cap


def test_small_limit_is_below_the_product_minimum():
    q = quote(flow(cfads=33_000), score(), WC, 12, Route.AUTO, requested=1_000_000)
    assert 0 < q.amount < 100_000 and q.below_product_minimum


# --- The world ------------------------------------------------------------------------

@pytest.fixture(scope='module')
def world_inputs():
    world = generate_world(seed=11, n=150, as_of=date(2026, 10, 1))
    model = load_scorecard_model(DEFAULT_CHAMPION)
    out = []
    for c in world.companies:
        p = SourcePayloads.from_company(c)
        out.append((cash_flow(p, data_quality(p)), model.predict(extract_features(p, world.meta.as_of))))
    return out


@pytest.mark.parametrize('product, term, requested', [(WC, 12, 1_000_000), (WC, 24, None),
                                                      (ProductId.OVERDRAFT, 12, 500_000),
                                                      (ProductId.CREDIT_LINE, 12, None)])
def test_world_quotes_are_consistent(world_inputs, product, term, requested):
    for cf, sc in world_inputs:
        q = quote(cf, sc, product, term, Route.AUTO, [GUARANTEE], requested=requested)
        assert q.amount <= q.limit.limit
        if q.price is None:
            assert q.amount == 0
            continue
        assert q.amount == requested or q.amount % 10_000 == 0
        cash = constraint(q.limit, 'cash_flow')
        if cash.value is not None:
            assert cash.inputs['rate'] == q.price.rate  # the limit was computed at the offer's own rate
        if cf.tier == DataQualityTier.THIN:
            assert cash.value is None
