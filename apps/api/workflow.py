"""Application workflow v2 (T19, SPEC §13): statuses, allowed transitions, audit log and the automatic run.

    submitted -> enriching -> scoring -> offered | declined | in_review
                 enriching -> declined (company not found) | in_review (required source unavailable)
    in_review -> offered | declined | needs_data   (staff, T20, staff_actions.py)
    in_review -> submitted                         (staff re-runs the automatic run, T20)
    needs_data -> enriching                        (client sent data, T23)
    offered -> accepted -> documents -> signed -> conditions -> disbursed   (client and worker, T21, disbursement.py)
    offered -> expired                             (offer validity ran out)
    any status before signed -> withdrawn          (client)

Every status change goes through `transition`, which refuses moves outside `TRANSITIONS` and writes an
`ApplicationEvent`. The worker claims a `submitted` application by moving it to `enriching`, runs enrichment,
then the pure `decide` (stop factors → scoring → offers → routing) and stores the `Decision`. See WORKFLOW.md.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm.exc import StaleDataError

from apps.api.contracts import (ApplicationRequest, ApplicationStatus as S, DataQuality, Decision, ProductId, Reason,
                                Route)
from apps.api.credit_policy import default_policy as credit_policy
from apps.api.db import ApplicationEvent, LoanApplication, Session, now, uid
from apps.api.fact_store import load_facts, save_facts
from apps.api.features import data_quality_from_responses, features_from_responses
from apps.api.features import default_policy as feature_policy
from apps.api.features.data_quality import default_policy as data_quality_policy
from apps.api.limits import cash_flow_from_responses
from apps.api.offers import OfferSet, generate_offers, requested_product
from apps.api.routing import route_application, route_for
from apps.api.scoring import FeatureSetMismatch, Scorer, default_scorer
from apps.api.stop_factors import evaluate_stop_factors
from apps.sources import Enrichment, RetryPolicy, SourceClient, SourceEvent, enrich, service_from_env
from apps.sources.enrichment import MSK

log = logging.getLogger(__name__)

SYSTEM = 'system'
TRANSITIONS: dict[S, frozenset[S]] = {
    S.DRAFT: frozenset({S.SUBMITTED, S.WITHDRAWN}),
    S.SUBMITTED: frozenset({S.ENRICHING, S.WITHDRAWN}),
    # enriching/scoring -> submitted: an interrupted run goes back to the queue (`fail`).
    S.ENRICHING: frozenset({S.SCORING, S.NEEDS_DATA, S.DECLINED, S.IN_REVIEW, S.SUBMITTED, S.WITHDRAWN}),
    S.SCORING: frozenset({S.OFFERED, S.DECLINED, S.IN_REVIEW, S.NEEDS_DATA, S.SUBMITTED, S.WITHDRAWN}),
    S.NEEDS_DATA: frozenset({S.ENRICHING, S.WITHDRAWN}),
    # in_review -> submitted: staff re-run the automatic run, e.g. once a required source is back (T20).
    S.IN_REVIEW: frozenset({S.OFFERED, S.DECLINED, S.NEEDS_DATA, S.SUBMITTED, S.WITHDRAWN}),
    S.OFFERED: frozenset({S.ACCEPTED, S.EXPIRED, S.WITHDRAWN}),
    S.ACCEPTED: frozenset({S.DOCUMENTS, S.WITHDRAWN}),
    S.DOCUMENTS: frozenset({S.SIGNED, S.WITHDRAWN}),
    S.SIGNED: frozenset({S.CONDITIONS}),
    S.CONDITIONS: frozenset({S.DISBURSED}),
    S.DECLINED: frozenset(),
    S.DISBURSED: frozenset(),
    S.WITHDRAWN: frozenset(),
    S.EXPIRED: frozenset(),
}
TERMINAL = frozenset(s for s, targets in TRANSITIONS.items() if not targets)
WITHDRAWABLE = frozenset(s for s, targets in TRANSITIONS.items() if S.WITHDRAWN in targets)

STATUS_TEXT = {
    S.DRAFT: 'Черновик', S.SUBMITTED: 'Заявка подана', S.ENRICHING: 'Сбор данных из источников',
    S.NEEDS_DATA: 'Нужны дополнительные данные', S.SCORING: 'Расчёт риска и условий',
    S.IN_REVIEW: 'На рассмотрении у сотрудника банка', S.OFFERED: 'Есть предложение', S.DECLINED: 'Отказ',
    S.ACCEPTED: 'Предложение выбрано', S.DOCUMENTS: 'Документы сформированы', S.SIGNED: 'Договор подписан',
    S.CONDITIONS: 'Выполнение отлагательных условий', S.DISBURSED: 'Деньги выданы',
    S.WITHDRAWN: 'Заявка отозвана', S.EXPIRED: 'Срок действия предложения истёк',
}

LEASE_SECONDS = 120  # longer than enrichment with every retry (SPEC §6: 10 s × 3 per source, in parallel)
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5

COMPANY_NOT_FOUND = Reason(code='company_not_found', staff_text='Компания с таким ИНН не найдена в ЕГРЮЛ / ЕГРИП',
                           client_text='Мы не нашли компанию с этим ИНН в госреестре. Проверьте ИНН и подайте заявку заново.')


class IllegalTransition(ValueError):
    pass


def actor_of(user) -> str:
    return f'{"staff" if user.role == "staff" else "client"}:{user.id}'


def today() -> date:
    return datetime.now(MSK).date()


# --- Audit log and transitions ---------------------------------------------------------

def record(db: OrmSession, app: LoanApplication, kind: str, message: str, *, actor: str = SYSTEM,
           data: dict | None = None, from_status: str | None = None, to_status: str | None = None) -> None:
    db.add(ApplicationEvent(application_id=app.id, at=now(), actor=actor, kind=kind, from_status=from_status,
                            to_status=to_status, message=message, data=data))


def transition(db: OrmSession, app: LoanApplication, to: S, *, actor: str = SYSTEM, message: str | None = None,
               data: dict | None = None) -> None:
    """The only way to change a status: checks `TRANSITIONS` and writes the audit event."""
    current = S(app.status)
    if to not in TRANSITIONS[current]:
        raise IllegalTransition(f'{current} → {to} is not allowed')
    app.status, app.updated = to.value, now()
    record(db, app, 'status', message or STATUS_TEXT[to], actor=actor, data=data, from_status=current.value,
           to_status=to.value)


def events(db: OrmSession, application_id: str) -> list[ApplicationEvent]:
    return list(db.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id == application_id)
                           .order_by(ApplicationEvent.id)))


# --- Client actions --------------------------------------------------------------------

def check_request(request: ApplicationRequest) -> None:
    """What the engine can't run without, refused at the door instead of failing in the worker."""
    if requested_product(request) == ProductId.INVESTMENT and request.equipment is None:
        raise ValueError('Для инвестиционного кредита укажите оборудование: цену, взнос и поставщика')


