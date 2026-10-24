"""Overdraft pre-approval (T22, SPEC §4, §5.1, §6 stage 0): the nightly "Вам доступно до X" for the bank's clients.

For every client the batch runs the same stages as an application, with no application:

    enrichment (the same adapters) -> data quality without a declared revenue: FULL only
    -> stop factors and flags: none -> score (champion only) -> 12-month overdraft quote at the auto route
    -> the amount's own route must be auto, the amount at least the product minimum, the rate under the cap

Anything else is "no pre-approval", with the reason kept for staff. A pre-approval is a soft offer: the
client still applies, and the application is decided again on that day's data (workflow.py). `preapprove`
is pure: same inputs, same result. The batch stores the latest result per ИНН in `preapprovals`. See
PREAPPROVAL.md.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from apps.api.contracts import (ApplicationRequest, DataQualityTier, EgrulPayload, Grade, LegalForm, ProductId,
                                Reason, Route, SourceId)
from apps.api.db import PreApprovalRow, Session
from apps.api.features import data_quality_from_responses, features_from_responses
from apps.api.limits import cash_flow_from_responses, quote
from apps.api.offers import rub
from apps.api.routing import route_application
from apps.api.scoring import FeatureSetMismatch, Scorer, default_scorer
from apps.api.stop_factors import evaluate_stop_factors
from apps.api.workflow import Pipeline, default_pipeline, policy_version
from apps.sources import Enrichment, MockSourceService, enrich
from apps.sources.enrichment import MSK
from apps.sources.service import SourceNotFound

log = logging.getLogger(__name__)

PRODUCT = ProductId.OVERDRAFT
TERM_MONTHS = 12  # the overdraft limit's period (SPEC §4)
# SIMPLIFICATION: validity and the batch period are code constants, like the offer values of T17; the
# next nightly run replaces every result anyway.
VALID_DAYS = 30
BATCH_EVERY_SECONDS = 24 * 3600
CONCURRENCY = 20  # clients enriched at once

NO_OFFER_TEXT = 'Предодобренного предложения пока нет: подайте заявку, и мы рассмотрим её как обычно.'


class PreApproval(BaseModel):
    """One client's pre-approval. Not a contract: only this module and its endpoint produce it."""
    model_config = ConfigDict(extra='forbid', frozen=True)

    inn: str
    approved: bool
    product: ProductId
    term_months: int
    amount: int | None  # the limit rounded down to the amount step; None without a pre-approval
    rate: float | None
    binding: str | None  # the limit constraint that set the amount
    grade: Grade | None
    data_quality_tier: DataQualityTier | None
    reasons: list[Reason]  # why not; for a pre-approval, the binding limit
    as_of: date  # the data the result is computed on
    computed_at: datetime
    valid_until: date | None
    model_version: str | None
    policy_version: str

    def client_view(self) -> dict | None:
        """What the banner (T29) shows: nothing without a valid pre-approval."""
        if not self.approved or self.valid_until is None:
            return None
        return {'product': self.product.value, 'term_months': self.term_months, 'amount': self.amount,
                'rate': self.rate, 'valid_until': self.valid_until.isoformat(),
                'client_text': f'Вам доступно до {rub(self.amount)}: овердрафт без залога, решение сразу'}


def _reason(code: str, staff_text: str) -> Reason:
    return Reason(code=f'preapproval_{code}', staff_text=staff_text, client_text=NO_OFFER_TEXT)


def standing_request(inn: str, egrul: EgrulPayload, given_at: datetime) -> ApplicationRequest:
    """The request the batch runs on: a 12-month overdraft at the product minimum, nothing declared.

    MOCK: the client gave a standing 218-ФЗ consent for the bureau when opening the account, for the
    company and its main owner by ЕГРЮЛ. A bank keeps a register of such consents with their own dates and
    scope; this prototype has none, so the consent is assumed. The real application still collects
    consent (T25) and is decided on its own.
    """
    if egrul.legal_form == LegalForm.IP:
        consents = [('bki_owner', inn)]
    else:
        owner = max(egrul.founders, key=lambda f: f.share).inn if egrul.founders else egrul.director.inn
        consents = [('bki_company', inn), ('bki_owner', owner)]
    return ApplicationRequest.model_validate(dict(
        application_id=f'preapproval-{inn}', inn=inn, legal_form=egrul.legal_form.value, purpose='cash_gap',
        amount=100_000, term_months=TERM_MONTHS, needed_by='later', preferred_product=PRODUCT.value,
        declared_annual_revenue=None, equipment=None, submitted_at=None,
        consents=[{'kind': kind, 'subject_inn': subject, 'given_at': given_at} for kind, subject in consents]))


