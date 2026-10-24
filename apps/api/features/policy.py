"""Credit-policy parameters of the feature library: `config/features.json`.

The overdue rules (SPEC §5), the inquiry and change windows, and the ОКВЭД risk groups
are policy, so they live in config/ and the econ team owns the values. The version of
the file is also the feature-set version (`Features.feature_set_version`): bump it when
the definitions in this package or the values in the file change, and retrain.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH = Path(__file__).resolve().parents[3] / 'config' / 'features.json'

RiskGroup = Literal['low', 'medium', 'high']


class _Config(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class OverdueRules(_Config):
    """Which delinquencies count, and with what weight (SPEC §5, §1.11)."""
    technical_days: int = Field(ge=0)  # up to this many days is a technical delay, ignored
    min_amount: int = Field(ge=0)  # rubles; a smaller amount is ignored
    min_payment_share: float = Field(ge=0, le=1)  # an amount below this share of the payment is ignored
    half_weight_after_months: int = Field(gt=0)  # older episodes count at half weight
    ignore_after_months: int = Field(gt=0)  # older episodes don't count

    @model_validator(mode='after')
    def _windows(self) -> OverdueRules:
        if self.ignore_after_months < self.half_weight_after_months:
            raise ValueError('ignore_after_months must not be shorter than half_weight_after_months')
        return self


class FeaturePolicy(_Config):
    version: str
    overdue: OverdueRules
    inquiry_window_months: int = Field(gt=0)  # bki_inquiries_3m
    change_window_months: int = Field(gt=0)  # owner_or_director_changed_6m (SPEC §7)
    # SIMPLIFICATION: groups for the world's dozen industries; a bank calibrates the whole ОКВЭД tree.
    okved_risk_groups: dict[RiskGroup, list[str]]  # ОКВЭД codes or prefixes ("41" covers "41.20")
    okved_default_risk_group: RiskGroup  # for a code no group lists

    @model_validator(mode='after')
    def _each_code_once(self) -> FeaturePolicy:
        codes = [code for group in self.okved_risk_groups.values() for code in group]
        if len(codes) != len(set(codes)):
            raise ValueError('an ОКВЭД code may be listed in one risk group only')
        return self

    def okved_risk_group(self, okved: str) -> RiskGroup:
        """The group of the longest listed code that is the ОКВЭД itself or its prefix."""
        matches = [(len(code), group) for group, codes in self.okved_risk_groups.items() for code in codes
                   if okved == code or okved.startswith(code + '.')]
        return max(matches)[1] if matches else self.okved_default_risk_group


def load_policy(path: Path = CONFIG_PATH) -> FeaturePolicy:
    return FeaturePolicy.model_validate(json.loads(path.read_text(encoding='utf-8')))


@lru_cache(maxsize=1)
def default_policy() -> FeaturePolicy:
    return load_policy()
