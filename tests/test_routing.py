"""T18 routing by the authority matrix (SPEC §11): auto, underwriter or committee, with every reason."""
import pytest

from apps.api.contracts import DataQualityTier, Grade, Reason, Route, SourceId
from apps.api.limits import cash_flow_from_responses
from apps.api.offers import generate_offers
from apps.api.routing import (CLIENT_AUTO, CLIENT_COMMITTEE, CLIENT_UNDERWRITER, RoutingPolicy, route_application,
                              route_for)
from test_limits import quality, responses
from test_offers import AS_OF, decision, request
from test_stop_factors import result

FULL, PARTIAL, THIN = DataQualityTier.FULL, DataQualityTier.PARTIAL, DataQualityTier.THIN
FLAG = Reason(code='fssp_proceedings', staff_text='Есть действующие исполнительные производства.',
              client_text='Есть действующие исполнительные производства.')


def routed(amount, grade=Grade.B, tier=FULL, flags=(), **kw):
    return route_application(amount, grade=grade, flags=flags, data_quality=quality(tier=tier), **kw)


def codes(routing):
    return [r.code for r in routing.reasons]


# --- The Decision fixtures ------------------------------------------------------------

def test_fixture_offered_is_auto():
    d = decision('decision_offered.json')
    sf = result()
    r = route_application(request().amount, grade=d.score.grade, flags=sf.flags, data_quality=d.data_quality)
    assert (r.route, list(r.reasons)) == (d.route, d.route_reasons)


def test_fixture_in_review_goes_to_the_underwriter_for_its_flag():
    d = decision('decision_in_review.json')
    sf = result(declared_annual_revenue=request().declared_annual_revenue * 2)
    assert [f.code for f in sf.flags] == [f.code for f in d.flags] == ['declared_revenue_mismatch']
    r = route_application(request().amount, grade=d.score.grade, flags=sf.flags, data_quality=d.data_quality)
    assert r.route == d.route == Route.UNDERWRITER
    assert codes(r) == ['route_flag_declared_revenue_mismatch']
    assert r.reasons[0].staff_text == f'Флаг: {sf.flags[0].staff_text}'
    assert r.reasons[0].client_text == CLIENT_UNDERWRITER


def test_fixture_declined_stop_factor_is_an_automatic_decline():
    d = decision('decision_declined.json')
    r = route_application(500_000, grade=None, data_quality=d.data_quality, stop_factors=d.stop_factors)
    assert (r.route, list(r.reasons)) == (d.route, d.route_reasons)


# --- The matrix -------------------------------------------------------------------------

@pytest.mark.parametrize('amount, grade, tier, route, expected', [
    (5_000_000, Grade.A, FULL, Route.AUTO, ['route_auto']),
    (5_000_001, Grade.A, FULL, Route.UNDERWRITER, ['route_amount_over_5m']),
    (15_000_000, Grade.C, FULL, Route.UNDERWRITER, ['route_amount_over_5m']),
    (15_000_001, Grade.A, FULL, Route.COMMITTEE, ['route_amount_over_15m']),
    (1_000_000, Grade.C, PARTIAL, Route.AUTO, ['route_auto']),
    (1_000_001, Grade.C, PARTIAL, Route.UNDERWRITER, ['route_data_partial_over_1m']),
    (100_000, Grade.A, THIN, Route.UNDERWRITER, ['route_data_thin']),
    (5_000_000, Grade.D, FULL, Route.UNDERWRITER, ['route_grade']),
    (5_000_001, Grade.D, FULL, Route.COMMITTEE, ['route_amount_over_5m', 'route_grade_over_5m']),
    (300_000, Grade.E, FULL, Route.UNDERWRITER, ['route_grade']),  # E is routed like D
    (6_000_000, Grade.E, FULL, Route.COMMITTEE, ['route_amount_over_5m', 'route_grade_over_5m']),
])
def test_matrix(amount, grade, tier, route, expected):
    r = routed(amount, grade, tier)
    assert r.route == route and codes(r) == expected
    assert len(r.routes) == len(r.reasons) and r.route == max(r.routes, key=list(Route).index)


def test_auto_reason_names_the_bound_of_its_tier():
    assert routed(900_000, tier=PARTIAL).reasons[0].staff_text == 'Грейд B, флагов нет, данные PARTIAL, сумма до 1 млн ₽'