def preapprove(request: ApplicationRequest, enrichment: Enrichment, *, as_of: date, computed_at: datetime,
               scorer: Scorer | None = None) -> PreApproval:
    """Data quality → stop factors → score → overdraft quote → route. Pure: same inputs, same result."""
    scorer = scorer or default_scorer()
    common = dict(inn=request.inn, product=PRODUCT, term_months=TERM_MONTHS, as_of=as_of, computed_at=computed_at)

    def no(reason: Reason, *, dq=None, grade=None, model_version=None) -> PreApproval:
        return PreApproval(approved=False, amount=None, rate=None, binding=None, grade=grade,
                           data_quality_tier=dq.tier if dq else None, reasons=[reason], valid_until=None,
                           model_version=model_version, policy_version=policy_version(dq), **common)

    if enrichment.company_not_found:
        return no(_reason('company_not_found', 'Компания не найдена в ЕГРЮЛ / ЕГРИП'))
    if enrichment.unavailable_required:
        names = ', '.join(s.value for s in enrichment.unavailable_required)
        return no(_reason('source_unavailable', f'Не ответили обязательные источники: {names}'))

    # No application, so no declared revenue: without ФНС revenue the coverage is null and the tier THIN.
    dq = data_quality_from_responses(enrichment.responses)
    if dq.tier != DataQualityTier.FULL:
        return no(_reason(f'data_{dq.tier.value.lower()}',
                          f'Уровень данных {dq.tier.value}: предодобрение только при FULL'), dq=dq)

    checks = evaluate_stop_factors(request, enrichment.responses, as_of)
    if checks.stop_factors:
        codes = ', '.join(r.code for r in checks.stop_factors)
        return no(_reason('stop_factor', f'Стоп-факторы: {codes}'), dq=dq)
    if checks.flags:
        codes = ', '.join(r.code for r in checks.flags)
        return no(_reason('flag', f'Флаги: {codes}; предодобрение только без флагов'), dq=dq)

    try:
        score = scorer.score(features_from_responses(enrichment.responses, as_of), request.application_id).score
    except FeatureSetMismatch as e:
        log.warning('pre-approval for %s not scored: %s', request.inn, e)
        return no(_reason('not_scored', 'Скоринговая карта не подходит к признакам'), dq=dq)
    scored = dict(dq=dq, grade=score.grade, model_version=score.model_version)

    cf = cash_flow_from_responses(enrichment.responses, dq)
    q = quote(cf, score, PRODUCT, TERM_MONTHS, Route.AUTO, requested=None)
    if q.amount == 0 or q.below_product_minimum:
        return no(_reason('below_product_minimum',
                          f'Лимит {rub(q.amount)} ниже минимума продукта (ограничение {q.limit.binding})'), **scored)
    if not q.within_cap:
        return no(_reason('rate_above_cap', f'Ставка {q.price.rate:.2%} выше потолка'), **scored)
    route = route_application(q.amount, grade=score.grade, flags=(), data_quality=dq)
    if route.route != Route.AUTO:
        codes = ', '.join(r.code for r in route.reasons)
        return no(_reason('not_auto', f'Маршрут {route.route.value}, не автоматический: {codes}'), **scored)

    binding = next(c for c in q.limit.constraints if c.name == q.limit.binding)
    return PreApproval(
        approved=True, amount=q.amount, rate=q.price.rate, binding=q.limit.binding, grade=score.grade,
        data_quality_tier=dq.tier, model_version=score.model_version, policy_version=policy_version(dq),
        reasons=[Reason(code=f'preapproval_limit_{binding.name}', staff_text=binding.note or binding.name,
                        client_text=f'Лимит овердрафта до {rub(q.amount)}')],
        valid_until=computed_at.astimezone(MSK).date() + timedelta(days=VALID_DAYS), **common)


# --- The batch ---------------------------------------------------------------------------

async def preapprove_client(inn: str, pipeline: Pipeline) -> PreApproval:
    """Enrich one client through the application's adapters and pre-approve it."""
    computed_at = pipeline.clock()

    def no(code: str, text: str) -> PreApproval:
        return PreApproval(inn=inn, approved=False, product=PRODUCT, term_months=TERM_MONTHS, amount=None, rate=None,
                           binding=None, grade=None, data_quality_tier=None, reasons=[_reason(code, text)],
                           as_of=pipeline.as_of, computed_at=computed_at, valid_until=None, model_version=None,
                           policy_version=policy_version(None))

    # SIMPLIFICATION: the registry is asked once more for the main owner the standing consent names; a bank
    # would read the owner from its consent register.
    try:
        egrul = await asyncio.wait_for(pipeline.client.fetch(SourceId.EGRUL, inn), pipeline.retry.timeout_seconds)
    except SourceNotFound:
        return no('company_not_found', 'Компания не найдена в ЕГРЮЛ / ЕГРИП')
    except Exception as e:  # tonight without this client; the next run asks again
        return no('source_unavailable', f'ЕГРЮЛ / ЕГРИП не ответил: {e.__class__.__name__}')
    request = standing_request(inn, egrul, computed_at)
    enrichment = await enrich(request, pipeline.client, policy=pipeline.retry, now=pipeline.clock)
    return preapprove(request, enrichment, as_of=pipeline.as_of, computed_at=computed_at, scorer=pipeline.scorer)


