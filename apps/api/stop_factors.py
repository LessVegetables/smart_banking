"""Deterministic stop factors and review flags for the v2 application workflow (T14)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from apps.api.contracts import (AmlPayload, ApplicationRequest, ArbitrationPayload, BkiPayload,
                                ClientCardPayload, EgrulPayload, FnsPayload, FsspPayload, Reason,
                                SourceCallStatus, SourceId, SourceResponse)
from apps.api.features import (FeaturePolicy, SourcePayloads, account_features, default_policy,
                               fns_revenue, subject_history)
from apps.api.features.extract import owner_or_director_changed


@dataclass(frozen=True)
class StopFactorResult:
    """Stop factors prevent scoring; flags require manual review."""

    stop_factors: tuple[Reason, ...]
    flags: tuple[Reason, ...]


def _reason(code: str, staff: str, client: str | None = None) -> Reason:
    return Reason(code=code, staff_text=staff, client_text=client or staff)


@lru_cache(maxsize=1)
def _okved_rules() -> dict:
    """Use the already published v1 policy lists; T14 does not change shared config."""
    path = Path(__file__).resolve().parents[2] / 'config' / 'rules.json'
    return json.loads(path.read_text(encoding='utf-8'))


def _matches_prefix(code: str, prefixes: Iterable[str]) -> bool:
    return any(code.startswith(prefix) for prefix in prefixes)


def evaluate_stop_factors(request: ApplicationRequest, responses: Iterable[SourceResponse], as_of: date,
                          policy: FeaturePolicy | None = None) -> StopFactorResult:
    """Evaluate SPEC §7 using successful source payloads and the shared cleaned features.

    Source failures are represented by the response envelope; a failed optional source yields
    its own review flag. Missing data never creates a stop factor by itself.
    """
    policy = policy or default_policy()
    responses = tuple(responses)
    by_source = {response.source: response for response in responses}

    def payload(source: SourceId, expected_type):
        response = by_source.get(source)
        if response is None or response.status != SourceCallStatus.OK:
            return None
        value = response.payload
        return value if isinstance(value, expected_type) else None

    egrul = payload(SourceId.EGRUL, EgrulPayload)
    fns = payload(SourceId.FNS, FnsPayload)
    bki = payload(SourceId.BKI, BkiPayload)
    fssp = payload(SourceId.FSSP, FsspPayload)
    arbitration = payload(SourceId.ARBITRATION, ArbitrationPayload)
    aml = payload(SourceId.AML, AmlPayload)
    client_card = payload(SourceId.CLIENT_CARD, ClientCardPayload)
    source_payloads = SourcePayloads.from_responses(responses)
    revenue = fns_revenue(fns, egrul, source_payloads.accounts)
    revenue_12m = revenue.estimate_12m if revenue else None
    rules = _okved_rules()
    stop: list[Reason] = []
    flags: list[Reason] = []

    if egrul:
        if egrul.status in {'liquidating', 'liquidated'}:
            stop.append(_reason('company_liquidating', 'Компания ликвидируется или ликвидирована.'))
        if _matches_prefix(egrul.okved_main, rules.get('okved_stop_prefixes', [])):
            stop.append(_reason('okved_prohibited', f'Основной ОКВЭД {egrul.okved_main} в запрещённом списке.'))
        age_months = (as_of.year - egrul.registered_at.year) * 12 + as_of.month - egrul.registered_at.month - (
            as_of.day < egrul.registered_at.day)
        age_limit = 3 if request.preferred_product and request.preferred_product.value == 'overdraft' else 6
        if age_months < age_limit:
            stop.append(_reason('business_too_young', f'Срок работы бизнеса {age_months} мес., требуется не менее {age_limit} мес.'))

    if arbitration and any(n.kind != 'other' for n in arbitration.bankruptcy_notices):
        stop.append(_reason('bankruptcy_notice', 'На Федресурсе есть сообщение о банкротстве.'))
    if fns and fns.accounts_suspended:
        stop.append(_reason('fns_accounts_suspended', 'ФНС приостановила операции по счетам.'))
    if fns and fns.director_disqualified:
        stop.append(_reason('director_disqualified', 'Директор дисквалифицирован.'))
    if client_card and client_card.internal_stop_list:
        stop.append(_reason('internal_stop_list', 'Клиент находится во внутреннем стоп-листе.'))
    if aml and aml.list_matches:
        stop.append(_reason('aml_list_match', 'Сведения требуют проверки в рамках обязательных процедур ПОД/ФТ.',
                            'Заявку нельзя обработать автоматически.'))

    if bki:
        for report in bki.reports:
            history = subject_history(report, as_of, policy.overdue)
            subject_name = 'компания' if report.subject == 'company' else 'собственник'
            if history.current_overdue_days > 30:
                stop.append(_reason(f'bki_current_overdue_30_{report.subject}',
                                    f'БКИ ({subject_name}): текущая просрочка {history.current_overdue_days} дней.'))
            if history.worst_days_24m >= 90:
                stop.append(_reason('bki_overdue_90_24m',
                                    f'БКИ ({subject_name}): просрочка 90+ дней за последние 24 месяца.'))

    # Optional source outages do not block the application, but require staff review.
    for source in (SourceId.FNS, SourceId.FSSP, SourceId.ARBITRATION):
        response = by_source.get(source)
        if response is None or response.status != SourceCallStatus.OK:
            flags.append(_reason(f'source_unavailable_{source.value}', f'Источник {source.value} недоступен; данных недостаточно.'))

    if egrul and _matches_prefix(egrul.okved_main, rules.get('okved_manual_prefixes', [])):
        flags.append(_reason('okved_manual_review', f'Основной ОКВЭД {egrul.okved_main} требует ручного рассмотрения.'))
    if egrul and owner_or_director_changed(egrul, as_of, policy.change_window_months):
        flags.append(_reason('owner_or_director_changed_6m', 'Директор или собственник сменился за последние 6 месяцев.'))
    if revenue_12m and request.declared_annual_revenue is not None and abs(
            request.declared_annual_revenue - revenue_12m) / revenue_12m > 0.30:
        flags.append(_reason('declared_revenue_mismatch', 'Заявленная выручка отличается от оценки ФНС более чем на 30%.'
                             ))

    accounts = source_payloads.accounts
    account = account_features(accounts)
    if account.top_customer_share is not None and account.top_customer_share > 0.60:
        flags.append(_reason('top_customer_concentration', 'Один покупатель обеспечивает более 60% поступлений.'))
    if account.cash_withdrawal_share is not None and account.cash_withdrawal_share > 0.30:
        flags.append(_reason('cash_withdrawal_share', 'Снятие наличных превышает 30% расходов.'))
    if aml and aml.transit_operations:
        flags.append(_reason('transit_operations', 'Выявлены признаки транзитных операций.'))
    if arbitration and revenue_12m and revenue_12m > 0 and any(
            case.role == 'defendant' and case.claim_amount > revenue_12m * 0.10 for case in arbitration.cases):
        flags.append(_reason('arbitration_claim_over_10pct_revenue', 'Иски к компании превышают 10% выручки.'))
    if fssp and any(proceeding.remaining > 0 for proceeding in fssp.proceedings):
        flags.append(_reason('fssp_proceedings', 'Есть действующие исполнительные производства.'))
    if bki and sum(inquiry.lender_type == 'bank' and inquiry.inquired_at >= as_of - timedelta(days=30)
                    for report in bki.reports for inquiry in report.inquiries) >= 3:
        flags.append(_reason('multiple_recent_bki_inquiries', 'За последние 30 дней было 3 или более запросов кредитной истории.'))

    return StopFactorResult(tuple(stop), tuple(flags))
