"""Offer set and counterfactual hints (T17, SPEC §10): instead of one counter-offer, a few priced variants.

The variants are tried in SPEC §10 order and the first `MAX_OFFERS` offerable ones are kept:

    as_requested     the requested product, amount and term, standard collateral
    longer_term      the requested amount over the shortest longer term that fits
    max_amount       the requested term, the largest amount the limit allows
    with_collateral  plus the Корпорация МСП guarantee, only when it unlocks what the standard
                     collateral can't: the requested amount, or a rate under the cap
    other_product    the product the purpose points to, when the client chose another one

Each variant is one `quote` (limits.py) at the route of its own amount. A variant is offerable when
its amount is positive, not below the product minimum, and the rate with the guarantee fee is under
the cap (SPEC §9.2). Counterfactuals re-run `quote` on a changed `CashFlow` or request. See OFFERS.md.
"""
from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from apps.api.contracts import (ApplicationRequest, CollateralType, Counterfactual, LegalForm, Offer, ProductId,
                                Purpose, Reason, Route, ScoreResult)
from apps.api.credit_policy import CreditPolicy, default_policy
from apps.api.limits import REVOLVING_PRODUCTS, CashFlow, Quote, quote
from apps.api.pricing import annuity_payment, price

RouteFor = Callable[[int], Route]  # the route of an amount (T18); grade, flags and tier are the caller's

# SIMPLIFICATION: offer policy lives here until it moves to config/credit_policy.json (shared-folder PR,
# docs/v2/NOTES.md, T17 → Unassigned). The values come from the fixtures and SPEC §4, §10.
MAX_OFFERS = 3
OFFER_VALID_DAYS = 10
REAPPLY_AFTER_DAYS = 90  # after an economic decline: about a quarter of new account history
CREDIT_LINE_UNUSED_FEE = 0.01  # annual, on the unused limit (SPEC §4); in neither the rate nor `Offer`
MAX_ROUTE_ITERATIONS = 5

# The product a purpose points to (SPEC §4). T26 recommends the product in the intake with the same mapping.
PURPOSE_PRODUCT = {
    Purpose.INVENTORY: ProductId.WORKING_CAPITAL,
    Purpose.PAYROLL_RENT: ProductId.WORKING_CAPITAL,
    Purpose.EQUIPMENT: ProductId.INVESTMENT,
    Purpose.CASH_GAP: ProductId.OVERDRAFT,
    Purpose.SEASONAL_GAP: ProductId.CREDIT_LINE,
    Purpose.CONTRACT: ProductId.CREDIT_LINE,
    Purpose.OTHER: ProductId.WORKING_CAPITAL,
}

PRODUCT_NAMES = {
    ProductId.OVERDRAFT: 'Овердрафт',
    ProductId.WORKING_CAPITAL: 'Оборотный кредит',
    ProductId.INVESTMENT: 'Инвестиционный кредит',
    ProductId.CREDIT_LINE: 'Кредитная линия',
}

PRODUCT_TERMS = {
    ProductId.OVERDRAFT: 'каждый транш гасится в течение 60 дней',
    ProductId.CREDIT_LINE: 'каждый транш до 180 дней',
}


def rub(value: int) -> str:
    return f'{value:,}'.replace(',', '\xa0') + ' ₽'


@dataclass(frozen=True)
class OfferSet:
    """What the engine (T19) puts into the `Decision`: `status` is `offered` with offers, otherwise
    the caller decides between `declined` and `in_review` from the route."""
    offers: list[Offer]
    routes: dict[str, Route]  # offer_id → the route its price was computed on
    counterfactuals: list[Counterfactual]
    reasons: list[Reason]  # why the amount is what it is, or why nothing can be offered
    matches_request: bool | None  # `Decision.matches_request`
    reapply_after: date | None  # set when there are no offers


def requested_product(request: ApplicationRequest) -> ProductId:
    return request.preferred_product or PURPOSE_PRODUCT[request.purpose]


