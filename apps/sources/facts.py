"""Facts from the source answers (T07, SPEC §2 principle 2).

One `Fact` per (name, source): the value the client declared in the application, the
value the source confirms, the source and its trust level. Facts are the source values
as they are, for the dossier's "declared / verified" table and for the rules that read
a single value. Anything computed over many values (the overdue rules, scorecard
features, data-quality metrics) belongs to the feature library (T08, T09); thresholds
belong to the stop factors and flags (T14).

A source that failed gives no verified values: its facts keep only what the client
declared, and a fact nobody declared is left out.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

from apps.api.contracts import (AccountsPayload, AmlPayload, ApplicationRequest, ArbitrationPayload, BkiPayload,
                                ClientCardPayload, EgrulPayload, Fact, FactValue, FnsPayload, FsspPayload, LegalForm,
                                SourceCallStatus, SourceId, TrustLevel)
from apps.api.features import business_report, changed_recently, default_policy, months_between

TRUST = {source: TrustLevel.BANK if source in (SourceId.ACCOUNTS, SourceId.CLIENT_CARD) else TrustLevel.REGISTRY
         for source in SourceId if source != SourceId.STATEMENT_UPLOAD}


def _changed_recently(since: date | None, registered_at: date, today: date) -> bool:
    """Within the window of "director or owner changed in the last 6 months" (SPEC §7, config/features.json)."""
    return changed_recently(since, registered_at, today, default_policy().change_window_months)


def _egrul(p: EgrulPayload, today: date) -> dict[str, FactValue | None]:
    facts = {
        'legal_form': p.legal_form.value,
        'registry_status': p.status,
        'business_age_months': months_between(p.registered_at, today),
        'okved_main': p.okved_main,
        'director_changed_6m': _changed_recently(p.director.since, p.registered_at, today),
    }
    if p.legal_form == LegalForm.OOO:
        facts['founder_changed_6m'] = any(_changed_recently(f.since, p.registered_at, today) for f in p.founders)
    return facts


def _fns(p: FnsPayload, today: date) -> dict[str, FactValue | None]:
    # The revenue of the last filed year as filed, also for a company that worked only part
    # of that year: a fact is the source value as it is. The annualized and 12-month
    # figures are in the feature library, apps/api/features/revenue.py.
    revenue = p.financials.revenue if p.financials else p.tax_return.income if p.tax_return else None
    return {'annual_revenue': revenue, 'employees': p.employees, 'tax_debt': p.tax_debt,
            'accounts_suspended': p.accounts_suspended, 'mass_address': p.mass_address,
            'director_disqualified': p.director_disqualified}


def _accounts(p: AccountsPayload, today: date) -> dict[str, FactValue | None]:
    last12 = p.months[-12:]
    return {'operating_inflows_12m': sum(m.inflows.customers for m in last12) if last12 else None}


def _bki(p: BkiPayload, today: date) -> dict[str, FactValue | None]:
    # The business's own debt service: the company report of an ООО, the only report of an ИП.
    business = business_report(p)
    if business is None:
        return {}
    return {'monthly_debt_payments': sum(loan.monthly_payment for loan in business.loans if loan.closed_at is None)}


def _client_card(p: ClientCardPayload, today: date) -> dict[str, FactValue | None]:
    return {'internal_stop_list': p.internal_stop_list}


def _fssp(p: FsspPayload, today: date) -> dict[str, FactValue | None]:
    return {'fssp_debt_remaining': sum(x.remaining for x in p.proceedings)}


def _arbitration(p: ArbitrationPayload, today: date) -> dict[str, FactValue | None]:
    claims = sum(case.claim_amount for case in p.cases if case.role == 'defendant' and case.status != 'dismissed')
    return {'claims_as_defendant': claims, 'bankruptcy_notice': bool(p.bankruptcy_notices)}


def _aml(p: AmlPayload, today: date) -> dict[str, FactValue | None]:
    return {'aml_list_match': bool(p.list_matches), 'transit_operations': p.transit_operations,
            'zsk_zone': p.zsk_zone}


READERS: dict[SourceId, Callable[..., dict[str, FactValue | None]]] = {
    SourceId.EGRUL: _egrul, SourceId.FNS: _fns, SourceId.ACCOUNTS: _accounts, SourceId.BKI: _bki,
    SourceId.CLIENT_CARD: _client_card, SourceId.FSSP: _fssp, SourceId.ARBITRATION: _arbitration, SourceId.AML: _aml,
}


def declared_values(request: ApplicationRequest) -> dict[str, FactValue | None]:
    """What the client said in the application. Only facts a source can confirm, plus the purpose."""
    return {'legal_form': request.legal_form.value, 'annual_revenue': request.declared_annual_revenue,
            'loan_purpose': request.purpose.value}


def build_facts(request: ApplicationRequest, responses) -> list[Fact]:
    """Facts from the source responses (contract `SourceResponse`) and the application."""
    declared = declared_values(request)
    facts: list[Fact] = []
    used: set[str] = set()
    for response in responses:
        if response.status != SourceCallStatus.OK:
            continue
        today = response.received_at.date()
        for name, value in READERS[response.source](response.payload, today).items():
            if value is None and declared.get(name) is None:
                continue
            facts.append(_fact(name, declared.get(name), value, response.source, response.received_at))
            used.add(name)
    for name, value in declared.items():
        if name not in used and value is not None:
            facts.append(Fact(name=name, declared_value=value, verified_value=None, source=None,
                              trust_level=TrustLevel.DECLARED, fetched_at=None))
    return facts


def _fact(name, declared, verified, source, fetched_at) -> Fact:
    if verified is None:
        return Fact(name=name, declared_value=declared, verified_value=None, source=None,
                    trust_level=TrustLevel.DECLARED, fetched_at=None)
    return Fact(name=name, declared_value=declared, verified_value=verified, source=source,
                trust_level=TRUST[source], fetched_at=fetched_at)
