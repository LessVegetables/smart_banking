"""Statements from other banks (T23, SPEC §5.1): what a `PARTIAL` or `THIN` client uploads so the bank sees
more of the business.

    upload       parse_statement: file → `Statement` (MOCK: a machine-readable export, no OCR)
    merge        with_statements: our account + the statements, month by month, for data quality and the limit
    check        check_statements: stale statements are left out; inflows far above ФНС revenue are a flag
    facts        statement_facts: `statement_upload` facts at the `client_document` trust level

The statements change only what SPEC §5.1 says they change: the data-quality metrics (most of all revenue
coverage) and the cash-flow limit. Scorecard features, stop factors and flags stay on our own account and the
registries: the card was trained on them, and a client document is less reliable (SPEC §5.1, trust order).
An application with a statement always goes to the underwriter (`route_statement_upload`, routing.py), who
checks the document. See STATEMENTS.md.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from apps.api.contracts import (AccountsPayload, ApplicationRequest, CounterpartySummary, Fact, Inflows, MonthlyTurnover,
                                Outflows, Reason, SourceId, TrustLevel)
from apps.api.features import SourcePayloads, fns_revenue

MAX_FILES = 5
MAX_FILE_BYTES = 2_000_000
HISTORY_MONTHS = 24  # AccountsPayload keeps at most 24 months
# SIMPLIFICATION: policy values set here, not in config/ (shared-folder PR, docs/v2/NOTES.md → Unassigned):
STALE_MONTHS = 3  # a statement must reach at least this close to the last full month before `as_of`
FNS_EXCESS = 0.30  # merged customer inflows above ФНС revenue by more than this: a flag, like the declared revenue

REQUESTED_STATEMENT = 'Выписка по расчётному счёту в другом банке за последние 12 месяцев'


class StatementError(ValueError):
    """The upload is refused; the text is for the client."""


# MOCK: the parsed statement. A bank would take a PDF or a 1C exchange file (1CClientBankExchange), read it with
# an OCR / parsing model with open weights and categorise every payment; here the client's file already is
# the result of that: monthly turnovers by the same categories as our own account (`MonthlyTurnover`).
class Statement(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    bank_name: Annotated[str, Field(min_length=1, max_length=200)]
    inn: Annotated[str, Field(pattern=r'^\d{10}(\d{2})?$')]
    months: Annotated[list[MonthlyTurnover], Field(min_length=1, max_length=HISTORY_MONTHS)]

    @model_validator(mode='after')
    def _months_ordered(self):
        names = [m.month for m in self.months]
        if names != sorted(set(names)):
            raise ValueError('months must be unique and ordered oldest first')
        return self


class UploadedStatement(BaseModel):
    """What is stored per file (`LoanApplication.statements`): the file's identity and its parsed content."""
    model_config = ConfigDict(extra='forbid', frozen=True)

    file_name: str
    size: int
    sha256: str
    uploaded_at: datetime
    statement: Statement


def parse_statement(file_name: str, content: bytes, request: ApplicationRequest, uploaded_at: datetime) -> UploadedStatement:
    """One uploaded file → its statement, or `StatementError` with the reason for the client."""
    if not content:
        raise StatementError(f'Файл «{file_name}» пустой')
    if len(content) > MAX_FILE_BYTES:
        raise StatementError(f'Файл «{file_name}» больше {MAX_FILE_BYTES // 1_000_000} МБ')
    try:
        statement = Statement.model_validate(json.loads(content.decode('utf-8')))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
        raise StatementError(f'Не удалось распознать выписку в файле «{file_name}»') from None
    if statement.inn != request.inn:
        raise StatementError(f'Выписка в файле «{file_name}» выдана на другой ИНН')
    return UploadedStatement(file_name=file_name, size=len(content), sha256=hashlib.sha256(content).hexdigest(),
                             uploaded_at=uploaded_at, statement=statement)


def parse_statements(files: Sequence[tuple[str, bytes]], request: ApplicationRequest, uploaded_at: datetime,
                     already: Sequence[UploadedStatement] = ()) -> list[UploadedStatement]:
    if not files:
        raise StatementError('Приложите хотя бы одну выписку')
    if len(files) + len(already) > MAX_FILES:
        raise StatementError(f'Можно загрузить не больше {MAX_FILES} выписок')
    parsed = [parse_statement(name, content, request, uploaded_at) for name, content in files]
    seen = {u.sha256 for u in already}
    for u in parsed:
        if u.sha256 in seen:
            raise StatementError(f'Файл «{u.file_name}» уже загружен')
        seen.add(u.sha256)
    return parsed