def standard_collateral(product: ProductId, legal_form: LegalForm) -> list[CollateralType]:
    """SPEC §3–4: owner guarantee is standard for ООО term loans, the investment loan is secured by the
    equipment it buys; the overdraft and the credit line go unsecured; an ИП is liable personally."""
    collateral = []
    if product == ProductId.INVESTMENT:
        collateral.append(CollateralType.EQUIPMENT_PLEDGE)
    if legal_form == LegalForm.OOO and product in (ProductId.WORKING_CAPITAL, ProductId.INVESTMENT):
        collateral.append(CollateralType.OWNER_GUARANTEE)
    return collateral


def clamp_term(product: ProductId, term_months: int, policy: CreditPolicy) -> int:
    pp = policy.products[product]
    return min(max(term_months, pp.min_term), pp.max_term)


def offerable(q: Quote) -> bool:
    return q.amount > 0 and not q.below_product_minimum and q.within_cap


@dataclass(frozen=True)
class _Variant:
    kind: str
    product: ProductId
    term_months: int
    collateral: tuple[CollateralType, ...]
    quote: Quote
    route: Route


class _Engine:
    """One application's inputs, so every variant and counterfactual is the same `quote` call."""

    def __init__(self, request: ApplicationRequest, cf: CashFlow, score: ScoreResult, route_for: RouteFor,
                 policy: CreditPolicy):
        self.request, self.cf, self.score, self.route_for, self.policy = request, cf, score, route_for, policy

    def quote(self, product: ProductId, term: int, collateral: tuple[CollateralType, ...], requested: int,
              cf: CashFlow | None = None, equipment=None) -> tuple[Quote, Route]:
        """`quote` at the route of the amount it ends up with: the processing cost depends on the route
        and the route on the amount, so re-price until the two agree; if they never do, keep the
        costlier route."""
        cf = cf or self.cf
        equipment = equipment if equipment is not None else self.request.equipment
        route = self.route_for(requested)
        seen: dict[Route, Quote] = {}
        for _ in range(MAX_ROUTE_ITERATIONS):
            q = quote(cf, self.score, product, term, route, collateral, requested, equipment, self.policy)
            seen[route] = q
            if q.amount == 0 or (nxt := self.route_for(q.amount)) == route:
                return q, route
            if nxt in seen:
                break
            route = nxt
        route = max(seen, key=lambda r: self.policy.pricing.processing_cost[r])
        return seen[route], route

    def variant(self, kind: str, product: ProductId, term: int, collateral, requested: int) -> _Variant:
        collateral = tuple(collateral)
        q, route = self.quote(product, term, collateral, requested)
        return _Variant(kind, product, term, collateral, q, route)


def quote_on_route(request: ApplicationRequest, cf: CashFlow, score: ScoreResult, route_for: RouteFor,
                   product: ProductId, term_months: int, collateral, amount: int,
                   policy: CreditPolicy | None = None) -> tuple[Quote, Route]:
    """One variant priced at the route of its own amount, as `generate_offers` prices each offer: for a
    staff-changed offer (T20, staff_actions.py)."""
    engine = _Engine(request, cf, score, route_for, policy or default_policy())
    return engine.quote(product, term_months, tuple(collateral), amount)


