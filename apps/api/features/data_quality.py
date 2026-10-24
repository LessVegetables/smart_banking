"""Account data quality (SPEC §5.1): four metrics, each with its tier, and the worst of them.

Being a bank client doesn't mean the bank sees the business. The tier drives the limit
(T16), routing (T18), pre-approval (T22) and the request for statements (T23):

- `history_months`: months in our account statement;
- `revenue_coverage`: operating inflows of the last 12 months / annual revenue from ФНС
  (`fns_revenue(...).annualized`), or the declared revenue when ФНС has none. With a
  shorter history the inflows are scaled to 12 months, so a short history lowers the
  history metric, not the coverage too;
- `operating_inflow_share`: inflows from customers / all inflows of the last 12 months
  (own transfers, loans received and founder contributions are not business income);
- `regularity_months`: months with inflows from customers among the last 12, not scaled.

A metric that can't be computed (no account statement, no revenue to compare with) is
THIN. Thresholds are credit policy, in `config/data_quality.json`; they don't change the
features, so changing them needs no retraining.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from apps.api.contracts import DataQuality, DataQualityMetric, DataQualityTier

from .accounts import WINDOW, window
from .extract import SourcePayloads
from .revenue import fns_revenue

CONFIG_PATH = Path(__file__).resolve().parents[3] / 'config' / 'data_quality.json'

METRICS = ('history_months', 'revenue_coverage', 'operating_inflow_share', 'regularity_months')

FULL_MESSAGE = 'Мы видим почти все обороты вашего бизнеса — этого достаточно для автоматического решения.'
REASONS = {  # why the worst metric is low, in the client's words
    'history_months': 'счёт открыт недавно',
    'revenue_coverage': 'похоже, часть выручки проходит через другие банки',
    'operating_inflow_share': 'заметная часть поступлений — не выручка, а переводы со своих счетов и займы',
    'regularity_months': 'выручка поступает на счёт не каждый месяц',
}
NO_DATA_REASONS = {
    'history_months': 'у нас нет оборотов по вашему счёту',
    'revenue_coverage': 'нам не с чем сравнить обороты по счёту: нет данных о выручке',
}


class _Config(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Thresholds(_Config):
    full: float  # from this value up: FULL
    partial: float  # from this value up: PARTIAL; below: THIN

    @model_validator(mode='after')
    def _ordered(self) -> Thresholds:
        if self.partial > self.full:
            raise ValueError('partial threshold must not be above full')
        return self

    def tier(self, value: float | None) -> DataQualityTier:
        if value is None:
            return DataQualityTier.THIN
        if value >= self.full:
            return DataQualityTier.FULL
        return DataQualityTier.PARTIAL if value >= self.partial else DataQualityTier.THIN


class DataQualityPolicy(_Config):
    # SIMPLIFICATION: SPEC §5.1 thresholds set by experts; a bank calibrates them on its portfolio.
    version: str  # goes to DataQuality.policy_version
    history_months: Thresholds
    revenue_coverage: Thresholds
    operating_inflow_share: Thresholds
    regularity_months: Thresholds


def load_policy(path: Path = CONFIG_PATH) -> DataQualityPolicy:
    return DataQualityPolicy.model_validate(json.loads(path.read_text(encoding='utf-8')))


@lru_cache(maxsize=1)
def default_policy() -> DataQualityPolicy:
    return load_policy()


def data_quality(payloads: SourcePayloads, declared_revenue: int | None = None,
                 policy: DataQualityPolicy | None = None) -> DataQuality:
    """`declared_revenue`: the client's `ApplicationRequest.declared_annual_revenue`; None without
    an application (pre-approval)."""
    policy = policy or default_policy()
    accounts = payloads.accounts
    months = window(accounts) if accounts else []
    customers = sum(m.inflows.customers for m in months)
    inflows = sum(m.inflows.total for m in months)

    revenue = fns_revenue(payloads.fns, payloads.egrul, accounts)
    if revenue is not None and revenue.annualized > 0:
        basis, annual_revenue = 'fns', revenue.annualized
    elif declared_revenue:
        basis, annual_revenue = 'declared', declared_revenue
    else:
        basis, annual_revenue = None, None

    values = {
        # SIMPLIFICATION: every month of the statement counts as a month with operations; the world
        # has no dormant months. A real account can sit idle, and those months shouldn't count.
        'history_months': float(len(accounts.months)) if accounts else None,
        'revenue_coverage': (round(customers * WINDOW / len(months) / annual_revenue, 3)
                             if months and annual_revenue else None),
        'operating_inflow_share': round(customers / inflows, 3) if inflows else None,
        'regularity_months': float(sum(m.inflows.customers > 0 for m in months)) if accounts else None,
    }
    metrics = {name: DataQualityMetric(value=values[name], tier=getattr(policy, name).tier(values[name]))
               for name in METRICS}
    worst = max(METRICS, key=lambda name: metrics[name].tier.rank)  # the first of the worst, in METRICS order
    tier = metrics[worst].tier
    return DataQuality(**metrics, revenue_basis=basis, tier=tier,
                       client_message=client_message(tier, worst, values[worst] is None),
                       policy_version=policy.version)


def client_message(tier: DataQualityTier, worst: str, no_data: bool) -> str:
    if tier == DataQualityTier.FULL:
        return FULL_MESSAGE
    reason = NO_DATA_REASONS.get(worst, REASONS[worst]) if no_data else REASONS[worst]
    if tier == DataQualityTier.PARTIAL:
        return f'Мы видим не все обороты вашего бизнеса: {reason}. Выписка из другого банка поможет увеличить лимит.'
    return (f'Мы видим только часть оборотов вашего бизнеса: {reason}. '
            'Чтобы рассчитать лимит, понадобятся выписки из других банков.')


def data_quality_from_responses(responses: Iterable, declared_revenue: int | None = None,
                                policy: DataQualityPolicy | None = None) -> DataQuality:
    """The live engine's entry point: data quality from enrichment's answers."""
    return data_quality(SourcePayloads.from_responses(responses), declared_revenue, policy)
