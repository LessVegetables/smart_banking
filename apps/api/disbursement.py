"""Documents, signing, pre-disbursement conditions and disbursement (T21, SPEC §6 stages 10–12).

    offered -> accepted -> documents    the client picks an offer; its documents are generated at once
    documents -> signed -> conditions   the client signs every document with one confirmation code (ПЭП)
    conditions -> disbursed             the worker checks the conditions (mock) and pays out (mock)

Everything about this stage lives in `LoanApplication.closing` (documents, the code, the signature, conditions,
the payout); statuses change only through `workflow.transition`. See DISBURSEMENT.md.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm.exc import StaleDataError

from apps.api.contracts import (ApplicationRequest, ApplicationStatus as S, CollateralType, EgrulPayload, LegalForm,
                                Offer, ProductId, SourceId)
from apps.api.db import LoanApplication, Session, now
from apps.api.offers import PRODUCT_NAMES, rub
from apps.api.workflow import IllegalTransition, accept as accept_offer, record, transition

log = logging.getLogger(__name__)

# SIMPLIFICATION: confirmation-code policy is a code constant; a bank sets it in its ПЭП agreement with the client.
CODE_DIGITS = 6
CODE_TTL_SECONDS = 300
CODE_ATTEMPTS = 3
MAX_CODES = 5  # codes per application, so a new code can't be asked for forever

COLLATERAL_TEXT = {
    CollateralType.OWNER_GUARANTEE: 'поручительство собственника',
    CollateralType.EQUIPMENT_PLEDGE: 'залог приобретаемого оборудования',
    CollateralType.MSP_GUARANTEE: 'гарантия Корпорации МСП',
}
CONDITION_TEXT = {
    'guarantee_signed': 'Договор поручительства подписан',
    'pledge_registered': 'Залог внесён в реестр уведомлений о залоге движимого имущества',
    'supplier_invoice_checked': 'Счёт поставщика проверен',
    'msp_guarantee_issued': 'Гарантия Корпорации МСП выдана',
}


class SigningError(ValueError):
    """No code can be issued; the text is shown to the client as is."""


# --- Documents (stage 10) ----------------------------------------------------------------

def selected_offer(app: LoanApplication) -> Offer:
    offers = {o['offer_id']: o for o in (app.decision or {}).get('offers', [])}
    return Offer.model_validate(offers[app.selected_offer_id])


def _registry(app: LoanApplication) -> EgrulPayload | None:
    for r in (app.enrichment or {}).get('responses', []):
        if r['source'] == SourceId.EGRUL and r.get('payload'):
            return EgrulPayload.model_validate(r['payload'])
    return None


def _guarantor(registry: EgrulPayload | None) -> str:
    """The main owner: the largest founder's share, or the director when ЕГРЮЛ lists no founders."""
    if registry is None:
        return 'собственник бизнеса'
    person = max(registry.founders, key=lambda f: f.share) if registry.founders else registry.director
    return f'{person.full_name}, ИНН {person.inn}'


def _number(app: LoanApplication, prefix: str) -> str:
    return f'{prefix}-{app.id[:8].upper()}'