def generate_offers(request: ApplicationRequest, cf: CashFlow, score: ScoreResult, route_for: RouteFor,
                    as_of: date, policy: CreditPolicy | None = None) -> OfferSet:
    """`cf` from `cash_flow_from_responses` and `score` from the champion (never the challenger);
    `as_of` is the decision day: offers are valid `OFFER_VALID_DAYS` from it."""
    policy = policy or default_policy()
    engine = _Engine(request, cf, score, route_for, policy)
    product = requested_product(request)
    if product == ProductId.INVESTMENT and request.equipment is None:
        raise ValueError('the investment loan needs the equipment price and down payment')
    term = clamp_term(product, request.term_months, policy)
    base_collateral = standard_collateral(product, request.legal_form)
    amount = request.amount

    base = engine.variant('as_requested', product, term, base_collateral, amount)
    candidates: list[_Variant] = []
    reached = base.quote.amount == amount and offerable(base.quote)
    if reached:
        candidates.append(base)
    else:
        longer = _longer_term(engine, product, term, base_collateral, amount, policy)
        if longer:
            candidates.append(longer)
        if 0 < base.quote.amount < amount:
            candidates.append(dataclasses.replace(base, kind='max_amount'))
    msp = _with_guarantee(engine, base, reached)
    if msp:
        candidates.append(msp)
    other = PURPOSE_PRODUCT[request.purpose]
    if other != product and (other != ProductId.INVESTMENT or request.equipment is not None):
        candidates.append(engine.variant('other_product', other, clamp_term(other, request.term_months, policy),
                                         standard_collateral(other, request.legal_form), amount))

    kept: list[_Variant] = []
    for v in candidates:
        key = (v.product, v.quote.amount, v.term_months, set(v.collateral))
        if offerable(v.quote) and all(key != (k.product, k.quote.amount, k.term_months, set(k.collateral))
                                      for k in kept):
            kept.append(v)
    kept = kept[:MAX_OFFERS]

    offers = [_offer(request, v, product, as_of) for v in kept]
    return OfferSet(
        offers=offers,
        routes={o.offer_id: v.route for o, v in zip(offers, kept)},
        counterfactuals=counterfactuals(engine, base, product, term, base_collateral),
        reasons=_reasons(base, term, amount, offers, policy),
        matches_request=any(o.matches_request for o in offers) if offers else None,
        reapply_after=None if offers else as_of + timedelta(days=REAPPLY_AFTER_DAYS),
    )


def _longer_term(engine: _Engine, product: ProductId, term: int, collateral, amount: int,
                 policy: CreditPolicy) -> _Variant | None:
    """SPEC §10.2: the shortest term over the requested one at which the requested amount fits."""
    if product in REVOLVING_PRODUCTS:
        return None  # a 12-month limit: there is no longer term
    for t in range(term + 1, policy.products[product].max_term + 1):
        v = engine.variant('longer_term', product, t, collateral, amount)
        if v.quote.amount == amount and offerable(v.quote):
            return v
    return None


def _with_guarantee(engine: _Engine, base: _Variant, reached: bool) -> _Variant | None:
    """SPEC §10.4: the МСП guarantee halves the LGD but costs a fee on top of the rate, so it's offered
    only when it gets what the standard collateral can't. Owner guarantee and pledge are already standard."""
    if CollateralType.MSP_GUARANTEE in base.collateral:
        return None
    v = engine.variant('with_collateral', base.product, base.term_months,
                       (*base.collateral, CollateralType.MSP_GUARANTEE), engine.request.amount)
    unlocks_amount = not reached and v.quote.amount == engine.request.amount
    unlocks_cap = base.quote.amount > 0 and not base.quote.within_cap and v.quote.within_cap
    return v if offerable(v.quote) and (unlocks_amount or unlocks_cap) else None


def _offer(request: ApplicationRequest, v: _Variant, product: ProductId, as_of: date) -> Offer:
    q = v.quote
    return Offer(
        offer_id=f'{request.application_id}-{v.kind.replace("_", "-")}',
        kind=v.kind, product=v.product, amount=q.amount, term_months=v.term_months, rate=q.price.rate,
        monthly_payment=q.monthly_payment, collateral=list(v.collateral), guarantee_fee=q.guarantee_fee,
        disbursement='supplier' if v.product == ProductId.INVESTMENT else 'client_account',
        matches_request=v.kind == 'as_requested' and v.term_months == request.term_months,
        tradeoff=_tradeoff(request, v, product), limit=q.limit, price=q.price, expected_profit=q.expected_profit,
        valid_until=as_of + timedelta(days=OFFER_VALID_DAYS),
    )


