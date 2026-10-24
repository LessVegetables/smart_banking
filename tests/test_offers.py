"""T17 offer set and counterfactuals (SPEC §10): variants in SPEC order, each priced by `quote` at its own route."""
import dataclasses
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from apps.api.contracts import (ApplicationRequest, CollateralType, Decision, EquipmentInfo, Grade, LegalForm, Offer,
                                ProductId, Purpose, Route)
from apps.api.features import SourcePayloads, data_quality, extract_features
from apps.api.limits import cash_flow, cash_flow_from_responses, quote
from apps.api.offers import MAX_OFFERS, OFFER_VALID_DAYS, REAPPLY_AFTER_DAYS, generate_offers, standard_collateral
from apps.api.scoring import DEFAULT_CHAMPION, load_scorecard_model
from apps.world import generate_world
from test_limits import flow, quality, responses, score

FIXTURES = Path(__file__).parent / 'fixtures'
AS_OF = date(2026, 10, 24)
GUARANTEE, PLEDGE, MSP = CollateralType.OWNER_GUARANTEE, CollateralType.EQUIPMENT_PLEDGE, CollateralType.MSP_GUARANTEE
WC = ProductId.WORKING_CAPITAL


def request(**changes) -> ApplicationRequest:
    raw = json.loads((FIXTURES / 'application_request.json').read_text('utf-8'))
    return ApplicationRequest.model_validate(raw).model_copy(update=changes)


def decision(name: str) -> Decision:
    return Decision.model_validate_json((FIXTURES / name).read_text('utf-8'))


def always(route: Route):
    return lambda amount: route


def kinds(offer_set):
    return [o.kind for o in offer_set.offers]


EQUIPMENT = EquipmentInfo(description='Печь', price=2_000_000, down_payment=400_000, supplier_inn='5406000002')


# --- The Decision fixtures ------------------------------------------------------------

@pytest.mark.parametrize('name, route', [('decision_offered.json', Route.AUTO),
                                         ('decision_in_review.json', Route.UNDERWRITER)])
def test_fixture_offers(name, route):
    d = decision(name)
    cf = cash_flow_from_responses(responses(), quality())
    s = generate_offers(request(application_id=d.application_id), cf, d.score, always(route), AS_OF)
    assert s.offers == d.offers  # 1M over 17 (18) months, or 730k (710k) over the requested 12
    assert s.reasons == d.reasons
    assert s.matches_request is False and s.reapply_after is None
    assert set(s.routes.values()) == {route}


def test_without_the_current_loan():
    """The fixture's hint, re-run through `quote`: 900k at its own rate (the fixture rounds to 890k)."""
    d = decision('decision_offered.json')
    cf = cash_flow_from_responses(responses(), quality())
    [hint] = generate_offers(request(), cf, d.score, always(Route.AUTO), AS_OF).counterfactuals
    expected = quote(dataclasses.replace(cf, current_payments=0), d.score, WC, 12, Route.AUTO, [GUARANTEE],
                     requested=1_000_000)
    assert (hint.lever, hint.current_value, hint.required_value) == ('monthly_debt_payments', 15_270, 0)
    assert hint.resulting_amount == expected.amount == 900_000
    assert hint.resulting_rate == expected.price.rate
    assert '15\xa0270 ₽' in hint.client_text and '900\xa0000 ₽' in hint.client_text


# --- Variants -------------------------------------------------------------------------

def test_as_requested():
    s = generate_offers(request(amount=500_000), flow(), score(), always(Route.AUTO), AS_OF)
    [offer] = s.offers
    assert offer.kind == 'as_requested' and offer.matches_request and s.matches_request
    assert (offer.amount, offer.term_months, offer.collateral) == (500_000, 12, [GUARANTEE])
    assert offer.tradeoff == 'Как запрошено' and offer.valid_until == AS_OF + timedelta(days=OFFER_VALID_DAYS)
    assert s.reasons == [] and s.counterfactuals == []


