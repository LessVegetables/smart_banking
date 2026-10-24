"""Credit policy for the limit and the rate (T16, SPEC §4, §9): `config/credit_policy.json`.

DSCR minimums, leverage and collateral multipliers, rate components, the LGD table,
processing costs, the rate cap and the product ranges are policy, not code. The econ
team owns the values; `version` says which policy a limit and a price were built on.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.api.contracts import Grade, ProductId, Route, Share

CONFIG_PATH = Path(__file__).resolve().parents[2] / 'config' / 'credit_policy.json'


class _Config(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class LimitPolicy(_Config):
    cfads_cv_cap: Share  # CFADS = median flow * (1 - min(cap, CV))
    dscr_min: dict[Grade, float]
    leverage_multiplier: Share  # of annual revenue; a product may override it
    collateral_ltv: Share
    turnover_multiplier: Share  # of average monthly operating inflows, overdraft only
    amount_step: int = Field(gt=0)  # cash-flow limits and offered amounts are rounded down to it

    @model_validator(mode='after')
    def _all_grades(self) -> LimitPolicy:
        if set(self.dscr_min) != set(Grade):
            raise ValueError('dscr_min needs every grade A..E')
        if any(v <= 0 for v in self.dscr_min.values()):
            raise ValueError('dscr_min must be positive')
        return self


class LgdTable(_Config):
    unsecured: Share
    owner_guarantee: Share
    equipment_pledge: Share


class PricingPolicy(_Config):
    # SIMPLIFICATION: funding = key rate + a fixed spread; a bank's treasury sets transfer prices by term.
    key_rate: Share
    funding_spread: Share
    capital: Share
    margin: Share
    rate_cap: Share
    lgd: LgdTable
    msp_guarantee_lgd_factor: Share  # MOCK: the Корпорация МСП guarantee covers this share of the risk
    msp_guarantee_fee: Share  # annual, paid on top of the rate
    processing_cost: dict[Route, int]

    @model_validator(mode='after')
    def _all_routes(self) -> PricingPolicy:
        if set(self.processing_cost) != set(Route):
            raise ValueError('processing_cost needs every route')
        return self

    @property
    def funding(self) -> float:
        return self.key_rate + self.funding_spread


class ProductPolicy(_Config):
    min_amount: int = Field(gt=0)
    max_amount: int = Field(gt=0)
    min_term: int = Field(ge=1)
    max_term: int = Field(ge=1)
    leverage_multiplier: float | None = Field(default=None, ge=0, le=1)
    min_down_payment_share: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode='after')
    def _ranges(self) -> ProductPolicy:
        if self.min_amount > self.max_amount or self.min_term > self.max_term:
            raise ValueError('product minimums must not exceed maximums')
        return self


class CreditPolicy(_Config):
    version: str
    limit: LimitPolicy
    pricing: PricingPolicy
    products: dict[ProductId, ProductPolicy]

    @model_validator(mode='after')
    def _all_products(self) -> CreditPolicy:
        if set(self.products) != set(ProductId):
            raise ValueError('products needs every product')
        return self


def load_policy(path: Path = CONFIG_PATH) -> CreditPolicy:
    return CreditPolicy.model_validate(json.loads(path.read_text(encoding='utf-8')))


@lru_cache(maxsize=1)
def default_policy() -> CreditPolicy:
    return load_policy()