def submit(db: OrmSession, user_id: str, idempotency_key: str, request: dict) -> LoanApplication:
    """A new application from the client's request; `application_id` and `submitted_at` are the server's."""
    existing = db.scalar(select(LoanApplication).where(LoanApplication.idempotency_key == idempotency_key))
    if existing:
        if existing.user_id != user_id:
            raise ValueError('Ключ идемпотентности уже использован')
        return existing
    application_id = uid()
    parsed = ApplicationRequest.model_validate({**request, 'application_id': application_id,
                                                'submitted_at': datetime.now(MSK)})
    check_request(parsed)
    app = LoanApplication(id=application_id, user_id=user_id, idempotency_key=idempotency_key,
                          status=S.SUBMITTED.value, request=parsed.model_dump(mode='json'), attempts=0)
    db.add(app)
    db.flush()
    record(db, app, 'status', STATUS_TEXT[S.SUBMITTED], actor=f'client:{user_id}', to_status=S.SUBMITTED.value)
    return app


def accept(db: OrmSession, app: LoanApplication, offer_id: str, *, actor: str, on: date | None = None) -> None:
    """The client picks one offer (SPEC §6 stage 9). An offer past its validity expires the application."""
    on = on or today()
    if S(app.status) != S.OFFERED:
        raise IllegalTransition('Выбрать предложение можно только в статусе offered')
    offers = {o['offer_id']: o for o in (app.decision or {}).get('offers', [])}
    if offer_id not in offers:
        raise LookupError('Такого предложения нет в решении')
    if date.fromisoformat(offers[offer_id]['valid_until']) < on:
        transition(db, app, S.EXPIRED, message='Срок действия предложения истёк')
        return
    app.selected_offer_id = offer_id
    transition(db, app, S.ACCEPTED, actor=actor, data={'offer_id': offer_id})


