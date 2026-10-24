"""Staff actions on an application in review (T20, SPEC §11): approve, change the offer, decline, request
data, re-run the automatic run. Every action needs a reason code and a comment.

    approve | change  -> offered     each offer is re-routed by its amount: an underwriter approves only within
                                     the underwriter's authority; a committee amount needs two signatures
    decline           -> declined
    request_data      -> needs_data
    rerun             -> submitted   e.g. a required source is back (`route_source_unavailable`)

A committee action is first a proposal (`LoanApplication.pending_review`): it applies once an underwriter and a
risk manager, two different people, have both signed it. Every action, proposal and signature goes to the audit
log with its reason code, comment and whether staff overrode the system. See STAFF_ACTIONS.md.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from apps.api.contracts import (ApplicationRequest, ApplicationStatus as S, CollateralType, DataQuality, Decision,
                                Offer, ProductId, Reason, Route)
from apps.api.db import ApplicationEvent, LoanApplication
from apps.api.limits import cash_flow_from_responses
from apps.api.offers import (MAX_OFFERS, OFFER_VALID_DAYS, PRODUCT_NAMES, REAPPLY_AFTER_DAYS, offerable,
                             quote_on_route, requested_product, rub, standard_collateral)
from apps.api.routing import Routing, route_application, route_for
from apps.api.workflow import IllegalTransition, policy_version, record, store_decision, transition
from apps.sources import Enrichment
from apps.sources.enrichment import MSK


class Position(StrEnum):
    UNDERWRITER = 'underwriter'
    RISK_MANAGER = 'risk_manager'


POSITION_TEXT = {Position.UNDERWRITER: 'андеррайтер', Position.RISK_MANAGER: 'риск-менеджер'}
COMMITTEE = frozenset(Position)  # SPEC §15: the committee is two signatures, an underwriter's and a risk manager's


class Action(StrEnum):
    APPROVE = 'approve'
    CHANGE = 'change'
    DECLINE = 'decline'
    REQUEST_DATA = 'request_data'
    RERUN = 'rerun'


DECISIONS = frozenset({Action.APPROVE, Action.CHANGE, Action.DECLINE})  # the ones counted for overrides

# SIMPLIFICATION: reason codes are code constants; in a bank they are a versioned directory of the credit
# policy. Moving them to config/ is a shared-folder PR (docs/v2/NOTES.md, Unassigned).
REASON_CODES: dict[Action, dict[str, str]] = {
    Action.APPROVE: {
        'confirmed_by_review': 'Проверка подтвердила расчёт системы',
        'flag_explained': 'Флаг проверен и объяснён',
        'data_verified': 'Данные клиента подтверждены вручную',
    },
    Action.CHANGE: {
        'risk_not_in_model': 'Риск, который модель не учитывает',
        'term_for_cash_flow': 'Срок подобран под денежный поток',
        'collateral_added': 'Добавлено обеспечение',
        'product_changed': 'Подобран другой продукт',
        'client_request': 'По просьбе клиента',
    },
    Action.DECLINE: {
        'fraud_suspected': 'Подозрение на мошенничество',
        'aml_risk': 'Риск ПОД/ФТ (115-ФЗ)',
        'business_risk': 'Риск бизнеса выше допустимого',
        'data_unreliable': 'Данные клиента недостоверны',
        'repayment_capacity': 'Недостаточно денежного потока для погашения',
    },
    Action.REQUEST_DATA: {
        'statement_needed': 'Нужна выписка по счёту в другом банке',
        'documents_needed': 'Нужны подтверждающие документы',
        'revenue_unconfirmed': 'Выручка не подтверждена',
    },
    Action.RERUN: {
        'source_restored': 'Обязательный источник снова доступен',
        'technical_failure': 'Повтор после технического сбоя',
    },
}
REJECT_REASON_CODES = {  # the committee's second signer turns a proposal down
    'terms_disagreed': 'Не согласен с условиями',
    'more_review_needed': 'Нужна дополнительная проверка',
    'authority_exceeded': 'Решение вне полномочий комитета',
}
MIN_COMMENT = 10

# The client sees only that staff reviewed the application, never the reason code: it may be AML (SPEC §1.11).
CLIENT_TEXT = {
    Action.APPROVE: 'Условия проверены и подтверждены специалистом банка.',
    Action.CHANGE: 'Условия подобраны специалистом банка.',
    Action.DECLINE: 'По результатам рассмотрения специалистом банка мы не можем сейчас предложить финансирование.',
    Action.REQUEST_DATA: 'Чтобы принять решение, специалисту банка нужны дополнительные документы.',
}


class StaffActionError(ValueError):
    """The action can't be done as asked (bad reason code, offer over the limit, …): HTTP 422."""


