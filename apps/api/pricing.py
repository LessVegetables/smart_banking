"""Risk-based rate (T16, SPEC §9.2): funding + expected loss + processing + capital + margin.

- expected loss = PD * LGD; LGD comes from the best collateral, and the Корпорация МСП
  guarantee covers a share of what remains at risk;
- processing = the route's one-off cost spread over the loan's life: cost / (average
  balance * term in years). The average balance is the mean opening balance of the
  annuity schedule, so it depends on the rate itself: `price` iterates until the rate
  stops moving at 4 decimals.

Every component is rounded to 4 decimals and the rate is their sum, so the staff see
components that add up. PD and grade come from the champion `ScoreResult`, never from the
shadow challenger (SCORING.md). The rate cap is checked here; what to do above it
(offer collateral or decline, SPEC §9.2) is the offer generator's call (T17, offers.py).
"""
from __future__ import annotations

from collections.abc import Iterable

from apps.api.contracts import CollateralType, PriceBreakdown, Route

from apps.api.credit_policy import CreditPolicy, default_policy

DIGITS = 4
MAX_ITERATIONS = 50


def annuity_payment(amount: float, rate: float, term_months: int) -> float:
    i = rate / 12
    if i == 0:
        return amount / term_months
    return amount * i / (1 - (1 + i) ** -term_months)


def present_value(payment: float, rate: float, term_months: int) -> float:
    """The amount a monthly annuity payment repays over the term: SPEC §9.1 `PV(P_max, rate, term)`."""
    i = rate / 12
    if i == 0:
        return payment * term_months
    return payment * (1 - (1 + i) ** -term_months) / i


def average_balance(amount: float, rate: float, term_months: int) -> float:
    """Mean balance at the start of each month of the annuity schedule."""
    i = rate / 12
    payment = annuity_payment(amount, rate, term_months)
    balance, total = float(amount), 0.0
    for _ in range(term_months):
        total += balance
        balance = balance * (1 + i) - payment
    return total / term_months


def lgd(collateral: Iterable[CollateralType], policy: CreditPolicy | None = None) -> float:
    """The lowest LGD the collateral gives; the МСП guarantee then halves it (SPEC §9.2)."""
    table = (policy or default_policy()).pricing.lgd
    collateral = set(collateral)
    value = table.unsecured
    if CollateralType.OWNER_GUARANTEE in collateral:
        value = min(value, table.owner_guarantee)
    if CollateralType.EQUIPMENT_PLEDGE in collateral:
        value = min(value, table.equipment_pledge)
    if CollateralType.MSP_GUARANTEE in collateral:
        value *= (policy or default_policy()).pricing.msp_guarantee_lgd_factor
    return value


def guarantee_fee(collateral: Iterable[CollateralType], policy: CreditPolicy | None = None) -> float:
    """`Offer.guarantee_fee`: annual, only with the МСП guarantee, not part of the rate."""
    policy = policy or default_policy()
    return policy.pricing.msp_guarantee_fee if CollateralType.MSP_GUARANTEE in set(collateral) else 0.0


def price(pd: float, amount: int, term_months: int, collateral: Iterable[CollateralType], route: Route,
          policy: CreditPolicy | None = None) -> PriceBreakdown:
    """Price one loan. `route` sets the processing cost: the route T18 gives this amount."""
    if amount <= 0:
        raise ValueError('amount must be positive')
    policy = policy or default_policy()
    p = policy.pricing
    loss = lgd(collateral, policy)
    fixed = {
        'funding': round(p.funding, DIGITS),
        'expected_loss': round(pd * loss, DIGITS),
        'capital': round(p.capital, DIGITS),
        'margin': round(p.margin, DIGITS),
    }
    cost = p.processing_cost[route]
    rate = sum(fixed.values())
    for _ in range(MAX_ITERATIONS):
        balance = average_balance(amount, rate, term_months)
        processing = round(cost / (balance * term_months / 12), DIGITS)
        new_rate = round(sum(fixed.values()) + processing, DIGITS)
        if new_rate == rate:
            break
        rate = new_rate
    balance = average_balance(amount, rate, term_months)
    return PriceBreakdown(**fixed, processing=processing, rate=rate, pd=pd, lgd=loss,
                          processing_cost=cost, average_balance=round(balance))


def within_cap(breakdown: PriceBreakdown, fee: float = 0.0, policy: CreditPolicy | None = None) -> bool:
    """Whether the client's cost (rate plus the МСП guarantee fee) is under the rate cap."""
    return breakdown.rate + fee <= (policy or default_policy()).pricing.rate_cap + 1e-9


def expected_profit(breakdown: PriceBreakdown, term_months: int) -> int:
    """`Offer.expected_profit` (staff only): the margin earned on the average balance over the term."""
    return round(breakdown.margin * breakdown.average_balance * term_months / 12)
