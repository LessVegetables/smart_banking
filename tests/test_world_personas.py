"""Demo personas (T12): the committed persona file is what the conveyor decides today, and it covers the demo."""
import json
import os
import tempfile

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='alfa-tests-'))

import pytest

from apps.api.statements import Statement
from apps.world import generate_world
from apps.world.months import first_full_month, last_full_month, month_name
from apps.world.personas import PERSONAS_FILE, STATEMENTS_DIR, read, select_personas, statements_of
from apps.world.statements import other_bank_statement


@pytest.fixture(scope='module')
def world():
    return generate_world()  # the default world the demo runs on


@pytest.fixture(scope='module')
def committed():
    return read(PERSONAS_FILE)


def test_persona_file_is_up_to_date(world, committed):
    # Fails after retraining the card or changing the world or config/: run `python -m apps.world.personas`.
    assert select_personas(world).model_dump(mode='json') == committed.model_dump(mode='json')
    for name, statement in statements_of(world, committed).items():
        assert json.loads((STATEMENTS_DIR / name).read_text(encoding='utf-8')) == statement


def test_personas_cover_the_demo(committed):
    personas = committed.personas
    every = [p.expected for p in personas] + [p.expected_after_statement for p in personas if p.expected_after_statement]
    assert {e.status for e in every} >= {'offered', 'declined', 'in_review', 'needs_data'}
    assert {e.route for e in every} == {'auto', 'underwriter', 'committee'}
    assert {e.data_quality_tier for e in every} >= {'FULL', 'PARTIAL', 'THIN'}
    assert {p.legal_form for p in personas} == {'ip', 'ooo'}
    assert {p.legal_form for p in personas if p.expected.status == 'offered'} == {'ip', 'ooo'}
    assert {'shell', 'inflater'} <= {p.archetype for p in personas}
    assert any(e.stop_factors for e in every)
    assert any('company_not_found' in e.reasons for e in every)
    assert any(e.matches_request is False and e.offers for e in every)
    assert any('equipment_pledge' in o.collateral for e in every for o in e.offers)
    assert any(p.preapproval and p.preapproval.approved for p in personas)
    assert len({p.inn for p in personas}) == len(personas)


def test_statement_personas_get_more_with_the_statement(committed):
    with_statement = [p for p in committed.personas if p.statement_file]
    assert {p.expected.data_quality_tier for p in with_statement} == {'PARTIAL', 'THIN'}
    for p in with_statement:
        assert (STATEMENTS_DIR / p.statement_file).exists()
        after = p.expected_after_statement
        assert after.status == 'in_review' and 'route_statement_upload' in after.route_reasons
        assert max(o.amount for o in after.offers) > max((o.amount for o in p.expected.offers), default=0)


def test_persona_file_holds_no_hidden_outcome():
    text = PERSONAS_FILE.read_text(encoding='utf-8')
    for hidden in ('"q"', 'pd_12m', 'default_12m', 'our_share'):
        assert hidden not in text


def test_other_bank_statement(world):
    last = month_name(last_full_month(world.meta.as_of))
    seen = set()
    for company in world.companies[:600]:
        statement = other_bank_statement(world.meta.seed, company, world.meta.as_of)
        if company.archetype not in ('main_bank_elsewhere', 'new_account'):
            assert statement is None
            continue
        seen.add(company.archetype)
        parsed = Statement.model_validate(statement)
        assert parsed.inn == company.inn and parsed.months[-1].month == last  # recent enough for T23's checks
        if company.archetype == 'main_bank_elsewhere':
            # The other bank carries the rest of the non-cash business; up to half of the revenue is cash (T02).
            ours = sum(m.inflows.customers for m in company.accounts.months[-12:])
            theirs = sum(m.inflows.customers for m in parsed.months[-12:])
            assert theirs > ours * 0.3 and 0.3 < (ours + theirs) / company.annual_revenue < 1.3
            assert all(m.outflows.loan_payments == 0 for m in parsed.months)
        else:
            # The old account until ours was opened, then dormant.
            opened = month_name(first_full_month(company.accounts.account_opened_at))
            assert all(m.inflows.total == 0 for m in parsed.months if m.month >= opened)
            assert any(m.inflows.total > 0 for m in parsed.months if m.month < opened)
    assert seen == {'main_bank_elsewhere', 'new_account'}
    assert other_bank_statement(world.meta.seed, world.companies[1], world.meta.as_of) == \
        other_bank_statement(world.meta.seed, world.companies[1], world.meta.as_of)
