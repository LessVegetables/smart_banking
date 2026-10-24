"""Scorecard features (SPEC §8): one function for training (T10) and the live engine.

`extract_features(payloads, as_of)` reads only contract source payloads, never the
synthetic world's hidden truth (`q`, archetype, outcome). Training passes the world's
copy of each answer, the live engine the answers enrichment received (`from_responses`):
the same code computes the same features from the same data.

A payload that is None means the source didn't answer; every feature that depends on it
is None and goes to the scorecard's "no data" bin (SPEC §5.1).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from apps.api.contracts import (AccountsPayload, BkiPayload, EgrulPayload, Features, FnsPayload, LegalForm,
                                SourceCallStatus, SourceId)

from .accounts import account_features
from .credit import credit_features
from .dates import changed_recently, months_between
from .policy import FeaturePolicy, default_policy
from .revenue import fns_revenue


@dataclass(frozen=True)
class SourcePayloads:
    """The source answers the features read. None: the source failed or wasn't asked."""
    egrul: EgrulPayload | None = None
    fns: FnsPayload | None = None
    accounts: AccountsPayload | None = None
    bki: BkiPayload | None = None

    @classmethod
    def from_responses(cls, responses: Iterable) -> SourcePayloads:
        """From contract `SourceResponse`s, e.g. `Enrichment.responses`; failed calls give None."""
        fields = {SourceId.EGRUL: 'egrul', SourceId.FNS: 'fns', SourceId.ACCOUNTS: 'accounts', SourceId.BKI: 'bki'}
        return cls(**{fields[r.source]: r.payload for r in responses
                      if r.source in fields and r.status == SourceCallStatus.OK})

    @classmethod
    def from_company(cls, company) -> SourcePayloads:
        """The world's copy of the answers (`apps.world.Company`), for training. Takes only the
        source payloads, so the hidden truth stays behind."""
        return cls(egrul=company.egrul, fns=company.fns, accounts=company.accounts, bki=company.bki)


def snapshot_date(responses: Iterable) -> date | None:
    """The day the sources answered: the date the features are computed as of in the live engine."""
    received = [r.received_at for r in responses if r.received_at is not None]
    return max(received).date() if received else None


def owner_or_director_changed(egrul: EgrulPayload, as_of: date, window_months: int) -> bool:
    people = [egrul.director, *egrul.founders] if egrul.legal_form == LegalForm.OOO else [egrul.director]
    return any(changed_recently(p.since, egrul.registered_at, as_of, window_months) for p in people)


def extract_features(payloads: SourcePayloads, as_of: date, policy: FeaturePolicy | None = None) -> Features:
    policy = policy or default_policy()
    egrul, fns = payloads.egrul, payloads.fns
    legal_form = egrul.legal_form if egrul else None
    is_ip = legal_form == LegalForm.IP or (legal_form is None and fns is not None and fns.tax_return is not None)
    account = account_features(payloads.accounts)
    credit = credit_features(payloads.bki, as_of, policy, is_ip=is_ip)
    revenue = fns_revenue(fns, egrul, payloads.accounts)
    financials = fns.financials if fns and not is_ip else None

    debt_to_revenue = None
    if credit.business_debt is not None and revenue is not None and revenue.annualized > 0:
        debt_to_revenue = round(credit.business_debt / revenue.annualized, 3)

    return Features(
        feature_set_version=policy.version,
        inflow_trend_12m=account.inflow_trend_12m,
        inflow_cv_12m=account.inflow_cv_12m,
        months_without_inflows_12m=account.months_without_inflows_12m,
        customers_count_12m=account.customers_count_12m,
        top_customer_share=account.top_customer_share,
        cash_withdrawal_share=account.cash_withdrawal_share,
        balance_days_of_expenses=account.balance_days_of_expenses,
        tax_payment_months_12m=account.tax_payment_months_12m,
        payroll_payment_months_12m=account.payroll_payment_months_12m,
        worst_delinquency_days_24m_company=credit.worst_delinquency_days_24m_company,
        worst_delinquency_days_24m_owner=credit.worst_delinquency_days_24m_owner,
        bki_inquiries_3m=credit.bki_inquiries_3m,
        debt_to_revenue=debt_to_revenue,
        limit_utilization=credit.limit_utilization,
        business_age_months=months_between(egrul.registered_at, as_of) if egrul else None,
        okved_risk_group=policy.okved_risk_group(egrul.okved_main) if egrul else None,
        legal_form=legal_form,
        owner_or_director_changed_6m=(owner_or_director_changed(egrul, as_of, policy.change_window_months)
                                      if egrul else None),
        margin=(round(financials.net_profit / financials.revenue, 3)
                if financials and financials.revenue > 0 else None),
        negative_equity=financials.equity < 0 if financials else None,
    )


def features_from_responses(responses: list, as_of: date | None = None,
                            policy: FeaturePolicy | None = None) -> Features:
    """The live engine's entry point: features from enrichment's answers, as of the day they came."""
    as_of = as_of or snapshot_date(responses)
    if as_of is None:
        raise ValueError('no source answered: pass as_of')
    return extract_features(SourcePayloads.from_responses(responses), as_of, policy)