def build_documents(app: LoanApplication, offer: Offer, request: ApplicationRequest) -> list[dict]:
    """The contracts the chosen offer needs: always the loan agreement, one more per collateral."""
    registry = _registry(app)
    borrower = f'{registry.name if registry else "Заёмщик"}, ИНН {request.inn}'
    loan_no = _number(app, 'КД')
    payout = (f'перечисление поставщику (ИНН {request.equipment.supplier_inn}) по счёту за оборудование'
              if offer.disbursement == 'supplier' and request.equipment else 'зачисление на расчётный счёт заёмщика')
    lines = [
        f'Кредитный договор № {loan_no}', f'Заёмщик: {borrower}', f'Продукт: {PRODUCT_NAMES[offer.product]}',
        f'Сумма: {rub(offer.amount)}', f'Срок: {offer.term_months} мес.', f'Ставка: {offer.rate:.2%} годовых',
        f'Ежемесячный платёж: {rub(offer.monthly_payment)}',
        'Обеспечение: ' + (', '.join(COLLATERAL_TEXT[c] for c in offer.collateral) or 'без обеспечения'),
        f'Порядок выдачи: {payout}',
    ]
    if offer.guarantee_fee:
        lines.append(f'Комиссия за гарантию Корпорации МСП: {offer.guarantee_fee:.2%} годовых')
    docs = [dict(kind='loan_agreement', title=f'Кредитный договор № {loan_no}', signer=borrower, lines=lines)]
    if CollateralType.OWNER_GUARANTEE in offer.collateral:
        guarantor = _guarantor(registry)
        docs.append(dict(kind='guarantee_agreement', title=f'Договор поручительства № {_number(app, "ДП")}',
                         signer=guarantor, lines=[
                             f'Поручитель: {guarantor}', f'Обязательства заёмщика по кредитному договору № {loan_no}',
                             f'Поручительство на всю сумму долга: {rub(offer.amount)}, проценты и неустойки']))
    if CollateralType.EQUIPMENT_PLEDGE in offer.collateral and request.equipment:
        eq = request.equipment
        docs.append(dict(kind='pledge_agreement', title=f'Договор залога № {_number(app, "ДЗ")}', signer=borrower,
                         lines=[f'Залогодатель: {borrower}', f'Предмет залога: {eq.description}',
                                f'Стоимость по счёту поставщика: {rub(eq.price)}',
                                f'Обеспечивает кредитный договор № {loan_no}']))
    if CollateralType.MSP_GUARANTEE in offer.collateral:
        # MOCK: the application to Корпорация МСП is a document the client signs; nothing is sent anywhere.
        docs.append(dict(kind='msp_guarantee_application',
                         title=f'Заявка на гарантию Корпорации МСП № {_number(app, "МСП")}', signer=borrower,
                         lines=[f'Принципал: {borrower}', f'Обеспечивает кредитный договор № {loan_no}',
                                f'Комиссия: {offer.guarantee_fee:.2%} годовых']))
    # One document per kind, so the kind is its id within the application.
    return [{'doc_id': d['kind'], 'kind': d['kind'], 'title': d['title'], 'signer': d['signer'],
             'text': '\n'.join(d['lines']), 'signed_at': None} for d in docs]


def conditions_for(offer: Offer) -> list[dict]:
    codes = []
    if CollateralType.OWNER_GUARANTEE in offer.collateral:
        codes.append('guarantee_signed')
    if CollateralType.EQUIPMENT_PLEDGE in offer.collateral:
        codes.append('pledge_registered')
    if offer.disbursement == 'supplier':
        codes.append('supplier_invoice_checked')
    if CollateralType.MSP_GUARANTEE in offer.collateral:
        codes.append('msp_guarantee_issued')
    return [{'code': c, 'title': CONDITION_TEXT[c], 'status': 'pending', 'detail': None, 'at': None} for c in codes]


def accept(db: OrmSession, app: LoanApplication, offer_id: str, *, actor: str, on: date | None = None) -> None:
    """The client picks an offer (`workflow.accept`); its documents are generated in the same transaction."""
    accept_offer(db, app, offer_id, actor=actor, on=on)
    if S(app.status) != S.ACCEPTED:  # the offer ran out: the application expired
        return
    offer = selected_offer(app)
    documents = build_documents(app, offer, ApplicationRequest.model_validate(app.request))
    app.closing = {'offer_id': offer.offer_id, 'documents': documents, 'code': None, 'codes_issued': 0,
                   'signature': None, 'conditions': conditions_for(offer), 'payout': None}
    transition(db, app, S.DOCUMENTS, message='Документы сформированы: ' + ', '.join(d['title'] for d in documents),
               data={'documents': [d['doc_id'] for d in documents]})


# --- Signing (stage 10) -------------------------------------------------------------------

def _hash(app: LoanApplication, code: str) -> str:
    return hashlib.sha256(f'{app.id}:{code}'.encode()).hexdigest()