def withdraw(db: OrmSession, app: LoanApplication, *, actor: str) -> None:
    if S(app.status) not in WITHDRAWABLE:
        raise IllegalTransition('После подписания или в финальном статусе заявку отозвать нельзя')
    transition(db, app, S.WITHDRAWN, actor=actor, message='Клиент отозвал заявку')


# --- The decision (stages 4–7) -----------------------------------------------------------

def policy_version(data_quality: DataQuality | None) -> str:
    """`Decision.policy_version` is one string, so it names all three policies the decision was built on."""
    dq = data_quality.policy_version if data_quality else data_quality_policy().version
    return f'credit={credit_policy().version};data_quality={dq};features={feature_policy().version}'


def _strictest_offer(offer_set: OfferSet) -> tuple[int, Route] | None:
    """The amount and route of the offer that needs the most authority; the first one on a tie."""
    order = list(Route)
    best = None
    for offer in offer_set.offers:
        route = offer_set.routes[offer.offer_id]
        if best is None or order.index(route) > order.index(best[1]):
            best = (offer.amount, route)
    return best


def decide(request: ApplicationRequest, enrichment: Enrichment, data_quality: DataQuality | None, *,
           as_of: date, decided_at: datetime, scorer: Scorer | None = None) -> Decision:
    """Stop factors → scoring → offers → routing → `Decision`. Pure: same inputs, same decision.

    `as_of` is the day the features and stop factors are computed on; offers are valid from `decided_at`.
    """
    scorer = scorer or default_scorer()
    common = dict(application_id=request.application_id, data_quality=data_quality, requested_documents=[],
                  policy_version=policy_version(data_quality), decided_at=decided_at)
    empty = dict(matches_request=None, offers=[], counterfactuals=[], score=None, challenger=None,
                 model_version=None)

    if enrichment.company_not_found:
        return Decision(status=S.DECLINED, route=Route.AUTO, route_reasons=[], reasons=[COMPANY_NOT_FOUND],
                        stop_factors=[], flags=[], reapply_after=None, **empty, **common)
    if enrichment.unavailable_required:
        routing = route_application(request.amount, grade=None, data_quality=data_quality,
                                    unavailable_sources=enrichment.unavailable_required)
        return Decision(status=S.IN_REVIEW, route=routing.route, route_reasons=list(routing.reasons), reasons=[],
                        stop_factors=[], flags=[], reapply_after=None, **empty, **common)

    checks = evaluate_stop_factors(request, enrichment.responses, as_of)
    flags = list(checks.flags)
    if checks.stop_factors:
        routing = route_application(request.amount, grade=None, data_quality=data_quality,
                                    stop_factors=checks.stop_factors)
        return Decision(status=S.DECLINED, route=routing.route, route_reasons=list(routing.reasons), reasons=[],
                        stop_factors=list(checks.stop_factors), flags=flags, reapply_after=None, **empty, **common)

    features = features_from_responses(enrichment.responses, as_of)
    try:
        scoring = scorer.score(features, request.application_id)
    except FeatureSetMismatch as e:
        log.warning('application %s not scored: %s', request.application_id, e)
        routing = route_application(request.amount, grade=None, flags=flags, data_quality=data_quality)
        return Decision(status=S.IN_REVIEW, route=routing.route, route_reasons=list(routing.reasons), reasons=[],
                        stop_factors=[], flags=flags, reapply_after=None, **empty, **common)

    score = scoring.score
    cf = cash_flow_from_responses(enrichment.responses, data_quality)
    offer_set = generate_offers(request, cf, score, route_for(score.grade, flags, data_quality),
                                decided_at.astimezone(MSK).date())
    # Offers of one application can sit on different routes: the application takes the strictest, so one
    # staff review covers every offer it shows the client.
    strictest = _strictest_offer(offer_set)
    amount = strictest[0] if strictest else request.amount
    routing = route_application(amount, grade=score.grade, flags=flags, data_quality=data_quality)
    # SIMPLIFICATION: THIN goes to the underwriter (`route_data_thin`) instead of `needs_data`: there is no
    # statement upload until T23 (cut list item 5).
    if routing.route == Route.AUTO:
        status = S.OFFERED if offer_set.offers else S.DECLINED
    else:
        status = S.IN_REVIEW  # the offers stay in the Decision for staff to approve or change (T20)
    return Decision(
        status=status, matches_request=offer_set.matches_request, route=routing.route,
        route_reasons=list(routing.reasons), reasons=offer_set.reasons, stop_factors=[], flags=flags, score=score,
        challenger=scoring.challenger, offers=offer_set.offers, counterfactuals=offer_set.counterfactuals,
        reapply_after=offer_set.reapply_after if status == S.DECLINED else None, model_version=score.model_version,
        **common)


