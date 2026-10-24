"""T16 risk-based pricing (SPEC §9.2) and the credit policy in config/credit_policy.json."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api.contracts import CollateralType, Decision, Route
from apps.api.credit_policy import CONFIG_PATH, CreditPolicy, default_policy, load_policy
from apps.api.pricing import (annuity_payment, average_balance, expected_profit, guarantee_fee, lgd,
                              present_value, price, within_cap)

FIXTURES = Path(__file__).parent / 'fixtures'
GUARANTEE, PLEDGE, MSP = CollateralType.OWNER_GUARANTEE, CollateralType.EQUIPMENT_PLEDGE, CollateralType.MSP_GUARANTEE


def decision(name: str) -> Decision:
    return Decision.model_validate_json((FIXTURES / name).read_text('utf-8'))


# --- Annuity math --------------------------------------------------------------------

def test_present_value_inverts_the_payment():
    payment = annuity_payment(1_000_000, 0.2039, 17)
    assert round(payment) == 68223
    assert present_value(payment, 0.2039, 17) == pytest.approx(1_000_000)


def test_zero_rate():
    assert annuity_payment(120_000, 0, 12) == 10_000
    assert present_value(10_000, 0, 12) == 120_000


def test_average_balance_is_the_mean_opening_balance():
    assert round(average_balance(1_000_000, 0.2039, 17)) == 553166
    assert average_balance(120_000, 0, 12) == pytest.approx(65_000)  # 120k, 110k, ... 10k


# --- Rate -----------------------------------------------------------------------------

@pytest.mark.parametrize('name, route', [('decision_offered.json', Route.AUTO),
                                         ('decision_in_review.json', Route.UNDERWRITER)])
def test_fixture_prices(name, route):
    for offer in decision(name).offers:
        got = price(offer.price.pd, offer.amount, offer.term_months, offer.collateral, route)
        assert got == offer.price
        assert expected_profit(got, offer.term_months) == offer.expected_profit


@pytest.mark.parametrize('pd, amount, collateral, route, spec_rate', [
    (0.02, 1_000_000, [], Route.AUTO, 0.209),
    (0.06, 1_000_000, [], Route.AUTO, 0.235),
    (0.06, 1_000_000, [PLEDGE], Route.AUTO, 0.217),
    (0.06, 1_000_000, [], Route.UNDERWRITER, 0.275),
])
def test_spec_examples(pd, amount, collateral, route, spec_rate):
    # SPEC §9.2 rounds the average balance to ~540k; the exact schedule gives ~559k, so up to 0.2 pp off.
    assert price(pd, amount, 12, collateral, route).rate == pytest.approx(spec_rate, abs=0.0025)


def test_manual_review_is_cheap_for_large_loans():
    assert price(0.06, 10_000_000, 12, [], Route.UNDERWRITER).processing == pytest.approx(0.005, abs=0.001)
    small = price(0.06, 1_000_000, 12, [], Route.UNDERWRITER).rate - price(0.06, 1_000_000, 12, [], Route.AUTO).rate
    assert small == pytest.approx(0.04, abs=0.005)  # "ручная проверка займа на 1 млн ₽ стоит клиенту 4 п. п."


def test_components_add_up():
    p = price(0.0457, 2_345_000, 23, [GUARANTEE, MSP], Route.COMMITTEE)
    assert p.rate == round(p.funding + p.expected_loss + p.processing + p.capital + p.margin, 4)
    assert p.funding == 0.155 and p.capital == 0.015 and p.margin == 0.02
    assert p.processing_cost == 60_000


def test_lgd_takes_the_best_collateral():
    assert lgd([]) == 0.65
    assert lgd([GUARANTEE]) == 0.55
    assert lgd([GUARANTEE, PLEDGE]) == 0.35
    assert lgd([MSP]) == pytest.approx(0.325)
    assert lgd([GUARANTEE, MSP]) == pytest.approx(0.275)


def test_msp_guarantee_fee():
    assert guarantee_fee([GUARANTEE]) == 0.0
    assert guarantee_fee([GUARANTEE, MSP]) == 0.01


def test_rate_cap_includes_the_guarantee_fee():
    p = price(0.15, 1_000_000, 12, [], Route.AUTO)  # 15.5 + 9.75 + 0.5 + 1.5 + 2 ≈ 29.3%
    assert within_cap(p)
    assert not within_cap(p, fee=0.01)
    assert not within_cap(price(0.25, 1_000_000, 12, [], Route.AUTO))


def test_amount_must_be_positive():
    with pytest.raises(ValueError):
        price(0.02, 0, 12, [], Route.AUTO)


# --- Policy ---------------------------------------------------------------------------

def test_policy_matches_spec():
    policy = default_policy()
    assert policy.version == 'credit_policy_v0'
    assert {g.value: v for g, v in policy.limit.dscr_min.items()} == {'A': 1.2, 'B': 1.3, 'C': 1.5, 'D': 1.75, 'E': 2.0}
    assert policy.pricing.funding == pytest.approx(0.155)
    assert policy.pricing.rate_cap == 0.30


def test_values_come_from_config(tmp_path):
    raw = json.loads(CONFIG_PATH.read_text('utf-8'))
    raw['pricing']['margin'] = 0.03
    path = tmp_path / 'credit_policy.json'
    path.write_text(json.dumps(raw), 'utf-8')
    policy = load_policy(path)
    assert price(0.02, 1_000_000, 12, [], Route.AUTO, policy).margin == 0.03


@pytest.mark.parametrize('change', [
    lambda r: r['limit']['dscr_min'].pop('E'),
    lambda r: r['pricing']['processing_cost'].pop('committee'),
    lambda r: r['products'].pop('overdraft'),
    lambda r: r['products']['working_capital'].update(min_term=30),
    lambda r: r['pricing'].update(rate_cap=1.5),
    lambda r: r.update(unknown=1),
])
def test_policy_validation(change):
    raw = json.loads(CONFIG_PATH.read_text('utf-8'))
    change(raw)
    with pytest.raises(ValidationError):
        CreditPolicy.model_validate(raw)
