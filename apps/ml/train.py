"""Scorecard training (SPEC §8, §14): WoE binning → logistic regression → points.

    ln(odds_good) = b0 + sum(b_i * WoE_i)
    points        = offset + factor * ln(odds_good),  factor = pdo / ln 2,
                    offset = base_points - factor * ln(base_odds)
    bin points    = factor * b_i * WoE of the bin; intercept_points = offset + factor * b0

So the points of a bin say how much safer (+) or riskier (−) than average it is, and the
total maps back to PD with `Scorecard.pd_from_points`.

Features come from the shared library (`apps/api/features`), the same call the live
engine makes; the world's hidden truth gives only the label (`default_12m`) and the
split (`sample`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from apps.api.contracts import Features, GradeBound, Scorecard, ScorecardBin, ScorecardFeature, ScorecardScaling
from apps.api.features import SourcePayloads, extract_features
from apps.world import World, train_holdout

from .binning import Binning, BinningSettings, bin_feature
from .labels import CATEGORIES, FEATURE_LABELS, MISSING_LABEL, category_label, numeric_bin_label
from .logreg import fit_logistic
from .score import gini, total_points

FEATURES = [name for name in Features.model_fields if name != 'feature_set_version']

# Grade bounds of SPEC §8 (PD over 12 months). Credit policy: the econ team confirms them.
GRADES = [GradeBound(grade='A', pd_max=0.015), GradeBound(grade='B', pd_max=0.03),
          GradeBound(grade='C', pd_max=0.06), GradeBound(grade='D', pd_max=0.12), GradeBound(grade='E', pd_max=None)]


# SIMPLIFICATION: textbook binning and selection settings, not tuned by cross-validation;
# a bank would also have underwriters review the bins.
@dataclass(frozen=True)
class TrainingSettings:
    binning: BinningSettings = field(default_factory=BinningSettings)
    min_iv: float = 0.02  # below this information value a feature doesn't discriminate
    min_z: float = 2.0  # coefficient / standard error; also drops coefficients with the wrong sign
    ridge: float = 1e-3


class _Report(BaseModel):
    model_config = ConfigDict(extra='forbid')


class WorldInfo(_Report):
    generator_version: str
    seed: int
    n: int
    as_of: date
    outcomes_version: str


class FeatureReport(_Report):
    name: str
    iv: float
    selected: bool
    coefficient: float | None = None
    z: float | None = None
    dropped: Literal['low_iv', 'wrong_sign', 'not_significant'] | None = None


class TrainingReport(_Report):
    """`scorecard_vN.training.json`, next to the scorecard: what it was trained on and how.
    `feature_set_version` lives here because the `Scorecard` contract has no field for it."""
    scorecard_version: str
    feature_set_version: str
    world: WorldInfo
    companies: int
    defaults: int
    settings: dict
    features: list[FeatureReport]
    gini_train: float


@dataclass(frozen=True)
class TrainingResult:
    scorecard: Scorecard
    report: TrainingReport


def training_sample(world: World) -> tuple[list[Features], list[bool]]:
    """Features and default flags of the training sample.

    SIMPLIFICATION: every company of the `train` sample is used, as if the bank had lent to
    all applicants (shell companies included: they apply too). A bank only knows outcomes
    of the loans it issued and corrects for the rejected applicants (reject inference)."""
    train, _ = train_holdout(world)
    features = [extract_features(SourcePayloads.from_company(c), world.meta.as_of) for c in train]
    return features, [c.default_12m for c in train]


def _select(binnings: list[Binning], features: list[Features], bads: list[bool], s: TrainingSettings):
    """Backward elimination: drop the weakest feature until every coefficient is positive
    (WoE points the right way) and significant."""
    reports = {b.name: FeatureReport(name=b.name, iv=round(b.iv, 4), selected=b.iv >= s.min_iv) for b in binnings}
    for r in reports.values():
        if not r.selected:
            r.dropped = 'low_iv'
    selected = [b for b in binnings if reports[b.name].selected]
    goods = [int(not bad) for bad in bads]
    while True:
        x = [[b.woe(getattr(f, b.name)) for b in selected] for f in features]
        fit = fit_logistic(x, goods, ridge=s.ridge)
        z = [fit.z(i) for i in range(len(selected))]
        worst = min(range(len(selected)), key=lambda i: z[i])
        if z[worst] >= s.min_z:
            break
        r = reports[selected[worst].name]
        r.selected, r.dropped = False, 'wrong_sign' if fit.coefficients[worst] <= 0 else 'not_significant'
        selected.pop(worst)
    for i, b in enumerate(selected):
        reports[b.name].coefficient = round(fit.coefficients[i], 4)
        reports[b.name].z = round(z[i], 2)
    return selected, fit, [reports[b.name] for b in binnings]


def _scorecard_feature(b: Binning, coefficient: float, factor: float) -> ScorecardFeature:
    bins = []
    for x in b.bins:
        if x.missing:
            label = MISSING_LABEL
        elif b.kind == 'categorical':
            label = category_label(x.categories)
        else:
            label = numeric_bin_label(b.name, x.lower, x.upper)
        woe = None if x.woe is None else round(x.woe, 4)
        bins.append(ScorecardBin(label=label, lower=x.lower, upper=x.upper,
                                 categories=x.categories if b.kind == 'categorical' and not x.missing else None,
                                 missing=x.missing, woe=woe, points=round(factor * coefficient * (woe or 0.0), 1)))
    return ScorecardFeature(name=b.name, label=FEATURE_LABELS[b.name], kind=b.kind, bins=bins)


def train_scorecard(world: World, *, version: str = 'scorecard_v1', created_at: date,
                    settings: TrainingSettings | None = None) -> TrainingResult:
    s = settings or TrainingSettings()
    features, bads = training_sample(world)
    feature_sets = {f.feature_set_version for f in features}
    if len(feature_sets) != 1:
        raise ValueError(f'mixed feature-set versions: {sorted(feature_sets)}')
    binnings = [bin_feature(name, [getattr(f, name) for f in features], bads, categories=CATEGORIES.get(name),
                            settings=s.binning) for name in FEATURES]
    selected, fit, feature_reports = _select(binnings, features, bads, s)

    scaling = ScorecardScaling()
    factor = scaling.pdo / math.log(2)
    offset = scaling.base_points - factor * math.log(scaling.base_odds)
    meta = world.meta
    defaults = sum(bads)
    # SIMPLIFICATION: calibrated to the default rate of one snapshot of the training sample,
    # not to a through-the-cycle average; trained on synthetic data (SPEC §15).
    scorecard = Scorecard(
        version=version,
        origin='trained',
        created_at=created_at,
        description=(f'Логистическая регрессия на WoE, обучена на синтетическом мире (зерно {meta.seed}, '
                     f'{meta.n} компаний, срез {meta.as_of.isoformat()}): {len(features)} компаний обучающей '
                     f'выборки, {defaults} дефолтов. Набор признаков {feature_sets.pop()}.'),
        scaling=scaling,
        intercept_points=round(offset + factor * fit.intercept, 1),
        features=[_scorecard_feature(b, fit.coefficients[i], factor) for i, b in enumerate(selected)],
        grades=GRADES,
    )
    report = TrainingReport(
        scorecard_version=version,
        feature_set_version=features[0].feature_set_version,
        world=WorldInfo(generator_version=meta.generator_version, seed=meta.seed, n=meta.n, as_of=meta.as_of,
                        outcomes_version=meta.outcomes.version),
        companies=len(features),
        defaults=defaults,
        settings={'min_iv': s.min_iv, 'min_z': s.min_z, 'ridge': s.ridge, **s.binning.__dict__},
        features=feature_reports,
        gini_train=round(gini([total_points(scorecard, f) for f in features], bads), 4),
    )
    return TrainingResult(scorecard=scorecard, report=report)
