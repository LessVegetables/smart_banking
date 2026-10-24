import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from apps.api import contracts as c

FIXTURES = Path(__file__).parent / 'fixtures'
SOURCE = c.SOURCE_RESPONSE
FACTS = TypeAdapter(list[c.Fact])

MODELS = {
    'application_request.json': c.ApplicationRequest,
    'data_quality_full.json': c.DataQuality,
    'data_quality_thin.json': c.DataQuality,
    'decision_declined.json': c.Decision,
    'decision_in_review.json': c.Decision,
    'decision_offered.json': c.Decision,
    'facts.json': FACTS,
    'features.json': c.Features,
    'scorecard_example.json': c.Scorecard,
    **{f'sources/{p.name}': SOURCE for p in (FIXTURES / 'sources').glob('*.json')},
}


def raw(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def load(name):
    model = MODELS[name]
    return model.validate_python(raw(name)) if isinstance(model, TypeAdapter) else model.model_validate(raw(name))


def dump(name, value):
    model = MODELS[name]
    return model.dump_python(value, mode='json') if isinstance(model, TypeAdapter) else value.model_dump(mode='json')


def test_every_fixture_has_a_model():
    files = {p.relative_to(FIXTURES).as_posix() for p in FIXTURES.rglob('*.json')}
    assert files == set(MODELS)


@pytest.mark.parametrize('name', sorted(MODELS))
def test_fixture_is_canonical(name):
    # A fixture equals its own validated dump: no unknown fields, no silently added defaults.
    assert dump(name, load(name)) == raw(name)


def test_source_fixtures_cover_every_source():
    assert {load(f).source for f in MODELS if f.startswith('sources/')} == set(c.SourceId) - {c.SourceId.STATEMENT_UPLOAD}


def test_main_persona_is_consistent():
    request = load('application_request.json')
    persona_sources = [f for f in MODELS if f.startswith('sources/') and not f.endswith('_ip.json')]
    assert {load(f).inn for f in persona_sources} == {request.inn}
    egrul = load('sources/egrul.json').payload
    assert egrul.legal_form == request.legal_form
    assert {x.subject_inn for x in request.consents} == {request.inn, egrul.director.inn}

    fns = load('sources/fns.json').payload
    bki = load('sources/bki.json').payload
    facts = {f.name: f for f in load('facts.json')}
    assert facts['annual_revenue'].verified_value == fns.financials.revenue
    assert facts['annual_revenue'].declared_value == request.declared_annual_revenue
    company = next(r for r in bki.reports if r.subject == 'company')
    assert facts['monthly_debt_payments'].verified_value == sum(x.monthly_payment for x in company.loans)


def test_full_data_quality_matches_accounts():
    accounts = load('sources/accounts.json').payload
    revenue = load('sources/fns.json').payload.financials.revenue
    dq = load('data_quality_full.json')
    last12 = accounts.months[-12:]
    customers = sum(m.inflows.customers for m in last12)
    assert dq.history_months.value == len(accounts.months)
    assert dq.regularity_months.value == sum(m.inflows.customers > 0 for m in last12)
    assert dq.revenue_coverage.value == pytest.approx(customers / revenue, abs=1e-3)
    assert dq.operating_inflow_share.value == pytest.approx(customers / sum(m.inflows.total for m in last12), abs=1e-3)
    assert load('decision_offered.json').data_quality == dq
    assert load('decision_declined.json').data_quality == load('data_quality_thin.json')


def test_score_follows_the_scorecard():
    scorecard = load('scorecard_example.json')
    features = load('features.json')
    score = load('decision_offered.json').score
    assert score.model_version == scorecard.version
    assert [x.feature for x in score.contributions] == [f.name for f in scorecard.features]
    for f, contribution in zip(scorecard.features, score.contributions):
        assert contribution.value == getattr(features, f.name)
        assert contribution.points == next(b.points for b in f.bins if b.label == contribution.bin_label)
    assert score.points == scorecard.intercept_points + sum(x.points for x in score.contributions)
    assert score.pd == pytest.approx(scorecard.pd_from_points(score.points), abs=1e-4)
    assert score.grade == scorecard.grade_for_pd(score.pd)


def test_scaling_matches_spec():
    scorecard = load('scorecard_example.json')
    assert scorecard.pd_from_points(600) == pytest.approx(1 / 51)
    assert (1 - scorecard.pd_from_points(620)) / scorecard.pd_from_points(620) == pytest.approx(100)


@pytest.mark.parametrize('name', ['decision_offered.json', 'decision_in_review.json'])
def test_offers_respect_the_request(name):
    request = load('application_request.json')
    for offer in load(name).offers:
        requested = next(x for x in offer.limit.constraints if x.name == 'requested')
        assert requested.value == request.amount
        assert offer.matches_request == (offer.amount == request.amount and offer.term_months == request.term_months)
        assert offer.price.pd == load(name).score.pd


def test_trust_levels_are_ordered():
    assert [t.rank for t in c.TrustLevel] == [0, 1, 2, 3]
    assert c.TrustLevel.BANK.rank < c.TrustLevel.DECLARED.rank


def test_fact_discrepancy_is_filled_in():
    fact = next(f for f in load('facts.json') if f.name == 'annual_revenue')
    assert fact.discrepancy == pytest.approx(abs(fact.declared_value - fact.verified_value) / fact.verified_value)
    assert fact.value == fact.verified_value


def broken(name, edit):
    data = deepcopy(raw(name))
    edit(data)
    with pytest.raises(ValidationError):
        MODELS[name].validate_python(data) if isinstance(MODELS[name], TypeAdapter) else MODELS[name].model_validate(data)


def test_unknown_fields_are_rejected():
    broken('features.json', lambda d: d.update(requested_amount=1))


def test_missing_feature_is_rejected_null_is_allowed():
    broken('features.json', lambda d: d.pop('margin'))
    c.Features.model_validate({**raw('features.json'), 'margin': None})


def test_data_quality_tier_is_the_worst_metric():
    broken('data_quality_thin.json', lambda d: d.update(tier='PARTIAL'))


def test_source_payload_matches_status():
    broken('sources/fssp.json', lambda d: d.update(status='timeout', error='x'))
    broken('sources/fssp_timeout.json', lambda d: d.update(error=None))


def test_source_payload_matches_source():
    broken('sources/fssp.json', lambda d: d.update(source='aml'))


def test_limit_is_the_binding_minimum():
    broken('decision_offered.json', lambda d: d['offers'][1]['limit'].update(binding='requested'))
    broken('decision_offered.json', lambda d: d['offers'][1]['limit'].update(limit=1))


def test_rate_is_the_sum_of_components():
    broken('decision_offered.json', lambda d: d['offers'][0]['price'].update(margin=0.05))


def test_decision_rules():
    broken('decision_offered.json', lambda d: d.update(offers=[]))
    broken('decision_offered.json', lambda d: d.update(status='disbursed'))
    broken('decision_offered.json', lambda d: d.update(matches_request=True))
    broken('decision_declined.json', lambda d: d.update(score=raw('decision_offered.json')['score']))


def test_scorecard_rules():
    broken('scorecard_example.json', lambda d: d['features'][0].update(name='requested_amount'))
    broken('scorecard_example.json', lambda d: d['features'][0]['bins'].pop())
    broken('scorecard_example.json', lambda d: d['grades'].reverse())


def test_contract_version_is_recorded():
    assert all(load(f).contract_version == c.CONTRACT_VERSION for f in MODELS if f.startswith('decision_'))