def test_payment_load_hint():
    """SPEC §10's «при платёжной нагрузке до …»: the load at which the requested amount fits."""
    cf = flow()  # CFADS 105k, grade C: 70k a month for all loans, 20k already taken
    s = generate_offers(request(amount=600_000), cf, score(0.04, Grade.C), always(Route.AUTO), AS_OF)
    assert kinds(s) == ['longer_term', 'max_amount']
    [hint] = s.counterfactuals
    assert hint.lever == 'monthly_debt_payments' and 0 < hint.required_value < 20_000
    assert hint.resulting_amount == 600_000
    q = quote(dataclasses.replace(cf, current_payments=hint.required_value), score(0.04, Grade.C), WC, 12,
              Route.AUTO, [GUARANTEE], requested=600_000)
    assert q.amount == 600_000


def test_leverage_hint():
    cf = flow(annual_revenue=1_500_000, current_debt=300_000)  # leverage room 450k
    s = generate_offers(request(amount=600_000), cf, score(0.01, Grade.A), always(Route.AUTO), AS_OF)
    assert s.reasons[0].code == 'limit_leverage'
    [hint] = s.counterfactuals
    assert (hint.lever, hint.required_value, hint.resulting_amount) == ('current_debt', 150_000, 600_000)
    assert kinds(s) == ['max_amount']  # a longer term doesn't help against leverage


def test_msp_guarantee_brings_the_rate_under_the_cap():
    s = generate_offers(request(amount=300_000), flow(), score(0.25, Grade.D), always(Route.AUTO), AS_OF)
    [offer] = s.offers
    assert offer.kind == 'with_collateral' and offer.collateral == [GUARANTEE, MSP]
    assert offer.guarantee_fee == 0.01 and offer.rate + offer.guarantee_fee <= 0.30
    assert not offer.matches_request and s.matches_request is False
    assert [r.code for r in s.reasons] == ['rate_above_cap']


def test_msp_guarantee_is_not_offered_when_it_unlocks_nothing():
    s = generate_offers(request(amount=500_000), flow(), score(), always(Route.AUTO), AS_OF)
    assert 'with_collateral' not in kinds(s)


def test_decline_over_the_cap():
    s = generate_offers(request(amount=300_000), flow(), score(0.5, Grade.E), always(Route.AUTO), AS_OF)
    assert s.offers == [] and s.matches_request is None
    assert s.reapply_after == AS_OF + timedelta(days=REAPPLY_AFTER_DAYS)
    assert [r.code for r in s.reasons] == ['rate_above_cap']


def test_decline_below_the_product_minimum():
    s = generate_offers(request(), flow(cfads=10_000), score(), always(Route.AUTO), AS_OF)
    assert s.offers == [] and s.reapply_after is not None
    assert [r.code for r in s.reasons] == ['limit_cash_flow', 'limit_below_product_minimum']


def test_thin_without_revenue_gets_nothing():
    cf = flow(cfads=None, annual_revenue=None, revenue_basis=None)
    s = generate_offers(request(), cf, score(), always(Route.UNDERWRITER), AS_OF)
    assert s.offers == [] and s.counterfactuals == []
    assert s.reasons[0].code == 'limit_leverage'


def test_other_product_for_a_cash_gap():
    s = generate_offers(request(amount=300_000, purpose=Purpose.CASH_GAP), flow(), score(), always(Route.AUTO), AS_OF)
    assert kinds(s) == ['as_requested', 'other_product']
    overdraft = s.offers[1]
    assert overdraft.product == ProductId.OVERDRAFT and overdraft.collateral == [] and overdraft.term_months == 12
    assert '60 дней' in overdraft.tradeoff and not overdraft.matches_request


def test_credit_line_shows_the_unused_limit_fee():
    s = generate_offers(request(amount=500_000, preferred_product=ProductId.CREDIT_LINE, purpose=Purpose.SEASONAL_GAP),
                        flow(), score(), always(Route.AUTO), AS_OF)
    [offer] = s.offers
    assert offer.collateral == [] and offer.term_months == 12
    assert offer.kind == 'as_requested' and offer.matches_request
    assert 'неиспользованный лимит' in offer.tradeoff


def test_term_outside_the_product_is_clamped():
    s = generate_offers(request(amount=300_000, term_months=6, preferred_product=ProductId.OVERDRAFT,
                                purpose=Purpose.CASH_GAP), flow(), score(), always(Route.AUTO), AS_OF)
    [offer] = s.offers
    assert offer.term_months == 12 and offer.kind == 'as_requested' and not offer.matches_request