@dataclass(frozen=True)
class Staff:
    user_id: str
    position: Position

    @property
    def actor(self) -> str:
        return f'staff:{self.user_id}'


def staff_of(user) -> Staff:
    """Staff who logged in before T20 have no position: they are underwriters."""
    return Staff(user.id, Position(user.staff_position or Position.UNDERWRITER))


@dataclass(frozen=True)
class OfferChange:
    """A staff-built offer: re-priced by the same `quote` as the engine's offers."""
    amount: int
    term_months: int
    product: ProductId | None = None  # the requested product when None
    collateral: Sequence[CollateralType] | None = None  # the product's standard collateral when None


def reason_codes() -> dict:
    return {'actions': {a.value: codes for a, codes in REASON_CODES.items()}, 'reject': REJECT_REASON_CODES}


# --- Actions -----------------------------------------------------------------------------------

def review(db: OrmSession, app: LoanApplication, staff: Staff, action: Action, reason_code: str, comment: str, *,
           offer_ids: Sequence[str] | None = None, changes: Sequence[OfferChange] = (),
           documents: Sequence[str] = (), at: datetime | None = None) -> str:
    """One staff action on an `in_review` application. Returns `applied`, or `proposed` when the action
    needs the committee and waits for the second signature (`sign`)."""
    at = at or datetime.now(MSK)
    action = Action(action)
    if S(app.status) != S.IN_REVIEW:
        raise IllegalTransition('Действия сотрудника доступны только в статусе in_review')
    if app.pending_review:
        raise IllegalTransition('Решение комитета ждёт второй подписи: подпишите или отклоните его')
    label = _check_reason(REASON_CODES[action], reason_code)
    comment = _check_comment(comment)
    base = Decision.model_validate(app.decision) if app.decision else None
    note = {'action': action.value, 'reason_code': reason_code, 'comment': comment}

    if action == Action.RERUN:
        if base and base.score is not None:
            raise StaffActionError('Заявка уже рассчитана: одобрите, измените или отклоните её')
        app.attempts, app.lease_until = 0, None
        record(db, app, 'review', f'{label}: {comment}', actor=staff.actor,
               data={**note, 'override': None, 'position': staff.position.value})
        transition(db, app, S.SUBMITTED, actor=staff.actor, message='Повторный автоматический расчёт')
        return 'applied'

    reason = Reason(code=f'staff_{reason_code}', staff_text=f'{label} ({POSITION_TEXT[staff.position]}): {comment}',
                    client_text=CLIENT_TEXT[action])
    if action == Action.APPROVE:
        offers = _kept_offers(base, offer_ids)
        override = {o.offer_id for o in offers} != {o.offer_id for o in base.offers}
    elif action == Action.CHANGE:
        offers = _changed_offers(app, base, changes, at)
        override = True
    else:
        offers, override = [], bool(base and base.offers) if action == Action.DECLINE else None

    if offers:
        routing = _routing(base, offers)
        decision = _offered(base, offers, routing, reason, at)
    elif action == Action.DECLINE:
        decision = _closed(app, base, S.DECLINED, reason, at, reapply_after=at.date() + timedelta(days=REAPPLY_AFTER_DAYS))
    else:
        documents = [d.strip() for d in documents if d.strip()]
        if not documents:
            raise StaffActionError('Перечислите документы, которые нужны от клиента')
        decision = _closed(app, base, S.NEEDS_DATA, reason, at, requested_documents=documents)

    note = {**note, 'override': override, 'route': decision.route.value}
    if offers and decision.route == Route.COMMITTEE:
        signature = _signature(staff, at)
        app.pending_review = {**note, 'decision': decision.model_dump(mode='json'), 'signatures': [signature]}
        record(db, app, 'proposal', f'Решение на комитет: {label}: {comment}', actor=staff.actor,
               data={**note, 'offers': [o.offer_id for o in offers], 'signatures': [signature]})
        return 'proposed'
    _apply(db, app, decision, staff, f'{label}: {comment}', {**note, 'signatures': [_signature(staff, at)]}, at)
    return 'applied'