# --- The automatic run (worker) ----------------------------------------------------------

@dataclass(frozen=True)
class Pipeline:
    """What the automatic run needs from outside: sources, model and clocks."""
    client: SourceClient
    as_of: date  # features and stop factors are computed as of this day
    scorer: Scorer
    retry: RetryPolicy
    clock: Callable[[], datetime] = lambda: datetime.now(MSK)


@lru_cache(maxsize=1)
def default_pipeline() -> Pipeline:
    service = service_from_env()
    # MOCK: the synthetic world is a snapshot frozen at its own `as_of` (WORLD_AS_OF), so the features are
    # computed on that day, not today; otherwise business age and the inquiry windows drift away from the
    # data. With a real source client this is the day the sources answered (`snapshot_date`).
    return Pipeline(client=service, as_of=service.world.as_of, scorer=default_scorer(), retry=RetryPolicy.from_env())


# SIMPLIFICATION: the queue is the `status` column, polled by the in-process worker (worker.py); a bank would
# use a message broker or a workflow engine with its own retries and dead-letter queue.
def claim_next() -> str | None:
    """Move the oldest due `submitted` application to `enriching`; the status change is the claim."""
    try:
        with Session.begin() as db:
            app = db.scalar(select(LoanApplication).where(
                LoanApplication.status == S.SUBMITTED.value,
                (LoanApplication.lease_until.is_(None)) | (LoanApplication.lease_until <= time.time()),
            ).order_by(LoanApplication.created).limit(1))
            if app is None:
                return None
            app.attempts += 1
            app.lease_until = time.time() + LEASE_SECONDS
            transition(db, app, S.ENRICHING, data={'attempt': app.attempts})
            return app.id
    except StaleDataError:  # another worker claimed it first
        return None


def _current(db: OrmSession, application_id: str, expected: S) -> LoanApplication | None:
    """The application if it is still where the run left it; a client may have withdrawn it meanwhile."""
    app = db.get(LoanApplication, application_id)
    if app is None or S(app.status) != expected:
        log.info('application %s moved on to %s; run result dropped', application_id, app and app.status)
        return None
    return app


SOURCE_EVENT_TEXT = {'started': 'запрос', 'ok': 'ответ получен', 'timeout': 'нет ответа', 'error': 'ошибка',
                     'not_found': 'компания не найдена', 'no_consent': 'нет согласия'}


def _log_source_event(application_id: str) -> Callable[[SourceEvent], None]:
    """Each source step becomes an audit event at once, so the "analyzing" screen (T27) can poll them."""
    def handler(e: SourceEvent) -> None:
        with Session.begin() as db:
            db.add(ApplicationEvent(
                application_id=application_id, at=now(), actor=SYSTEM, kind='source',
                message=f'{e.source.value}: {SOURCE_EVENT_TEXT[e.status]} (попытка {e.attempt})',
                data={'source': e.source.value, 'attempt': e.attempt, 'status': e.status, 'final': e.final,
                      'error': e.error}))
    return handler


