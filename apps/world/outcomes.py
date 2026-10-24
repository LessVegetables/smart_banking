"""Default outcomes and the train/holdout split (SPEC §14, T05).

Every company gets a hidden default outcome: whether it defaults (90+ days overdue)
within the 12 months after as_of. Like q and the archetype, it is ground truth for
training (T10), validation (T11) and the personas (T12); the engine never sees it.

    pd_12m = sigmoid(a + b·q + industry offset)
    default_12m ~ Bernoulli(pd_12m)

- `b` is `quality_slope` from config/world_outcomes.json: how fast the odds of default
  fall as q grows.
- The industry offset is `industry_default_offsets` from the same file: how much
  riskier an industry is than the others at the same q.
- `a` is not set by hand. It is solved so that the expected default rate of the
  population (q ~ N(0, 1), industries by their weights) equals `target_default_rate`.
  The econ team sets the rate, not the intercept. The intercept depends only on the
  config, not on the generated companies, so growing the world keeps the outcomes of
  the companies already in it.
- A shell company does not mean to repay: its PD is `shell_default_probability`.
- An inflater is a real business: the formula applies with its own q. So do the
  data-gap archetypes: thin data is not a risk by itself (SPEC §1.2).

Every company also lands in the training or the holdout sample, by a draw that does
not depend on the outcome. Both draws come from the company's own `outcome` stream,
default first, then the split. Raising the target rate therefore only adds defaults,
and changing it never moves a company between samples.

SIMPLIFICATION: the outcome is drawn from the true PD, not simulated payment by
payment over the next 12 months. A real outcome is read from the loan book: the first
90+ day delinquency after the loan was issued.

SIMPLIFICATION: every company in the world gets an outcome, as if the bank had lent to
all of them. A real training sample only has outcomes for approved loans, and the bank
has to correct for the rejected applicants (reject inference). The scorecard (T10,
apps/ml/) trains on every company of the training sample.
"""
from __future__ import annotations

import json
import math
import random
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .industries import BY_KEY, INDUSTRIES, Industry

if TYPE_CHECKING:
    from .generator import Company, World

CONFIG_PATH = Path(__file__).resolve().parents[2] / 'config' / 'world_outcomes.json'

Sample = Literal['train', 'holdout']


class OutcomeConfig(BaseModel):
    """`config/world_outcomes.json`. Owned by the econ team."""
    model_config = ConfigDict(extra='forbid', frozen=True)

    version: str
    target_default_rate: float = Field(gt=0, lt=1)  # over all companies except shells
    quality_slope: float = Field(lt=0)  # change in the log-odds of default per +1 of q
    shell_default_probability: float = Field(ge=0, le=1)
    holdout_share: float = Field(gt=0, lt=1)
    industry_default_offsets: dict[str, float]  # added to the default logit, one per industry in industries.py

    @model_validator(mode='after')
    def _every_industry_has_an_offset(self) -> OutcomeConfig:
        if self.industry_default_offsets.keys() != BY_KEY.keys():
            raise ValueError(f'industry_default_offsets must list exactly the industries {sorted(BY_KEY)}')
        return self


def load_outcome_config(path: Path = CONFIG_PATH) -> OutcomeConfig:
    return OutcomeConfig.model_validate(json.loads(path.read_text(encoding='utf-8')))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


# Standard normal on a fine grid: the population of q, for the expected default rate.
_Q_STEP = 0.01
_Q_GRID = [k * _Q_STEP for k in range(-800, 801)]
_Q_WEIGHTS = [math.exp(-q * q / 2) for q in _Q_GRID]
_Q_TOTAL = sum(_Q_WEIGHTS)


def expected_default_rate(intercept: float, slope: float, offsets: dict[str, float]) -> float:
    """Mean PD over q ~ N(0, 1) and the industry mix of the world."""
    total_weight = sum(i.weight for i in INDUSTRIES)
    rate = 0.0
    for industry in INDUSTRIES:
        offset = intercept + offsets[industry.key]
        mean = sum(w * _sigmoid(offset + slope * q) for q, w in zip(_Q_GRID, _Q_WEIGHTS)) / _Q_TOTAL
        rate += industry.weight / total_weight * mean
    return rate


def default_intercept(config: OutcomeConfig) -> float:
    """The intercept `a` at which the expected default rate equals the target."""
    return _intercept(config.target_default_rate, config.quality_slope,
                      tuple(sorted(config.industry_default_offsets.items())))


@lru_cache(maxsize=None)
def _intercept(target_default_rate: float, slope: float, offsets: tuple[tuple[str, float], ...]) -> float:
    low, high = -20.0, 20.0  # bisection: the expected rate grows with the intercept
    for _ in range(100):
        mid = (low + high) / 2
        if expected_default_rate(mid, slope, dict(offsets)) < target_default_rate:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def default_probability(config: OutcomeConfig, *, q: float, industry: Industry, archetype: str) -> float:
    # SIMPLIFICATION: PD depends only on q, the industry and the archetype. Real defaults also
    # follow events after the loan is issued: a lost key customer, new debt, an industry crisis.
    if archetype == 'shell':
        return config.shell_default_probability
    a = default_intercept(config)
    return _sigmoid(a + config.quality_slope * q + config.industry_default_offsets[industry.key])


def draw_outcome(rng: random.Random, config: OutcomeConfig, *, q: float, industry: Industry,
                 archetype: str) -> tuple[float, bool, Sample]:
    """(pd_12m, default_12m, sample) for one company, from its `outcome` stream."""
    pd = round(default_probability(config, q=q, industry=industry, archetype=archetype), 6)
    defaulted = rng.random() < pd
    # SIMPLIFICATION: a random split at one as_of. A bank would also hold out a later period
    # (out-of-time) to check that the model stays stable.
    sample: Sample = 'holdout' if rng.random() < config.holdout_share else 'train'
    return pd, defaulted, sample


def train_holdout(world: World) -> tuple[list[Company], list[Company]]:
    """The training and the holdout companies, in world order."""
    train = [x for x in world.companies if x.sample == 'train']
    holdout = [x for x in world.companies if x.sample == 'holdout']
    return train, holdout