# --- Checks ------------------------------------------------------------------------------

def _month_index(month: str) -> int:
    year, m = month.split('-')
    return int(year) * 12 + int(m) - 1


def last_full_month(as_of: date) -> int:
    return as_of.year * 12 + as_of.month - 2


@dataclass(frozen=True)
class StatementCheck:
    used: tuple[UploadedStatement, ...]  # the statements that join our account
    flags: tuple[Reason, ...]


def _flag(code: str, text: str) -> Reason:
    return Reason(code=code, staff_text=text, client_text='Мы проверим загруженные выписки вручную.')


def check_statements(statements: Sequence[UploadedStatement], payloads: SourcePayloads, as_of: date) -> StatementCheck:
    """Consistency with our own data and the registries (SPEC §5.1: client documents are checked against them).

    `payloads` are our own account and the registries, without the statements.
    """
    last = last_full_month(as_of)
    used, flags = [], []
    for u in statements:
        end = _month_index(u.statement.months[-1].month)
        if end > last or last - end > STALE_MONTHS:
            flags.append(_flag('statement_period', f'Выписка «{u.file_name}» ({u.statement.bank_name}) заканчивается '
                                                   f'{u.statement.months[-1].month}: не учтена'))
        else:
            used.append(u)
    revenue = fns_revenue(payloads.fns, payloads.egrul, payloads.accounts)
    if used and revenue is not None and revenue.annualized > 0:
        merged = with_statements(payloads, used).accounts
        inflows = sum(m.inflows.customers for m in merged.months[-12:])
        if inflows > revenue.annualized * (1 + FNS_EXCESS):
            flags.append(_flag('statement_inflows_above_fns',
                               f'Поступления от покупателей с учётом выписок ({inflows:,} ₽ за 12 мес.) выше выручки '
                               f'по ФНС ({revenue.annualized:,} ₽) более чем на {FNS_EXCESS:.0%}'.replace(',', ' ')))
    return StatementCheck(tuple(used), tuple(flags))


# --- Merge -------------------------------------------------------------------------------

def _add(a: MonthlyTurnover, b: MonthlyTurnover) -> MonthlyTurnover:
    return MonthlyTurnover(
        month=a.month,
        inflows=Inflows(**{k: getattr(a.inflows, k) + getattr(b.inflows, k) for k in Inflows.model_fields}),
        outflows=Outflows(**{k: getattr(a.outflows, k) + getattr(b.outflows, k) for k in Outflows.model_fields}),
        average_balance=a.average_balance + b.average_balance)


def with_statements(payloads: SourcePayloads, statements: Iterable[UploadedStatement]) -> SourcePayloads:
    """Our account plus the statements, summed month by month: the input of `data_quality` and `cash_flow`.

    Only for data quality and the limit; features and stop factors read `payloads` as they are.
    """
    statements = list(statements)
    if not statements:
        return payloads
    ours = payloads.accounts
    by_month = {m.month: m for m in ours.months} if ours else {}
    for u in statements:
        for m in u.statement.months:
            by_month[m.month] = _add(by_month[m.month], m) if m.month in by_month else m
    # SIMPLIFICATION: transfers between our account and the other bank stay in both statements (an inflow in
    # one, an outflow in the other), which lowers the operating inflow share; a bank would match and drop them.
    months = [by_month[k] for k in sorted(by_month)][-HISTORY_MONTHS:]
    first = date(int(months[0].month[:4]), int(months[0].month[5:]), 1)
    opened_at = min(ours.account_opened_at, first) if ours else first
    # The other banks give no counterparty summary: the dossier keeps ours.
    counterparties = ours.counterparties_12m if ours else CounterpartySummary(
        customers_count=0, suppliers_count=0, top_customer_share=None)
    return replace(payloads, accounts=AccountsPayload(account_opened_at=opened_at, months=months,
                                                      counterparties_12m=counterparties))


# --- Facts -------------------------------------------------------------------------------

def statement_facts(statements: Sequence[UploadedStatement]) -> list[Fact]:
    """What the statements show, next to the same facts from our own account."""
    if not statements:
        return []
    fetched_at = max(u.uploaded_at for u in statements)
    inflows = sum(m.inflows.customers for u in statements for m in u.statement.months[-12:])
    months = len({m.month for u in statements for m in u.statement.months})
    return [Fact(name=name, declared_value=None, verified_value=value, source=SourceId.STATEMENT_UPLOAD,
                 trust_level=TrustLevel.CLIENT_DOCUMENT, fetched_at=fetched_at)
            for name, value in (('operating_inflows_12m', inflows), ('statement_months', months))]