def sign(db: OrmSession, app: LoanApplication, staff: Staff, *, approve: bool, comment: str,
         reason_code: str | None = None, at: datetime | None = None) -> str:
    """The second committee signature: applies the proposal, or turns it down with a reason code."""
    at = at or datetime.now(MSK)
    if S(app.status) != S.IN_REVIEW or not app.pending_review:
        raise IllegalTransition('Нет решения, которое ждёт подписи комитета')
    comment = _check_comment(comment)
    pending = app.pending_review
    if not approve:
        label = _check_reason(REJECT_REASON_CODES, reason_code)
        app.pending_review = None
        record(db, app, 'signature', f'Решение комитета отклонено: {label}: {comment}', actor=staff.actor,
               data={'approve': False, 'reason_code': reason_code, 'comment': comment,
                     'position': staff.position.value, 'proposal': pending['action']})
        return 'rejected'

    signatures = pending['signatures']
    if staff.user_id in {s['user_id'] for s in signatures}:
        raise StaffActionError('Вторую подпись ставит другой сотрудник')
    missing = COMMITTEE - {Position(s['position']) for s in signatures}
    if staff.position not in missing:
        raise StaffActionError(f'Вторую подпись ставит {", ".join(POSITION_TEXT[p] for p in missing)}')
    signature = {**_signature(staff, at), 'comment': comment}
    record(db, app, 'signature', f'Вторая подпись комитета: {comment}', actor=staff.actor,
           data={'approve': True, 'comment': comment, 'position': staff.position.value, 'proposal': pending['action']})
    app.pending_review = None
    note = {k: pending[k] for k in ('action', 'reason_code', 'comment', 'override', 'route')}
    label = REASON_CODES[Action(pending['action'])][pending['reason_code']]
    _apply(db, app, Decision.model_validate(pending['decision']), staff, f'Комитет: {label}: {pending["comment"]}',
           {**note, 'signatures': [*signatures, signature]}, at)
    return 'applied'


def override_stats(db: OrmSession) -> dict:
    """How often staff disagree with the system (SPEC §11): over every applied approve / change / decline.

    Agreeing is approving every offer the system computed, or declining when it had none. Approving only some
    offers, changing them, or declining when there were offers is an override."""
    rows = [e.data for e in db.scalars(select(ApplicationEvent).where(ApplicationEvent.kind == 'review'))
            if e.data and e.data.get('action') in DECISIONS]
    by_action = {a.value: {'decisions': 0, 'overrides': 0} for a in Action if a in DECISIONS}
    for r in rows:
        by_action[r['action']]['decisions'] += 1
        by_action[r['action']]['overrides'] += bool(r['override'])
    overrides = sum(bool(r['override']) for r in rows)
    return {'decisions': len(rows), 'overrides': overrides,
            'override_rate': round(overrides / len(rows), 4) if rows else None, 'by_action': by_action,
            'override_reasons': dict(Counter(r['reason_code'] for r in rows if r['override']))}


# --- Building the decision ---------------------------------------------------------------------

def _check_reason(codes: dict[str, str], code: str | None) -> str:
    if code not in codes:
        raise StaffActionError(f'Укажите код причины: {", ".join(codes)}')
    return codes[code]


def _check_comment(comment: str) -> str:
    comment = (comment or '').strip()
    if len(comment) < MIN_COMMENT:
        raise StaffActionError(f'Комментарий обязателен: не короче {MIN_COMMENT} символов')
    return comment


def _signature(staff: Staff, at: datetime) -> dict:
    return {'user_id': staff.user_id, 'position': staff.position.value, 'at': at.isoformat()}


def _kept_offers(base: Decision | None, offer_ids: Sequence[str] | None) -> list[Offer]:
    if base is None or not base.offers:
        raise StaffActionError('Система не рассчитала предложений: измените условия или отклоните заявку')
    if offer_ids is None:
        return list(base.offers)
    known = {o.offer_id: o for o in base.offers}
    if not offer_ids or set(offer_ids) - set(known):
        raise StaffActionError('Выберите предложения из решения системы')
    return [o for o in base.offers if o.offer_id in set(offer_ids)]


def _changed_offers(app: LoanApplication, base: Decision | None, changes: Sequence[OfferChange],
                    at: datetime) -> list[Offer]:
    """Re-price each change with `quote` at the route of its own amount. The amount can't go over the limit:
    `Offer.amount <= limit.limit` is a contract rule."""
    if base is None or base.score is None or app.enrichment is None or base.data_quality is None:
        raise StaffActionError('Без скоринга условия не рассчитать: отклоните заявку или запросите данные')
    if not 1 <= len(changes) <= MAX_OFFERS:
        raise StaffActionError(f'Укажите от 1 до {MAX_OFFERS} предложений')
    request = ApplicationRequest.model_validate(app.request)
    responses = Enrichment.model_validate({**app.enrichment, 'facts': []}).responses
    cf = cash_flow_from_responses(responses, base.data_quality)
    routes = route_for(base.score.grade, base.flags, base.data_quality)
    offers = []
    for i, change in enumerate(changes, 1):
        product = change.product or requested_product(request)
        if product == ProductId.INVESTMENT and request.equipment is None:
            raise StaffActionError('Для инвестиционного кредита в заявке нет оборудования')
        collateral = list(change.collateral) if change.collateral is not None \
            else standard_collateral(product, request.legal_form)
        try:
            q, _ = quote_on_route(request, cf, base.score, routes, product, change.term_months, collateral,
                                  change.amount)
        except ValueError as e:
            raise StaffActionError(f'Предложение {i}: {e}') from e
        if q.amount < change.amount:
            raise StaffActionError(f'Предложение {i}: сумма выше лимита, доступно до {rub(q.amount)}')
        if q.below_product_minimum:
            raise StaffActionError(f'Предложение {i}: сумма меньше минимума продукта')
        if not offerable(q):
            raise StaffActionError(f'Предложение {i}: ставка {q.price.rate:.2%} выше потолка, риск не оценить ценой')
        offers.append(_offer(app.id, request, i, product, change.term_months, collateral, q, at))
    return offers