@pytest.mark.parametrize('form, collateral', [(LegalForm.OOO, [PLEDGE, GUARANTEE]), (LegalForm.IP, [PLEDGE])])
def test_investment_loan(form, collateral):
    s = generate_offers(request(amount=1_400_000, term_months=36, purpose=Purpose.EQUIPMENT,
                                preferred_product=ProductId.INVESTMENT, equipment=EQUIPMENT, legal_form=form),
                        flow(), score(), always(Route.AUTO), AS_OF)
    offer = s.offers[0]
    assert offer.collateral == collateral and offer.disbursement == 'supplier'
    assert offer.price.lgd == 0.35


def test_down_payment_hint():
    equipment = EQUIPMENT.model_copy(update={'down_payment': 100_000})  # 5% of the price
    s = generate_offers(request(amount=1_400_000, term_months=36, purpose=Purpose.EQUIPMENT,
                                preferred_product=ProductId.INVESTMENT, equipment=equipment),
                        flow(), score(), always(Route.AUTO), AS_OF)
    assert s.offers == []
    [hint] = s.counterfactuals
    assert (hint.lever, hint.required_value, hint.resulting_amount) == ('down_payment', 300_000, 1_400_000)


def test_investment_needs_the_equipment():
    with pytest.raises(ValueError):
        generate_offers(request(purpose=Purpose.EQUIPMENT, preferred_product=ProductId.INVESTMENT), flow(), score(),
                        always(Route.AUTO), AS_OF)


def test_ip_working_capital_is_unsecured():
    assert standard_collateral(WC, LegalForm.IP) == []
    s = generate_offers(request(amount=500_000, legal_form=LegalForm.IP), flow(), score(), always(Route.AUTO), AS_OF)
    assert s.offers[0].collateral == [] and s.offers[0].price.lgd == 0.65


def test_each_offer_is_priced_on_the_route_of_its_amount():
    """Above 800k the underwriter: the longer-term 1M and the 12-month max get different processing costs."""
    d = decision('decision_offered.json')
    cf = cash_flow_from_responses(responses(), quality())
    s = generate_offers(request(), cf, d.score, lambda a: Route.UNDERWRITER if a > 800_000 else Route.AUTO, AS_OF)
    longer, best = s.offers
    assert s.routes[longer.offer_id] == Route.UNDERWRITER and longer.price.processing_cost == 25_000
    assert s.routes[best.offer_id] == Route.AUTO and best.price.processing_cost == 3_000


# --- The world ------------------------------------------------------------------------

@pytest.fixture(scope='module')
def world_inputs():
    world = generate_world(seed=11, n=150, as_of=date(2026, 10, 1))
    model = load_scorecard_model(DEFAULT_CHAMPION)
    out = []
    for c in world.companies:
        p = SourcePayloads.from_company(c)
        out.append((p.egrul.legal_form, cash_flow(p, data_quality(p)), model.predict(extract_features(p, world.meta.as_of))))
    return out


@pytest.mark.parametrize('amount, term, purpose', [(300_000, 12, Purpose.INVENTORY), (1_000_000, 6, Purpose.CASH_GAP),
                                                   (2_000_000, 24, Purpose.CONTRACT)])
def test_world_offer_sets_are_consistent(world_inputs, amount, term, purpose):
    route_for = lambda a: Route.AUTO if a <= 1_000_000 else Route.UNDERWRITER  # noqa: E731
    offered = 0
    for form, cf, sc in world_inputs:
        s = generate_offers(request(amount=amount, term_months=term, purpose=purpose, legal_form=form,
                                    preferred_product=WC), cf, sc, route_for, AS_OF)
        assert len(s.offers) <= MAX_OFFERS and len({o.offer_id for o in s.offers}) == len(s.offers)
        assert (s.reapply_after is None) == bool(s.offers)
        for o in s.offers:
            Offer.model_validate(o.model_dump())
            assert o.rate + o.guarantee_fee <= 0.30 and o.amount >= 100_000
            assert o.price.processing_cost == {Route.AUTO: 3_000, Route.UNDERWRITER: 25_000}[route_for(o.amount)]
            assert o.matches_request == (o.amount == amount and o.term_months == term and o.kind == 'as_requested')
        offered += bool(s.offers)
    assert offered > 0