def test_every_condition_gives_a_reason_and_the_strictest_route_wins():
    flags = (FLAG, Reason(code='transit_operations', staff_text='Выявлены признаки транзитных операций.',
                          client_text='Выявлены признаки транзитных операций.'))
    r = routed(20_000_000, Grade.D, PARTIAL, flags)
    assert r.route == Route.COMMITTEE
    assert codes(r) == ['route_amount_over_15m', 'route_grade_over_5m', 'route_flag_fssp_proceedings',
                        'route_flag_transit_operations', 'route_data_partial_over_1m']
    assert r.routes == (Route.COMMITTEE, Route.COMMITTEE, Route.UNDERWRITER, Route.UNDERWRITER, Route.UNDERWRITER)


def test_flag_reasons_keep_the_code_and_staff_text_but_tell_the_client_only_about_the_review():
    r = routed(500_000, flags=(FLAG,))
    assert r.route == Route.UNDERWRITER
    assert r.reasons[0] == Reason(code='route_flag_fssp_proceedings', staff_text=f'Флаг: {FLAG.staff_text}',
                                  client_text=CLIENT_UNDERWRITER)


def test_data_quality_reasons_show_the_tier_message_to_the_client():
    dq = quality('data_quality_thin.json')
    r = route_application(300_000, grade=Grade.B, data_quality=dq)
    assert codes(r) == ['route_data_thin'] and r.reasons[0].client_text == dq.client_message
    assert routed(2_000_000, tier=PARTIAL).reasons[0].client_text == quality(tier=PARTIAL).client_message


def test_committee_reasons_tell_the_client_about_the_committee():
    assert {r.client_text for r in routed(20_000_000).reasons} == {CLIENT_COMMITTEE}


# --- Before scoring -------------------------------------------------------------------

def test_stop_factor_wins_over_everything():
    stop = Reason(code='bki_overdue_90_24m', staff_text='x', client_text='x')
    r = routed(20_000_000, Grade.E, THIN, (FLAG,), stop_factors=(stop,), unavailable_sources=(SourceId.BKI,))
    assert r.route == Route.AUTO and codes(r) == ['route_stop_factor'] and r.reasons[0].client_text == CLIENT_AUTO


def test_required_source_unavailable_goes_to_the_underwriter():
    r = route_application(500_000, grade=None, data_quality=None, unavailable_sources=[SourceId.BKI, SourceId.AML])
    assert r.route == Route.UNDERWRITER and codes(r) == ['route_source_unavailable']
    assert r.reasons[0].staff_text.endswith('bki, aml')


def test_no_score_or_no_data_quality_goes_to_the_underwriter():
    assert codes(routed(500_000, grade=None)) == ['route_score_unavailable']
    r = route_application(500_000, grade=Grade.A, data_quality=None)
    assert r.route == Route.UNDERWRITER and codes(r) == ['route_data_quality_missing']


def test_policy_can_be_passed_in():
    strict = RoutingPolicy(auto_max_full=1_000_000)
    assert routed(2_000_000).route == Route.AUTO
    assert routed(2_000_000, policy=strict).route == Route.UNDERWRITER


# --- route_for → pricing ---------------------------------------------------------------

def test_route_for_matches_route_application():
    f = route_for(Grade.B, (), quality(tier=PARTIAL))
    assert [f(a) for a in (1_000_000, 1_000_001, 15_000_001)] == [Route.AUTO, Route.UNDERWRITER, Route.COMMITTEE]


def test_offers_are_priced_on_their_own_route():
    """PARTIAL data: 1M is still automatic, the longer-term offer of more than 1M would be the underwriter's."""
    d = decision('decision_offered.json')
    cf = cash_flow_from_responses(responses(), quality())
    s = generate_offers(request(amount=1_200_000), cf, d.score, route_for(d.score.grade, (), quality(tier=PARTIAL)),
                        AS_OF)
    assert s.offers
    for o in s.offers:
        expected = Route.AUTO if o.amount <= 1_000_000 else Route.UNDERWRITER
        assert s.routes[o.offer_id] == expected
        assert o.price.processing_cost == {Route.AUTO: 3_000, Route.UNDERWRITER: 25_000}[expected]