def _tradeoff(request: ApplicationRequest, v: _Variant, product: ProductId) -> str:
    q = v.quote
    if v.kind == 'as_requested':
        text = 'Как запрошено' if v.term_months == request.term_months \
            else f'Запрошенная сумма на {v.term_months} мес.: срок продукта вместо {request.term_months}'
    elif v.kind == 'longer_term':
        text = f'Запрошенная сумма, но срок {v.term_months} мес. вместо {request.term_months}'
    elif v.kind == 'max_amount':
        text = f'Запрошенный срок {v.term_months} мес., сумма до {rub(q.amount)}'
    elif v.kind == 'with_collateral':
        text = (f'С гарантией Корпорации МСП: {rub(q.amount)} на {v.term_months} мес., '
                f'комиссия гарантии {q.guarantee_fee:.0%} годовых сверх ставки')
    else:
        text = f'{PRODUCT_NAMES[v.product]} вместо продукта «{PRODUCT_NAMES[product].lower()}»: {rub(q.amount)}'
        if v.product in PRODUCT_TERMS:
            text += f', {PRODUCT_TERMS[v.product]}'
    if v.product == ProductId.CREDIT_LINE:
        text += f'; комиссия {CREDIT_LINE_UNUSED_FEE:.0%} годовых за неиспользованный лимит'
    return text


# --- Reasons --------------------------------------------------------------------------

LIMIT_CLIENT_TEXT = {
    'cash_flow': 'ограничена денежным потоком вашего бизнеса: платёж должен оставлять запас на текущие расходы.',
    'leverage': 'ограничена выручкой бизнеса и текущим долгом.',
    'collateral': 'ограничена стоимостью залога и собственным взносом.',
    'turnover': 'ограничена половиной среднемесячных поступлений на счёт.',
    'product_max': 'ограничена максимумом продукта.',
}


def _limit_staff_text(name: str, inputs: dict, term: int) -> str:
    head = f'Лимит на {term} мес. ограничен'
    if name == 'cash_flow':
        return (f'{head} денежным потоком: CFADS {rub(inputs["cfads"])}, DSCR_min {inputs["dscr_min"]}, '
                f'текущие платежи {rub(inputs["current_payments"])}, P_max {rub(inputs["p_max"])}')
    if name == 'leverage':
        if 'annual_revenue' not in inputs:
            return f'{head} долговой нагрузкой: подтверждённой выручки нет'
        return (f'{head} долговой нагрузкой: выручка {rub(inputs["annual_revenue"])} × {inputs["multiplier"]}, '
                f'текущий долг {rub(inputs["current_debt"])}')
    if name == 'collateral':
        return (f'{head} залогом: цена оборудования {rub(inputs["equipment_price"])}, '
                f'собственный взнос {rub(inputs["down_payment"])}')
    if name == 'turnover':
        return f'{head} оборотами: средние поступления {rub(inputs.get("average_monthly_inflows") or 0)} в месяц'
    return f'{head} максимумом продукта'


def _reasons(base: _Variant, term: int, amount: int, offers: list[Offer], policy: CreditPolicy) -> list[Reason]:
    q = base.quote
    reasons = []
    if q.amount < amount:
        binding = q.limit.binding
        c = next(c for c in q.limit.constraints if c.name == binding)
        reasons.append(Reason(code=f'limit_{binding}', staff_text=_limit_staff_text(binding, c.inputs, term),
                              client_text=f'Сумма на {term} месяцев {LIMIT_CLIENT_TEXT[binding]}'))
    if q.amount > 0 and not q.within_cap:
        cap = policy.pricing.rate_cap
        reasons.append(Reason(
            code='rate_above_cap',
            staff_text=f'Ставка {q.price.rate:.2%} с комиссией гарантии {q.guarantee_fee:.0%} выше потолка {cap:.0%}: '
                       f'риск не укладывается в цену (PD {q.price.pd:.2%}, LGD {q.price.lgd})',
            client_text='Риск по заявке слишком высок для наших продуктов на этих условиях.'))
    if not offers and (q.amount == 0 or q.below_product_minimum):
        minimum = policy.products[base.product].min_amount
        reasons.append(Reason(
            code='limit_below_product_minimum',
            staff_text=f'Лимит {rub(q.amount)} меньше минимума продукта {rub(minimum)}',
            client_text=f'Доступная сумма меньше минимальной суммы продукта ({rub(minimum)}).'))
    return reasons


# --- Counterfactuals ------------------------------------------------------------------