def issue_code(db: OrmSession, app: LoanApplication, *, actor: str, at: float | None = None) -> str:
    """A new confirmation code; the previous one stops working. Only its hash is stored."""
    at = time.time() if at is None else at
    if S(app.status) != S.DOCUMENTS:
        raise IllegalTransition('Код подписания можно получить только в статусе documents')
    closing = dict(app.closing)
    if closing['codes_issued'] >= MAX_CODES:
        raise SigningError('Лимит кодов подтверждения исчерпан. Обратитесь в банк.')
    code = ''.join(secrets.choice('0123456789') for _ in range(CODE_DIGITS))
    closing['code'] = {'hash': _hash(app, code), 'expires_at': at + CODE_TTL_SECONDS, 'attempts_left': CODE_ATTEMPTS}
    closing['codes_issued'] += 1
    app.closing = closing
    # MOCK: no SMS gateway; the code goes back to the client interface (`demo_code`) instead of a phone.
    record(db, app, 'action', 'Отправлен код подтверждения для подписания', actor=actor,
           data={'codes_issued': closing['codes_issued']})
    return code


def sign(db: OrmSession, app: LoanApplication, code: str, *, actor: str, at: float | None = None) -> str | None:
    """Signs every document with one code (ПЭП): documents → signed → conditions.

    Returns None when signed, else the text for the client. Not an exception: a wrong code uses up an attempt,
    and that change must commit.
    """
    at = time.time() if at is None else at
    if S(app.status) != S.DOCUMENTS:
        raise IllegalTransition('Подписать документы можно только в статусе documents')
    closing = dict(app.closing)
    pending = closing['code']
    if pending is None:
        return 'Сначала запросите код подтверждения'
    if at > pending['expires_at']:
        return 'Срок действия кода истёк. Запросите новый код.'
    if not hmac.compare_digest(pending['hash'], _hash(app, code.strip())):
        left = pending['attempts_left'] - 1
        closing['code'] = {**pending, 'attempts_left': left} if left > 0 else None
        app.closing = closing
        record(db, app, 'action', 'Неверный код подтверждения', actor=actor, data={'attempts_left': left})
        return (f'Неверный код. Осталось попыток: {left}' if left > 0
                else 'Неверный код. Попытки закончились, запросите новый код.')
    signed_at = now()
    # SIMPLIFICATION: the client's one code also signs the owner's guarantee; in a bank the guarantor (the main
    # owner) signs it with their own ПЭП or ЭП.
    closing['documents'] = [{**d, 'signed_at': signed_at} for d in closing['documents']]
    closing['code'] = None
    closing['signature'] = {'at': signed_at, 'actor': actor, 'method': 'pep_code',
                            'documents': [d['doc_id'] for d in closing['documents']]}
    app.closing = closing
    transition(db, app, S.SIGNED, actor=actor, message='Документы подписаны кодом подтверждения (ПЭП)',
               data={'documents': closing['signature']['documents']})
    transition(db, app, S.CONDITIONS)
    return None


# --- Conditions and disbursement (stages 11–12) -------------------------------------------

def _mock_number(app: LoanApplication, kind: str, digits: int) -> str:
    return str(int(hashlib.sha256(f'{kind}:{app.id}'.encode()).hexdigest(), 16))[:digits].rjust(digits, '0')


def check_condition(app: LoanApplication, code: str, request: ApplicationRequest, offer: Offer) -> tuple[bool, str]:
    """MOCK: each check stands in for a registry or back-office step; the result and its detail are logged."""
    if code == 'guarantee_signed':
        doc = next((d for d in app.closing['documents'] if d['kind'] == 'guarantee_agreement'), None)
        return (doc is not None and doc['signed_at'] is not None,
                f'Подписан {doc["signed_at"]}' if doc and doc['signed_at'] else 'Договор поручительства не подписан')
    if code == 'pledge_registered':
        # MOCK: Федеральная нотариальная палата; the registration number is made up from the application.
        return True, f'Уведомление о залоге № {date.today().year}-{_mock_number(app, "pledge", 9)}'
    if code == 'supplier_invoice_checked':
        eq = request.equipment
        if eq is None:
            return False, 'В заявке нет счёта поставщика'
        if eq.supplier_inn == request.inn:
            return False, 'Поставщик совпадает с заёмщиком'
        if offer.amount > eq.price - eq.down_payment:
            return False, f'Сумма кредита больше стоимости за вычетом взноса: {rub(eq.price - eq.down_payment)}'
        # MOCK: the invoice isn't a real document; its number is made up and the supplier isn't looked up.
        return True, (f'Счёт № {_mock_number(app, "invoice", 6)} на {rub(eq.price)}, поставщик ИНН {eq.supplier_inn}; '
                      f'собственный взнос {rub(eq.down_payment)}')
    if code == 'msp_guarantee_issued':
        return True, f'Гарантия № МСП-{_mock_number(app, "msp", 8)} (мок)'
    raise ValueError(code)


