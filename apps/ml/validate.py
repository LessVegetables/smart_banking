"""Validation of a scorecard on the synthetic world (T11): the holdout against the training sample.

Features come from the shared library, the same call as training and the live engine. The
world's hidden truth gives only the outcomes (`default_12m`), the split (`sample`) and, for
the Gini ceiling, the true PD (`pd_12m`) — aggregated, never per company.
"""
from __future__ import annotations

from apps.api.contracts import Features, Scorecard
from apps.api.features import SourcePayloads, extract_features
from apps.world import World, train_holdout

from .score import find_bin, gini, total_points
from .train import world_info
from .validation import (GiniResult, PsiResult, SampleInfo, Thresholds, ValidationReport, band_label, band_labels,
                         calibration, cap_curve, gini_interval, psi, score_bands)

LIMITATIONS = [
    # SIMPLIFICATION: a random holdout of the same snapshot, not an out-of-time sample.
    'Отложенная выборка — случайная часть того же среза мира, что и обучающая, а не более поздний период '
    '(out-of-time). Поэтому Gini на ней ближе к обучающему, чем было бы в реальности.',
    # SIMPLIFICATION: PSI of the holdout against the training sample, not of the application flow.
    'PSI сравнивает отложенную выборку с обучающей. При случайном разбиении он должен быть около нуля: '
    'это проверка, что выборки сопоставимы, а не мониторинг потока заявок.',
    'Карта откалибрована на долю дефолтов обучающей выборки. Если в отложенной выборке дефолтов случайно '
    'больше, прогнозная PD в грейдах оказывается ниже фактической доли; карту мы не перекалибровываем.',
    'Модель обучена и проверена на синтетических данных: её качество доказывает работу конвейера обучения, '
    'а не реальный риск (SPEC §15).',
]


def _sample(name: str, bads: list[bool]) -> SampleInfo:
    return SampleInfo(name=name, companies=len(bads), defaults=sum(bads), default_rate=round(sum(bads) / len(bads), 4))


def _feature_psi(scorecard: Scorecard, train: list[Features], holdout: list[Features]) -> list[PsiResult]:
    out = []
    for f in scorecard.features:
        def bin_of(x: Features) -> str:
            return find_bin(f, getattr(x, f.name)).label
        out.append(psi(f.name, f.label, [b.label for b in f.bins], [bin_of(x) for x in train],
                       [bin_of(x) for x in holdout]))
    return out


def validate_scorecard(world: World, scorecard: Scorecard) -> ValidationReport:
    train_companies, holdout_companies = train_holdout(world)
    as_of = world.meta.as_of
    train = [extract_features(SourcePayloads.from_company(c), as_of) for c in train_companies]
    holdout = [extract_features(SourcePayloads.from_company(c), as_of) for c in holdout_companies]
    feature_sets = {f.feature_set_version for f in [*train, *holdout]}
    if len(feature_sets) != 1:
        raise ValueError(f'mixed feature-set versions: {sorted(feature_sets)}')
    train_bads = [c.default_12m for c in train_companies]
    holdout_bads = [c.default_12m for c in holdout_companies]
    train_points = [total_points(scorecard, f) for f in train]
    holdout_points = [total_points(scorecard, f) for f in holdout]
    holdout_pds = [scorecard.pd_from_points(p) for p in holdout_points]

    g = gini(holdout_points, holdout_bads)
    low, high = gini_interval(g, sum(holdout_bads), len(holdout_bads) - sum(holdout_bads))
    true_ceiling = gini([-c.pd_12m for c in holdout_companies], holdout_bads)

    def grade_of(points: float) -> str:
        return scorecard.grade_for_pd(scorecard.pd_from_points(points)).value

    cuts = score_bands(train_points)
    return ValidationReport(
        scorecard_version=scorecard.version,
        feature_set_version=feature_sets.pop(),
        world=world_info(world),
        samples=[_sample('train', train_bads), _sample('holdout', holdout_bads)],
        thresholds=Thresholds(),
        gini=GiniResult(holdout=round(g, 4), holdout_ci_low=round(low, 4), holdout_ci_high=round(high, 4),
                        train=round(gini(train_points, train_bads), 4), true_pd_ceiling=round(true_ceiling, 4)),
        cap=cap_curve(holdout_points, holdout_bads),
        calibration=calibration(scorecard, holdout_pds, holdout_bads),
        psi_grades=psi('grade', 'Грейд', [x.grade.value for x in scorecard.grades],
                       [grade_of(p) for p in train_points], [grade_of(p) for p in holdout_points]),
        psi_points=psi('points', 'Баллы', band_labels(cuts), [band_label(cuts, p) for p in train_points],
                       [band_label(cuts, p) for p in holdout_points]),
        psi_features=_feature_psi(scorecard, train, holdout),
        limitations=LIMITATIONS,
    )
