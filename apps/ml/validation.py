"""Validation metrics of a scorecard (T11, SPEC §14) and the report the risk-manager page shows.

The three properties the case names for rating models:

- discrimination — Gini on the holdout, its 95% interval and the CAP curve;
- accuracy — predicted PD against the actual default rate in every grade;
- stability — PSI of grades and score bands, and of every feature of the card (CSI).

Needs only the contracts, like `score.py`: the API can read the report without loading the
world. Running it on the world is `validate.py`.
"""
from __future__ import annotations

import math
from typing import Literal

from apps.api.contracts import Grade, Scorecard

from .report import WorldInfo, _Report

# SIMPLIFICATION: conventional thresholds, kept here rather than in config/; a bank's validation
# unit sets them. Credit policy: the econ team confirms them.
PSI_WATCH = 0.1  # 0.1–0.25: the population moved, look at what changed
PSI_SHIFT = 0.25  # from 0.25: the model is applied to a different population
Z_95 = 1.959964
SCORE_BANDS = 10  # score bands of equal size on the reference sample
CAP_POINTS = 20
_SHARE_FLOOR = 1e-4  # an empty bucket would make PSI infinite

PsiStatus = Literal['stable', 'watch', 'shift']
CalibrationStatus = Literal['within', 'below', 'above']


class SampleInfo(_Report):
    name: Literal['train', 'holdout']
    companies: int
    defaults: int
    default_rate: float


class GiniResult(_Report):
    holdout: float
    holdout_ci_low: float
    holdout_ci_high: float
    train: float
    true_pd_ceiling: float  # Gini of the world's true PD on the holdout: the best any model can do


class CapPoint(_Report):
    """Cumulative accuracy profile: rejecting the riskiest `companies_share`, the bank
    avoids `defaults_share` of all defaults."""
    companies_share: float
    defaults_share: float


class GradeCalibration(_Report):
    grade: Grade
    pd_min: float
    pd_max: float | None
    companies: int
    companies_share: float
    defaults: int
    predicted_pd: float | None  # mean PD of the companies in the grade
    actual_rate: float | None
    actual_ci_low: float | None  # Wilson interval of the actual rate
    actual_ci_high: float | None
    predicted_vs_actual: CalibrationStatus | None  # 'below': the card underestimates the risk


class CalibrationResult(_Report):
    predicted_pd: float
    actual_rate: float
    grades: list[GradeCalibration]


class PsiBucket(_Report):
    label: str
    expected_share: float  # training sample
    actual_share: float  # holdout
    contribution: float


class PsiResult(_Report):
    name: str
    label: str
    value: float
    status: PsiStatus
    buckets: list[PsiBucket]


class Thresholds(_Report):
    psi_watch: float = PSI_WATCH
    psi_shift: float = PSI_SHIFT
    confidence: float = 0.95


class ValidationReport(_Report):
    """`scorecard_vN.validation.json`, next to the scorecard: what the risk-manager page (T32) shows."""
    scorecard_version: str
    feature_set_version: str
    world: WorldInfo
    samples: list[SampleInfo]
    thresholds: Thresholds
    gini: GiniResult
    cap: list[CapPoint]
    calibration: CalibrationResult
    psi_grades: PsiResult
    psi_points: PsiResult
    psi_features: list[PsiResult]  # characteristic stability, one per feature of the card
    limitations: list[str]


def _r(x: float) -> float:
    return round(x, 4)


# --- Discrimination -----------------------------------------------------------------

def gini_interval(g: float, bads: int, goods: int) -> tuple[float, float]:
    """95% interval of Gini from the standard error of AUC (Hanley & McNeil, 1982)."""
    auc = (g + 1) / 2
    q1, q2 = auc / (2 - auc), 2 * auc ** 2 / (1 + auc)
    se = math.sqrt((auc * (1 - auc) + (bads - 1) * (q1 - auc ** 2) + (goods - 1) * (q2 - auc ** 2)) / (bads * goods))
    return 2 * (auc - Z_95 * se) - 1, 2 * (auc + Z_95 * se) - 1


