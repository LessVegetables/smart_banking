"""Limit (T16, SPEC §9.1): the minimum of every constraint that applies, each with its inputs.

    cash_flow    PV(P_max, rate, term); P_max = CFADS / DSCR_min(grade) - current payments,
                 CFADS = median monthly operating flow * (1 - min(cap, CV)); not computed for THIN
    leverage     multiplier * annual revenue - current debt (0.7 for the investment loan)
    collateral   equipment pledge: min(LTV * price, price - down payment)
    turnover     multiplier * average monthly operating inflows, overdraft only
    product_max  the product's maximum
    requested    the requested amount, an upper bound only

The cash-flow constraint depends on the rate and the rate on the amount (processing cost
over the average balance), so `quote` iterates the two until the amount stops moving.
The building blocks (`CashFlow`, `limit_breakdown`, `quote`) are what the offer generator
(T17) combines into offers; see LIMITS.md.
"""
from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from apps.api.contracts import (CollateralType, DataQuality, DataQualityTier, EquipmentInfo, Grade, LimitBreakdown,
                                LimitConstraint, MonthlyTurnover, PriceBreakdown, ProductId, Route, ScoreResult)
from apps.api.credit_policy import CreditPolicy, default_policy
from apps.api.features import SourcePayloads, business_report, fns_revenue
from apps.api.features.accounts import window
from apps.api.features.credit import CONTINGENT
from apps.api.pricing import annuity_payment, expected_profit, guarantee_fee, present_value, price, within_cap

MAX_ITERATIONS = 20
REVOLVING_PRODUCTS = frozenset({ProductId.OVERDRAFT, ProductId.CREDIT_LINE})


def operating_flow(month: MonthlyTurnover) -> int:
    """Customer inflows minus outflows, without loan payments and own transfers (SPEC §9.1)."""
    o = month.outflows
    return month.inflows.customers - (o.total - o.loan_payments - o.own_transfers)


@dataclass(frozen=True)
class CashFlow:
    """The application's limit inputs from our account and the credit bureau, computed once."""
    tier: DataQualityTier
    months: int  # account months used, up to 12
    median_operating_flow: int | None
    cv: float | None  # of the monthly operating flow; None when its mean is not positive
    cfads: int | None  # None for THIN: no cash-flow limit (SPEC §5.1)
    average_monthly_inflows: int | None  # customer inflows
    current_payments: int  # monthly, open loans of the business
    current_debt: int
    annual_revenue: int | None
    revenue_basis: Literal['fns', 'accounts'] | None


def cash_flow(payloads: SourcePayloads, data_quality: DataQuality, policy: CreditPolicy | None = None) -> CashFlow:
    """`data_quality`: the application's tier, from `data_quality_from_responses` (T09).

    PARTIAL uses the flow on our account as it is: no scaling up for what goes through other
    banks. The credit bureau is required (SPEC §5): without it there is no decision to price.
    """
    policy = policy or default_policy()
    if payloads.bki is None:
        raise ValueError('the credit bureau report is required for the limit')
    business = business_report(payloads.bki)
    loans = [loan for loan in business.loans if loan.closed_at is None and loan.kind not in CONTINGENT] \
        if business else []

    months = window(payloads.accounts) if payloads.accounts else []
    median = cv = cfads = inflows = None
    if months:
        flows = [operating_flow(m) for m in months]
        median = round(statistics.median(flows))
        mean = statistics.fmean(flows)
        cv = round(statistics.pstdev(flows) / mean, 3) if mean > 0 else None
        inflows = round(statistics.fmean(m.inflows.customers for m in months))
        if data_quality.tier != DataQualityTier.THIN:
            discount = policy.limit.cfads_cv_cap if cv is None else min(policy.limit.cfads_cv_cap, cv)
            cfads = max(0, int(round(median * (1 - discount), -2)))

    revenue = fns_revenue(payloads.fns, payloads.egrul, payloads.accounts)
    if revenue is not None and revenue.annualized > 0:
        annual_revenue, basis = revenue.annualized, 'fns'
    elif months:
        # Not filed with ФНС yet: our own account's customer inflows, as they are (no scaling to 12 months).
        annual_revenue, basis = sum(m.inflows.customers for m in months), 'accounts'
    else:
        annual_revenue, basis = None, None

    return CashFlow(tier=data_quality.tier, months=len(months), median_operating_flow=median, cv=cv, cfads=cfads,
                    average_monthly_inflows=inflows, current_payments=sum(loan.monthly_payment for loan in loans),
                    current_debt=sum(loan.balance for loan in loans), annual_revenue=annual_revenue,
                    revenue_basis=basis)