async def _preapprove_all(inns: Iterable[str], pipeline: Pipeline, concurrency: int) -> list[PreApproval]:
    gate = asyncio.Semaphore(concurrency)

    async def one(inn: str) -> PreApproval:
        async with gate:
            return await preapprove_client(inn, pipeline)
    return list(await asyncio.gather(*(one(inn) for inn in inns)))


def client_inns(pipeline: Pipeline) -> list[str]:
    # MOCK: the bank's client base is the synthetic world: every company in it has an account with us.
    return [company.inn for company in pipeline.client.world.companies]


def batch_pipeline(pipeline: Pipeline) -> Pipeline:
    """The application pipeline without the mock latency: a night batch doesn't wait on each answer.

    MOCK: a real batch reads the bank's own data warehouse and a bureau batch file instead of calling each
    source per client; the online calls here keep one code path for applications and the batch.
    """
    if isinstance(pipeline.client, MockSourceService):
        client = MockSourceService(pipeline.client.world, replace(pipeline.client.settings, latency_scale=0))
        return replace(pipeline, client=client)
    return pipeline


@dataclass(frozen=True)
class BatchResult:
    clients: int
    approved: int
    seconds: float


def run_batch(pipeline: Pipeline | None = None, inns: Iterable[str] | None = None,
              concurrency: int = CONCURRENCY) -> BatchResult:
    """Pre-approve every client and store the latest result per ИНН, replacing the previous night's."""
    started = time.monotonic()
    pipeline = batch_pipeline(pipeline or default_pipeline())
    inns = list(client_inns(pipeline) if inns is None else inns)
    results = asyncio.run(_preapprove_all(inns, pipeline, concurrency))
    with Session.begin() as db:
        for result in results:
            row = db.get(PreApprovalRow, result.inn) or PreApprovalRow(inn=result.inn)
            row.approved = result.approved
            row.amount = result.amount
            row.computed_at = result.computed_at.isoformat()
            row.valid_until = result.valid_until.isoformat() if result.valid_until else None
            row.result = result.model_dump(mode='json')
            db.add(row)
    approved = sum(r.approved for r in results)
    batch = BatchResult(clients=len(results), approved=approved, seconds=round(time.monotonic() - started, 1))
    log.info('pre-approval batch: %d of %d clients pre-approved in %.1f s', approved, batch.clients, batch.seconds)
    return batch


def latest(inn: str, on: date | None = None) -> PreApproval | None:
    """The stored result for this ИНН; an approved one past `valid_until` comes back as not approved."""
    with Session() as db:
        row = db.get(PreApprovalRow, inn)
        if row is None:
            return None
        result = PreApproval.model_validate(row.result)
    on = on or datetime.now(MSK).date()
    if result.approved and result.valid_until is not None and result.valid_until < on:
        return result.model_copy(update={'approved': False, 'reasons': [
            _reason('expired', f'Предодобрение действовало до {result.valid_until.isoformat()}')]})
    return result


# SIMPLIFICATION: "nightly" is "when the latest batch is older than a day", checked by the in-process worker;
# a bank would schedule the batch in its job scheduler at night.
_running = threading.Lock()
_last_attempt = 0.0  # monotonic; a failing batch is retried once an hour, not on every worker step
RETRY_SECONDS = 3600


def last_run() -> datetime | None:
    with Session() as db:
        value = db.scalar(select(func.max(PreApprovalRow.computed_at)))
    return datetime.fromisoformat(value) if value else None


def due(now: datetime | None = None) -> bool:
    last = last_run()
    now = now or datetime.now(MSK)
    return last is None or (now - last).total_seconds() >= BATCH_EVERY_SECONDS


def start_batch(pipeline: Pipeline | None = None) -> bool:
    """Run the batch in its own thread, so the application queue doesn't wait; False if one is running."""
    if not _running.acquire(blocking=False):
        return False

    def run() -> None:
        try:
            run_batch(pipeline)
        except Exception:
            log.exception('pre-approval batch failed')
        finally:
            _running.release()
    threading.Thread(target=run, name='preapproval-batch', daemon=True).start()
    return True


def tick() -> None:
    """The worker's nightly check: start the batch when it is due."""
    global _last_attempt
    if _running.locked() or time.monotonic() - _last_attempt < RETRY_SECONDS or not due():
        return
    _last_attempt = time.monotonic()
    start_batch()


if __name__ == '__main__':
    from apps.api.db import init_db
    logging.basicConfig(level=logging.INFO)
    init_db()
    print(run_batch())