def cap_curve(scores: list[float], bads: list[bool], points: int = CAP_POINTS) -> list[CapPoint]:
    """Companies from the riskiest (lowest score) up; ties keep the input order."""
    ranked = [bad for _, bad in sorted(zip(scores, bads), key=lambda p: p[0])]
    total_bads = sum(ranked)
    out = []
    for k in range(points + 1):
        n = len(ranked) * k // points
        out.append(CapPoint(companies_share=_r(k / points), defaults_share=_r(sum(ranked[:n]) / total_bads)))
    return out


# --- Accuracy -----------------------------------------------------------------------

def wilson_interval(defaults: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Confidence interval of a default rate; unlike p ± z·se it stays inside [0, 1] for few defaults."""
    p = defaults / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def calibration(scorecard: Scorecard, pds: list[float], bads: list[bool]) -> CalibrationResult:
    grades = []
    lower = 0.0
    for bound in scorecard.grades:
        members = [(pd, bad) for pd, bad in zip(pds, bads) if scorecard.grade_for_pd(pd) == bound.grade]
        n, d = len(members), sum(bad for _, bad in members)
        row = GradeCalibration(grade=bound.grade, pd_min=lower, pd_max=bound.pd_max, companies=n,
                               companies_share=_r(n / len(pds)), defaults=d, predicted_pd=None, actual_rate=None,
                               actual_ci_low=None, actual_ci_high=None, predicted_vs_actual=None)
        if n:
            predicted = sum(pd for pd, _ in members) / n
            low, high = wilson_interval(d, n)
            row.predicted_pd, row.actual_rate = _r(predicted), _r(d / n)
            row.actual_ci_low, row.actual_ci_high = _r(low), _r(high)
            row.predicted_vs_actual = 'below' if predicted < low else 'above' if predicted > high else 'within'
        grades.append(row)
        lower = bound.pd_max or lower
    return CalibrationResult(predicted_pd=_r(sum(pds) / len(pds)), actual_rate=_r(sum(bads) / len(bads)),
                             grades=grades)


# --- Stability ----------------------------------------------------------------------

def psi_status(value: float) -> PsiStatus:
    return 'shift' if value >= PSI_SHIFT else 'watch' if value >= PSI_WATCH else 'stable'


def psi(name: str, label: str, buckets: list[str], expected: list[str], actual: list[str]) -> PsiResult:
    """PSI = sum((actual − expected) · ln(actual / expected)) over buckets, where expected
    and actual are the shares of the reference (training) and the compared population."""
    rows = []
    for b in buckets:
        e = expected.count(b) / len(expected)
        a = actual.count(b) / len(actual)
        ef, af = max(e, _SHARE_FLOOR), max(a, _SHARE_FLOOR)
        rows.append(PsiBucket(label=b, expected_share=_r(e), actual_share=_r(a),
                              contribution=_r((af - ef) * math.log(af / ef))))
    value = _r(sum(r.contribution for r in rows))
    return PsiResult(name=name, label=label, value=value, status=psi_status(value), buckets=rows)


def score_bands(reference: list[float], bands: int = SCORE_BANDS) -> list[float]:
    """Cut points that split the reference scores into bands of equal size (fewer on ties)."""
    values = sorted(reference)
    return sorted({values[len(values) * k // bands] for k in range(1, bands)} - {values[0]})


def _band_name(lo: float | None, hi: float | None) -> str:
    if lo is None and hi is None:
        return 'любое значение'
    if lo is None:
        return f'меньше {hi:g}'.replace('.', ',')
    if hi is None:
        return f'{lo:g} и больше'.replace('.', ',')
    return f'{lo:g}–{hi:g}'.replace('.', ',')


def _bands(cuts: list[float]) -> list[tuple[float | None, float | None]]:
    edges = [None, *cuts, None]
    return list(zip(edges, edges[1:]))


def band_labels(cuts: list[float]) -> list[str]:
    return [_band_name(lo, hi) for lo, hi in _bands(cuts)]


def band_label(cuts: list[float], score: float) -> str:
    """[lower, upper), like the scorecard's numeric bins."""
    return next(_band_name(lo, hi) for lo, hi in _bands(cuts) if hi is None or score < hi)