def cash_flow_from_responses(responses: Iterable, data_quality: DataQuality,
                             policy: CreditPolicy | None = None) -> CashFlow:
    """The live engine's entry point: limit inputs from enrichment's answers."""
    return cash_flow(SourcePayloads.from_responses(responses), data_quality, policy)


def _floor(value: float, step: int) -> int:
    return max(0, int(value // step * step))


def limit_breakdown(cf: CashFlow, grade: Grade, product: ProductId, term_months: int, rate: float,
                    collateral: Sequence[CollateralType] = (), requested: int | None = None,
                    equipment: EquipmentInfo | None = None, policy: CreditPolicy | None = None) -> LimitBreakdown:
    """All six constraints at this rate and term; `requested` is None without an application (pre-approval)."""
    policy = policy or default_policy()
    lp, pp = policy.limit, policy.products[product]
    _check_term(product, term_months, policy)
    constraints = [
        _cash_flow_constraint(cf, grade, product, term_months, rate, policy),
        _leverage_constraint(cf, pp.leverage_multiplier or lp.leverage_multiplier),
        _collateral_constraint(collateral, equipment, pp.min_down_payment_share, lp.collateral_ltv),
        _turnover_constraint(cf, product, lp.turnover_multiplier),
        LimitConstraint(name='product_max', value=pp.max_amount, inputs={'product': product.value}, note=None),
        LimitConstraint(name='requested', value=requested, inputs={},
                        note='Запрос — только верхняя граница' if requested is not None
                        else 'Без заявки: запрошенной суммы нет'),
    ]
    applicable = [c for c in constraints if c.value is not None]
    limit = min(c.value for c in applicable)
    # On a tie name the later constraint: "as requested" reads better than "cash flow" at the same amount.
    binding = next(c.name for c in reversed(applicable) if c.value == limit)
    return LimitBreakdown(constraints=constraints, binding=binding, limit=limit)


def _check_term(product: ProductId, term_months: int, policy: CreditPolicy) -> None:
    pp = policy.products[product]
    if not pp.min_term <= term_months <= pp.max_term:
        raise ValueError(f'{product.value}: term {term_months} outside {pp.min_term}–{pp.max_term} months')


def _cash_flow_constraint(cf: CashFlow, grade: Grade, product: ProductId, term_months: int, rate: float,
                          policy: CreditPolicy) -> LimitConstraint:
    if cf.cfads is None:
        note = ('Уровень данных THIN: лимит по денежному потоку не считается' if cf.tier == DataQualityTier.THIN
                else 'Нет оборотов по счёту')
        return LimitConstraint(name='cash_flow', value=None, inputs={}, note=note)
    dscr = policy.limit.dscr_min[grade]
    p_max = int(cf.cfads / dscr) - cf.current_payments
    value = _floor(present_value(p_max, rate, term_months), policy.limit.amount_step) if p_max > 0 else 0
    note = 'PV допустимого платежа при ставке и сроке предложения'
    if product in REVOLVING_PRODUCTS:
        # SIMPLIFICATION: a revolving limit is sized as an annuity over its 12-month period; a bank
        # would size it on the tranche terms and the expected usage.
        note += '; возобновляемый лимит — как аннуитет на срок лимита'
    if cf.tier == DataQualityTier.PARTIAL:
        note += '; данные PARTIAL — только фактический поток по нашему счёту, без экстраполяции'
    return LimitConstraint(name='cash_flow', value=value, note=note, inputs={
        'median_operating_flow': cf.median_operating_flow, 'cv': cf.cv, 'cfads': cf.cfads, 'dscr_min': dscr,
        'current_payments': cf.current_payments, 'p_max': p_max, 'rate': rate, 'term_months': term_months})


def _leverage_constraint(cf: CashFlow, multiplier: float) -> LimitConstraint:
    if cf.annual_revenue is None:
        # The declared revenue is never a limit input (SPEC §1.1): no verified revenue, no leverage room.
        return LimitConstraint(name='leverage', value=0, inputs={'current_debt': cf.current_debt},
                               note='Нет подтверждённой выручки: ни ФНС, ни оборотов по счёту')
    value = max(0, int(multiplier * cf.annual_revenue) - cf.current_debt)
    note = None if cf.revenue_basis == 'fns' else 'Выручки по данным ФНС нет: поступления от покупателей по нашему счёту'
    return LimitConstraint(name='leverage', value=value, note=note, inputs={
        'annual_revenue': cf.annual_revenue, 'multiplier': multiplier, 'current_debt': cf.current_debt})


def _collateral_constraint(collateral: Sequence[CollateralType], equipment: EquipmentInfo | None,
                           min_down_payment_share: float | None, ltv: float) -> LimitConstraint:
    if CollateralType.EQUIPMENT_PLEDGE not in collateral:
        return LimitConstraint(name='collateral', value=None, inputs={}, note='Залога нет')
    if equipment is None:
        raise ValueError('an equipment pledge needs the equipment price and down payment')
    inputs = {'equipment_price': equipment.price, 'down_payment': equipment.down_payment, 'ltv': ltv}
    if min_down_payment_share is not None and equipment.down_payment < min_down_payment_share * equipment.price:
        return LimitConstraint(name='collateral', value=0, inputs=inputs | {'min_down_payment_share': min_down_payment_share},
                               note=f'Собственный взнос меньше {min_down_payment_share:.0%} цены оборудования')
    # SPEC §4 caps the loan at 70% of the price and §9.1 at price minus down payment: both hold.
    # SIMPLIFICATION: the pledge is valued at its purchase price; a bank would use an independent
    # valuation with a liquidity discount.
    value = min(int(ltv * equipment.price), equipment.price - equipment.down_payment)
    return LimitConstraint(name='collateral', value=value, inputs=inputs,
                           note='Меньшее из LTV × цена и цены за вычетом собственного взноса')


def _turnover_constraint(cf: CashFlow, product: ProductId, multiplier: float) -> LimitConstraint:
    if product != ProductId.OVERDRAFT:
        return LimitConstraint(name='turnover', value=None, inputs={}, note='Только для овердрафта')
    if cf.average_monthly_inflows is None:
        return LimitConstraint(name='turnover', value=0, inputs={}, note='Нет оборотов по счёту')
    return LimitConstraint(name='turnover', value=int(multiplier * cf.average_monthly_inflows), note=None,
                           inputs={'average_monthly_inflows': cf.average_monthly_inflows, 'multiplier': multiplier})


@dataclass(frozen=True)
class Quote:
    """One product, term and collateral, priced: the amount the limit allows at its own rate."""
    amount: int  # 0 when nothing can be lent
    limit: LimitBreakdown
    price: PriceBreakdown | None  # None when amount is 0
    monthly_payment: int
    guarantee_fee: float  # `Offer.guarantee_fee`
    within_cap: bool  # rate + guarantee fee under the rate cap; above it the risk can't be priced (SPEC §9.2)
    below_product_minimum: bool
    expected_profit: int | None  # staff only


def quote(cf: CashFlow, score: ScoreResult, product: ProductId, term_months: int, route: Route,
          collateral: Sequence[CollateralType] = (), requested: int | None = None,
          equipment: EquipmentInfo | None = None, policy: CreditPolicy | None = None) -> Quote:
    """Price and limit one variant. The amount is the requested one when the limit covers it, otherwise
    the limit rounded down to `amount_step`. `route` (T18) sets the processing cost; `score` must be
    the champion's `ScoreResult`, never the shadow challenger."""
    policy = policy or default_policy()
    _check_term(product, term_months, policy)
    step = policy.limit.amount_step
    fee = guarantee_fee(collateral, policy)

    def evaluate(amount: int) -> tuple[PriceBreakdown, LimitBreakdown, int]:
        breakdown = price(score.pd, amount, term_months, collateral, route, policy)
        limit = limit_breakdown(cf, score.grade, product, term_months, breakdown.rate, collateral, requested,
                                equipment, policy)
        allowed = requested if requested is not None and requested <= limit.limit else _floor(limit.limit, step)
        return breakdown, limit, allowed

    amount = requested if requested is not None else policy.products[product].max_amount
    for _ in range(MAX_ITERATIONS):
        breakdown, limit, allowed = evaluate(amount)
        if allowed in (amount, 0):
            break
        amount = allowed
    # Without a fixed point, step down until the limit at the amount's own rate covers it.
    while 0 < allowed < amount:
        amount = allowed
        breakdown, limit, allowed = evaluate(amount)
    if allowed == 0:
        return Quote(amount=0, limit=limit, price=None, monthly_payment=0, guarantee_fee=fee, within_cap=False,
                     below_product_minimum=True, expected_profit=None)
    return Quote(amount=amount, limit=limit, price=breakdown,
                 monthly_payment=round(annuity_payment(amount, breakdown.rate, term_months)), guarantee_fee=fee,
                 within_cap=within_cap(breakdown, fee, policy),
                 below_product_minimum=amount < policy.products[product].min_amount,
                 expected_profit=expected_profit(breakdown, term_months))
