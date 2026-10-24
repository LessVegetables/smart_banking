"""T14 stop factors and review flags from source responses."""
import json
from datetime import date
from pathlib import Path

from apps.api.contracts import SOURCE_RESPONSE, ApplicationRequest, SourceCallStatus, SourceId
from apps.api.stop_factors import evaluate_stop_factors

FIXTURES = Path(__file__).parent / 'fixtures'
AS_OF = date(2026, 10, 24)


def fixture(name):
    return SOURCE_RESPONSE.validate_python(json.loads((FIXTURES / 'sources' / f'{name}.json').read_text('utf-8')))


def request(**changes):
    value = json.loads((FIXTURES / 'application_request.json').read_text('utf-8'))
    value.update(changes)
    return ApplicationRequest.model_validate(value)


def healthy_responses():
    return [fixture(name) for name in ('accounts', 'client_card', 'egrul', 'fns', 'bki', 'fssp', 'arbitration', 'aml')]


def result(responses=None, **request_changes):
    return evaluate_stop_factors(request(**request_changes), responses or healthy_responses(), AS_OF)


def test_healthy_fixture_has_no_stop_factors_or_flags():
    evaluated = result()
    assert evaluated.stop_factors == ()
    assert evaluated.flags == ()


def test_stop_factors_from_registry_tax_and_internal_stop_list():
    responses = healthy_responses()
    i = next(i for i, item in enumerate(responses) if item.source == SourceId.EGRUL)
    responses[i] = responses[i].model_copy(update={
        'payload': responses[i].payload.model_copy(update={'status': 'liquidating', 'okved_main': '92.10'})
    })
    i = next(i for i, item in enumerate(responses) if item.source == SourceId.FNS)
    responses[i] = responses[i].model_copy(update={
        'payload': responses[i].payload.model_copy(update={'accounts_suspended': True, 'director_disqualified': True})
    })
    i = next(i for i, item in enumerate(responses) if item.source == SourceId.CLIENT_CARD)
    responses[i] = responses[i].model_copy(update={
        'payload': responses[i].payload.model_copy(update={'internal_stop_list': True})
    })

    codes = {reason.code for reason in result(responses).stop_factors}
    assert {'company_liquidating', 'okved_prohibited', 'fns_accounts_suspended',
            'director_disqualified', 'internal_stop_list'} <= codes


def test_bki_stop_factors_use_cleaned_subject_history():
    responses = healthy_responses()
    i = next(i for i, item in enumerate(responses) if item.source == SourceId.BKI)
    bki = responses[i].payload
    company = bki.reports[0]
    loan = type(company.loans[0]).model_validate(company.loans[0].model_dump() | {
        'current_overdue_days': 31, 'current_overdue_amount': 2_000,
        'delinquencies': [dict(started_at='2025-06-01', days=90, amount=5_000, payment_due=10_000)],
    })
    bki = bki.model_copy(update={'reports': [company.model_copy(update={'loans': [loan]}), bki.reports[1]]})
    responses[i] = responses[i].model_copy(update={'payload': bki})

    codes = {reason.code for reason in result(responses).stop_factors}
    assert 'bki_current_overdue_30_company' in codes
    assert 'bki_overdue_90_24m' in codes


def test_aml_list_match_is_a_generic_client_stop_reason_and_transit_is_a_flag():
    responses = healthy_responses()
    i = next(i for i, item in enumerate(responses) if item.source == SourceId.AML)
    aml = responses[i].payload.model_copy(update={
        'list_matches': [{'list_name': 'restricted', 'subject': 'director', 'matched_name': 'Имя'}],
        'transit_operations': True,
    })
    responses[i] = responses[i].model_copy(update={'payload': aml})

    evaluated = result(responses)
    assert {reason.code for reason in evaluated.stop_factors} == {'aml_list_match'}
    assert evaluated.stop_factors[0].client_text == 'Заявку нельзя обработать автоматически.'
    assert {reason.code for reason in evaluated.flags} == {'transit_operations'}


def test_optional_source_failures_create_individual_flags():
    responses = healthy_responses()
    for source in (SourceId.FNS, SourceId.FSSP, SourceId.ARBITRATION):
        i = next(i for i, item in enumerate(responses) if item.source == source)
        responses[i] = responses[i].model_copy(update={
            'status': SourceCallStatus.ERROR, 'payload': None, 'received_at': None, 'error': 'unavailable'
        })

    flags = {reason.code for reason in result(responses).flags}
    assert flags == {'source_unavailable_fns', 'source_unavailable_fssp', 'source_unavailable_arbitration'}


def test_only_other_banks_inquiries_in_30_days_trigger_flag():
    responses = healthy_responses()
    i = next(i for i, item in enumerate(responses) if item.source == SourceId.BKI)
    bki = responses[i].payload
    owner = type(bki.reports[1]).model_validate(bki.reports[1].model_dump() | {'inquiries': [
        {'inquired_at': '2026-10-01', 'lender_type': 'bank'},
        {'inquired_at': '2026-10-10', 'lender_type': 'mfo'},
        {'inquired_at': '2026-10-24', 'lender_type': 'leasing'},
    ]})
    responses[i] = responses[i].model_copy(update={'payload': bki.model_copy(update={'reports': [bki.reports[0], owner]})})
    assert 'multiple_recent_bki_inquiries' not in {reason.code for reason in result(responses).flags}

    owner = type(owner).model_validate(owner.model_dump() | {'inquiries': [
        {'inquired_at': '2026-10-01', 'lender_type': 'bank'},
        {'inquired_at': '2026-10-10', 'lender_type': 'bank'},
        {'inquired_at': '2026-10-24', 'lender_type': 'bank'},
    ]})
    responses[i] = responses[i].model_copy(update={'payload': bki.model_copy(update={'reports': [bki.reports[0], owner]})})
    assert 'multiple_recent_bki_inquiries' in {reason.code for reason in result(responses).flags}


def test_overdraft_uses_three_month_business_age_stop_threshold():
    responses = healthy_responses()
    i = next(i for i, item in enumerate(responses) if item.source == SourceId.EGRUL)
    egrul = type(responses[i].payload).model_validate(
        responses[i].payload.model_dump() | {'registered_at': '2026-07-24'})
    responses[i] = responses[i].model_copy(update={'payload': egrul})
    overdraft = result(responses, preferred_product='overdraft')
    regular = result(responses, preferred_product='working_capital')
    assert 'business_too_young' not in {reason.code for reason in overdraft.stop_factors}
    assert 'business_too_young' in {reason.code for reason in regular.stop_factors}