def run_application(application_id: str, pipeline: Pipeline) -> None:
    """Stages 3–7 for one claimed application: enrich, store facts and data quality, decide, store."""
    with Session() as db:
        request = ApplicationRequest.model_validate(db.get(LoanApplication, application_id).request)
    enrichment = asyncio.run(enrich(request, pipeline.client, policy=pipeline.retry,
                                    on_event=_log_source_event(application_id), now=pipeline.clock))
    dq = None if enrichment.company_not_found else \
        data_quality_from_responses(enrichment.responses, request.declared_annual_revenue)

    with Session.begin() as db:
        if (app := _current(db, application_id, S.ENRICHING)) is None:
            return
        save_facts(db, application_id, enrichment.facts)
        app.enrichment = enrichment.model_dump(mode='json', exclude={'facts'})
        app.data_quality = dq.model_dump(mode='json') if dq else None
        record(db, app, 'action', 'Данные из источников собраны' + (f', уровень данных {dq.tier}' if dq else ''),
               data={'unavailable_required': enrichment.unavailable_required, 'degraded': enrichment.degraded,
                     'company_not_found': enrichment.company_not_found, 'tier': dq.tier if dq else None})
        if enrichment.complete:
            app.lease_until = time.time() + LEASE_SECONDS
            transition(db, app, S.SCORING)

    decision = decide(request, enrichment, dq, as_of=pipeline.as_of, decided_at=pipeline.clock(),
                      scorer=pipeline.scorer)
    with Session.begin() as db:
        if (app := _current(db, application_id, S.SCORING if enrichment.complete else S.ENRICHING)) is None:
            return
        store_decision(db, app, decision)


def store_decision(db: OrmSession, app: LoanApplication, decision: Decision, *, actor: str = SYSTEM) -> None:
    app.decision = decision.model_dump(mode='json')
    app.lease_until = None
    app.offer_valid_until = max(o.valid_until for o in decision.offers).isoformat() \
        if decision.status == S.OFFERED else None
    record(db, app, 'decision', '; '.join(r.staff_text for r in decision.route_reasons) or
           '; '.join(r.staff_text for r in decision.reasons) or 'Решение принято', actor=actor, data={
               'status': decision.status, 'route': decision.route,
               'grade': decision.score.grade if decision.score else None,
               'pd': decision.score.pd if decision.score else None, 'model_version': decision.model_version,
               'policy_version': decision.policy_version, 'offers': [o.offer_id for o in decision.offers],
               'stop_factors': [r.code for r in decision.stop_factors], 'flags': [r.code for r in decision.flags]})
    transition(db, app, decision.status, actor=actor)


def fail(application_id: str, error: str) -> None:
    """An interrupted run goes back to the queue; after `MAX_ATTEMPTS` it goes to staff without a decision."""
    with Session.begin() as db:
        app = db.get(LoanApplication, application_id)
        if app is None or S(app.status) not in (S.ENRICHING, S.SCORING):
            return
        record(db, app, 'error', f'Автоматический расчёт прерван: {error}', data={'attempt': app.attempts})
        if app.attempts < MAX_ATTEMPTS:
            app.lease_until = time.time() + RETRY_DELAY_SECONDS * app.attempts
            transition(db, app, S.SUBMITTED, message='Повтор автоматического расчёта')
        else:
            app.lease_until = None
            transition(db, app, S.IN_REVIEW, message='Автоматический расчёт не завершён: заявку проверит сотрудник')


def recover_stale() -> int:
    """Runs whose worker died: the lease ran out while the application sat in enriching or scoring."""
    with Session() as db:
        stale = list(db.scalars(select(LoanApplication.id).where(
            LoanApplication.status.in_([S.ENRICHING.value, S.SCORING.value]),
            LoanApplication.lease_until < time.time())))
    for application_id in stale:
        fail(application_id, 'истекло время обработки')
    return len(stale)