def _offer(application_id: str, request: ApplicationRequest, i: int, product: ProductId, term: int,
           collateral: list[CollateralType], q, at: datetime) -> Offer:
    requested = requested_product(request)
    matches = product == requested and q.amount == request.amount and term == request.term_months
    if product != requested:
        kind = 'other_product'
    elif CollateralType.MSP_GUARANTEE in collateral:
        kind = 'with_collateral'
    elif q.amount == request.amount:
        kind = 'as_requested' if term == request.term_months else 'longer_term'
    else:
        kind = 'max_amount'
    tradeoff = f'Условия специалиста банка: {rub(q.amount)} на {term} мес.'
    if product != requested:
        tradeoff += f', {PRODUCT_NAMES[product].lower()}'
    return Offer(
        offer_id=f'{application_id}-staff-{i}', kind=kind, product=product, amount=q.amount, term_months=term,
        rate=q.price.rate, monthly_payment=q.monthly_payment, collateral=collateral, guarantee_fee=q.guarantee_fee,
        disbursement='supplier' if product == ProductId.INVESTMENT else 'client_account', matches_request=matches,
        tradeoff=tradeoff, limit=q.limit, price=q.price, expected_profit=q.expected_profit,
        valid_until=at.date() + timedelta(days=OFFER_VALID_DAYS))


def _routing(base: Decision, offers: list[Offer]) -> Routing:
    """The authority the approved offers need: the strictest route among them, as in `decide`."""
    def route(amount: int) -> Routing:
        return route_application(amount, grade=base.score.grade if base.score else None, flags=base.flags,
                                 data_quality=base.data_quality)
    return max((route(o.amount) for o in offers), key=lambda r: list(Route).index(r.route))


def _offered(base: Decision, offers: list[Offer], routing: Routing, reason: Reason, at: datetime) -> Decision:
    return Decision(**{**dict(base), 'status': S.OFFERED, 'offers': offers, 'route': routing.route,
                       'route_reasons': list(routing.reasons), 'reasons': [*base.reasons, reason],
                       'matches_request': any(o.matches_request for o in offers), 'requested_documents': [],
                       'reapply_after': None, 'decided_at': at})


def _closed(app: LoanApplication, base: Decision | None, status: S, reason: Reason, at: datetime, *,
            reapply_after=None, requested_documents=()) -> Decision:
    """A decline or a data request, also for an application the system never decided."""
    if base is None:
        dq = DataQuality.model_validate(app.data_quality) if app.data_quality else None
        base = Decision(application_id=app.id, status=S.IN_REVIEW, matches_request=None, route=Route.UNDERWRITER,
                        route_reasons=[], reasons=[], stop_factors=[], flags=[], score=None, challenger=None,
                        data_quality=dq, offers=[], counterfactuals=[], requested_documents=[], reapply_after=None,
                        model_version=None, policy_version=policy_version(dq), decided_at=at)
    return Decision(**{**dict(base), 'status': status, 'offers': [], 'matches_request': None,
                       'reasons': [*base.reasons, reason], 'requested_documents': list(requested_documents),
                       'reapply_after': reapply_after, 'decided_at': at})


def _apply(db: OrmSession, app: LoanApplication, decision: Decision, staff: Staff, message: str, data: dict,
           at: datetime) -> None:
    # Offers are valid from the day staff approve them, not from the engine's run (or the proposal).
    valid_until = at.date() + timedelta(days=OFFER_VALID_DAYS)
    decision = Decision(**{**dict(decision), 'decided_at': at,
                           'offers': [o.model_copy(update={'valid_until': valid_until}) for o in decision.offers]})
    record(db, app, 'review', message, actor=staff.actor, data={**data, 'position': staff.position.value})
    store_decision(db, app, decision, actor=staff.actor)