def counterfactuals(engine: _Engine, base: _Variant, product: ProductId, term: int,
                    collateral) -> list[Counterfactual]:
    """SPEC §10: what would change the amount at the requested term, each computed by re-running `quote`.
    Only for the constraint that binds; nothing when the request already fits."""
    request, cf, q = engine.request, engine.cf, base.quote
    if q.amount >= request.amount:
        return []
    collateral = tuple(collateral)
    binding = q.limit.binding
    out = []

    if binding == 'cash_flow' and cf.current_payments > 0:
        out += _payments_hints(engine, product, term, collateral, q.amount)
    if binding == 'leverage' and cf.current_debt > 0 and cf.annual_revenue is not None:
        multiplier = policy_multiplier(engine.policy, product)
        required = max(0, int(multiplier * cf.annual_revenue) - request.amount)
        if required < cf.current_debt:
            r, _ = engine.quote(product, term, collateral, request.amount, dataclasses.replace(cf, current_debt=required))
            if r.amount > q.amount:
                out.append(Counterfactual(
                    lever='current_debt', current_value=cf.current_debt, required_value=required,
                    resulting_amount=r.amount, resulting_rate=r.price.rate if r.price else None,
                    client_text=f'При текущем долге до {rub(required)} на {term} месяцев будет доступно до {rub(r.amount)}.'))
    if binding == 'collateral' and request.equipment is not None:
        share = engine.policy.products[product].min_down_payment_share or 0
        e = request.equipment
        required = math.ceil(share * e.price)
        if e.down_payment < required:
            r, _ = engine.quote(product, term, collateral, request.amount,
                                equipment=e.model_copy(update={'down_payment': required}))
            if r.amount > q.amount:
                out.append(Counterfactual(
                    lever='down_payment', current_value=e.down_payment, required_value=required,
                    resulting_amount=r.amount, resulting_rate=r.price.rate if r.price else None,
                    client_text=f'При собственном взносе от {rub(required)} ({share:.0%} цены) '
                                f'будет доступно до {rub(r.amount)}.'))
    return out


def policy_multiplier(policy: CreditPolicy, product: ProductId) -> float:
    return policy.products[product].leverage_multiplier or policy.limit.leverage_multiplier


def _payments_hints(engine: _Engine, product: ProductId, term: int, collateral,
                    current_amount: int) -> list[Counterfactual]:
    """The payment load at which the requested amount fits (SPEC §10's «при платёжной нагрузке до …»);
    if no load is low enough, the amount without the current loans at all."""
    request, cf, score, policy = engine.request, engine.cf, engine.score, engine.policy
    out = []
    # The load that leaves room for the requested amount's payment at the requested amount's own rate.
    rate = price(score.pd, request.amount, term, collateral, engine.route_for(request.amount), policy).rate
    payment = math.ceil(annuity_payment(request.amount, rate, term))
    required = int(cf.cfads / policy.limit.dscr_min[score.grade]) - payment
    if 0 < required < cf.current_payments:
        r, _ = engine.quote(product, term, collateral, request.amount, dataclasses.replace(cf, current_payments=required))
        if r.amount == request.amount and offerable(r):
            out.append(Counterfactual(
                lever='monthly_debt_payments', current_value=cf.current_payments, required_value=required,
                resulting_amount=r.amount, resulting_rate=r.price.rate,
                client_text=f'При платёжной нагрузке по текущим кредитам до {rub(required)}/мес. '
                            f'на {term} месяцев будет доступно {rub(r.amount)}.'))
            return out
    r, _ = engine.quote(product, term, collateral, request.amount, dataclasses.replace(cf, current_payments=0))
    if r.amount > current_amount and r.price is not None:
        out.append(Counterfactual(
            lever='monthly_debt_payments', current_value=cf.current_payments, required_value=0,
            resulting_amount=r.amount, resulting_rate=r.price.rate,
            client_text=f'Если погасить текущие кредиты (платежи {rub(cf.current_payments)}/мес.), '
                        f'на {term} месяцев будет доступно до {rub(r.amount)}.'))
    return out