def expire_offers(on: date | None = None) -> int:
    """offered → expired once the last offer's validity (`Offer.valid_until`) is behind."""
    on = on or today()
    with Session.begin() as db:
        due = list(db.scalars(select(LoanApplication).where(LoanApplication.status == S.OFFERED.value,
                                                            LoanApplication.offer_valid_until < on.isoformat())))
        for app in due:
            transition(db, app, S.EXPIRED)
    return len(due)


def run_next(pipeline: Pipeline | None = None) -> bool:
    """Claim and run one application; False when the queue is empty."""
    application_id = claim_next()
    if application_id is None:
        return False
    try:
        run_application(application_id, pipeline or default_pipeline())
    except Exception as e:  # the run is retried; the client never sees a crashed conveyor
        log.exception('application %s run failed', application_id)
        fail(application_id, e.__class__.__name__)
    return True


def tick(pipeline: Pipeline | None = None) -> None:
    """One worker step: recover dead runs, expire old offers, run one application."""
    recover_stale()
    expire_offers()
    run_next(pipeline)


# --- Views -------------------------------------------------------------------------------

def _distinct(texts: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(texts))


def client_decision(decision: dict) -> dict:
    """What the client may see: no staff texts, flags, scores, challenger or expected profit (SPEC §1.11)."""
    d = Decision.model_validate(decision)
    dq = d.data_quality
    return {
        'status': d.status, 'matches_request': d.matches_request,
        'route_messages': _distinct(r.client_text for r in d.route_reasons),
        'reasons': _distinct(r.client_text for r in [*d.stop_factors, *d.reasons]),
        'offers': [o.model_dump(mode='json', exclude={'expected_profit'}) for o in d.offers],
        'counterfactuals': [c.model_dump(mode='json') for c in d.counterfactuals],
        'data_quality': {'tier': dq.tier, 'client_message': dq.client_message} if dq else None,
        'requested_documents': d.requested_documents,
        'reapply_after': d.reapply_after.isoformat() if d.reapply_after else None,
        'decided_at': d.decided_at.isoformat(),
    }


def event_json(e: ApplicationEvent) -> dict:
    return {'id': e.id, 'at': e.at, 'actor': e.actor, 'kind': e.kind, 'from_status': e.from_status,
            'to_status': e.to_status, 'message': e.message, 'data': e.data}


def application_json(db: OrmSession, app: LoanApplication, *, staff: bool) -> dict:
    """Staff see everything stored; the client sees the request, status changes, source progress and
    `client_decision`."""
    base = {'id': app.id, 'status': app.status, 'status_text': STATUS_TEXT[S(app.status)],
            'revision': app.revision, 'request': app.request, 'created': app.created, 'updated': app.updated,
            'offer_valid_until': app.offer_valid_until, 'selected_offer_id': app.selected_offer_id}
    history = events(db, app.id)
    from apps.api.disbursement import closing_json  # disbursement imports this module
    base['closing'] = closing_json(app.closing, staff=staff)
    if not staff:
        return {**base, 'decision': client_decision(app.decision) if app.decision else None,
                'events': [{'at': e.at, 'status': e.to_status, 'message': e.message}
                           for e in history if e.kind == 'status'],
                # per-source progress for the "analyzing" screen (T27); error texts stay with staff
                'sources': [{'at': e.at, **{k: e.data[k] for k in ('source', 'attempt', 'status', 'final')}}
                            for e in history if e.kind == 'source']}
    sources = [{k: r[k] for k in ('source', 'status', 'requested_at', 'received_at', 'latency_ms', 'attempts', 'error')}
               for r in (app.enrichment or {}).get('responses', [])]
    return {**base, 'decision': app.decision, 'data_quality': app.data_quality, 'sources': sources,
            'facts': [f.model_dump(mode='json') for f in load_facts(db, app.id)], 'attempts': app.attempts,
            'pending_review': app.pending_review, 'events': [event_json(e) for e in history]}