def client_account(request: ApplicationRequest) -> str:
    """MOCK: the client's settlement account at our bank, derived from the ИНН (40702 for ООО, 40802 for ИП)."""
    prefix = '40702810' if request.legal_form == LegalForm.OOO else '40802810'
    return prefix + str(int(hashlib.sha256(request.inn.encode()).hexdigest(), 16))[:12]


def payout_for(app: LoanApplication, request: ApplicationRequest, offer: Offer) -> dict:
    """MOCK: no real payment; the payout record says where the money (or the limit) went."""
    base = {'amount': offer.amount, 'payment_id': f'PAY-{_mock_number(app, "payment", 10)}', 'at': now()}
    if offer.disbursement == 'supplier' and request.equipment:
        return {**base, 'kind': 'payment', 'to': 'supplier', 'supplier_inn': request.equipment.supplier_inn,
                'text': f'{rub(offer.amount)} перечислено поставщику (ИНН {request.equipment.supplier_inn}) '
                        'в оплату оборудования'}
    account = client_account(request)
    if offer.product in (ProductId.OVERDRAFT, ProductId.CREDIT_LINE):
        return {**base, 'kind': 'limit', 'to': 'client_account', 'account': account,
                'text': f'{PRODUCT_NAMES[offer.product]}: лимит {rub(offer.amount)} установлен по счёту {account}'}
    return {**base, 'kind': 'payment', 'to': 'client_account', 'account': account,
            'text': f'{rub(offer.amount)} зачислено на счёт {account}'}


def complete_conditions(db: OrmSession, app: LoanApplication) -> bool:
    """Runs the pending checks; disburses once all are met. False when a condition failed (stays in conditions)."""
    request = ApplicationRequest.model_validate(app.request)
    offer = selected_offer(app)
    closing = dict(app.closing)
    conditions = []
    for c in closing['conditions']:
        if c['status'] == 'pending':
            ok, detail = check_condition(app, c['code'], request, offer)
            c = {**c, 'status': 'met' if ok else 'failed', 'detail': detail, 'at': now()}
            record(db, app, 'action', f'{c["title"]}: {detail}' if ok else f'Условие не выполнено: {c["title"]}. {detail}',
                   data={'condition': c['code'], 'status': c['status']})
        conditions.append(c)
    closing['conditions'] = conditions
    if any(c['status'] == 'failed' for c in conditions):
        app.closing = closing
        return False
    closing['payout'] = payout_for(app, request, offer)
    app.closing = closing
    transition(db, app, S.DISBURSED, message=closing['payout']['text'],
               data={k: v for k, v in closing['payout'].items() if k != 'text'})
    return True


def tick() -> int:
    """One worker step: every application in `conditions` with no failed condition is checked and paid out."""
    with Session() as db:
        due = list(db.scalars(select(LoanApplication.id).where(LoanApplication.status == S.CONDITIONS.value)
                              .order_by(LoanApplication.updated)))
    done = 0
    for application_id in due:
        try:
            with Session.begin() as db:
                app = db.get(LoanApplication, application_id)
                if app is None or S(app.status) != S.CONDITIONS or \
                        any(c['status'] == 'failed' for c in app.closing['conditions']):
                    continue
                done += complete_conditions(db, app)
        except StaleDataError:  # another worker got there first
            continue
    return done


# --- Views -------------------------------------------------------------------------------

def closing_json(closing: dict | None, *, staff: bool) -> dict | None:
    """Documents, signature, conditions and payout; the code itself is never shown, only whether one is pending."""
    if closing is None:
        return None
    code = closing.get('code')
    view = {**{k: v for k, v in closing.items() if k != 'code'},
            'code': {'expires_at': code['expires_at'], 'attempts_left': code['attempts_left']} if code else None}
    if not staff:
        view['conditions'] = [{k: c[k] for k in ('code', 'title', 'status')} for c in closing['conditions']]
    return view
