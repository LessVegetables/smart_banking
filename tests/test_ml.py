"""Scorecard training (T10) and validation (T11): WoE binning, logistic regression, points, validation
metrics, the committed scorecard_v1."""
import math
import random
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from apps.api.contracts import Scorecard
from apps.api.features import SourcePayloads, default_policy, extract_features
from apps.ml import gini, total_points
from apps.ml.__main__ import main
from apps.ml.binning import bin_feature
from apps.ml.labels import numeric_bin_label
from apps.ml.logreg import fit_logistic
from apps.ml.train import GRADES, TrainingReport, train_scorecard, training_sample
from apps.ml.validate import validate_scorecard
from apps.ml.validation import (ValidationReport, band_label, band_labels, calibration, cap_curve, gini_interval, psi,
                                psi_status, score_bands, wilson_interval)
from apps.world import generate_world, train_holdout

AS_OF = date(2026, 10, 1)
CREATED = date(2026, 10, 24)
ML = Path(__file__).resolve().parents[1] / 'apps' / 'ml'


def _risky_with_value(n=4000, seed=1):
    rng = random.Random(seed)
    values = [rng.uniform(0, 100) for _ in range(n)]
    bads = [rng.random() < 0.02 + 0.002 * v for v in values]  # 2% → 22%
    return values, bads


# --- Binning ------------------------------------------------------------------------

def test_numeric_bins_are_monotonic_and_large_enough():
    values, bads = _risky_with_value()
    b = bin_feature('inflow_cv_12m', values, bads)
    value_bins = [x for x in b.bins if not x.missing]
    assert 2 <= len(value_bins) <= 5
    assert [x.woe for x in value_bins] == sorted((x.woe for x in value_bins), reverse=True)  # riskier as value grows
    assert all(x.total >= 0.05 * len(values) and x.bads >= 10 for x in value_bins)
    # Contiguous and open at both ends: every value the engine can see falls into one bin.
    assert value_bins[0].lower is None and value_bins[-1].upper is None
    assert all(a.upper == c.lower for a, c in zip(value_bins, value_bins[1:]))
    assert b.find(-1e9) is value_bins[0] and b.find(1e9) is value_bins[-1]
    assert b.iv > 0.1


def test_missing_bin_gets_its_own_woe_only_with_enough_data():
    values, bads = _risky_with_value()
    few = bin_feature('inflow_cv_12m', [None if i < 20 else v for i, v in enumerate(values)], bads)
    assert few.find(None).woe is None and few.woe(None) == 0  # average risk: nothing to learn from
    many = bin_feature('inflow_cv_12m', [None if i < 800 else v for i, v in enumerate(values)], bads)
    assert many.find(None).woe is not None


def test_noise_feature_has_low_iv():
    rng = random.Random(2)
    bads = [rng.random() < 0.08 for _ in range(4000)]
    b = bin_feature('margin', [rng.random() for _ in bads], bads)
    assert b.iv < 0.02


def test_categorical_bins_cover_unseen_categories():
    bads = [i % 10 == 0 for i in range(2000)]
    values = ['low' if i % 2 else 'high' for i in range(2000)]
    b = bin_feature('okved_risk_group', values, bads, categories=['low', 'medium', 'high'])
    assert sorted(c for x in b.bins for c in x.categories) == ['high', 'low', 'medium']
    assert b.find('medium') is not None


# --- Logistic regression and Gini ---------------------------------------------------

def test_logistic_regression_recovers_coefficients():
    rng = random.Random(3)
    x = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(6000)]
    y = [int(rng.random() < 1 / (1 + math.exp(-(0.5 + 1.0 * a - 0.7 * b)))) for a, b in x]
    fit = fit_logistic(x, y, ridge=0)
    assert fit.intercept == pytest.approx(0.5, abs=0.1)
    assert fit.coefficients == pytest.approx([1.0, -0.7], abs=0.1)
    assert fit.z(0) > 10 and fit.z(1) < -10


def test_gini():
    bads = [True, True, False, False]
    assert gini([1, 2, 3, 4], bads) == 1
    assert gini([4, 3, 2, 1], bads) == -1
    assert gini([1, 1, 1, 1], bads) == 0


def test_bin_labels():
    assert numeric_bin_label('business_age_months', None, 15) == '14 и меньше'
    assert numeric_bin_label('business_age_months', 15, 30) == '15–29'
    assert numeric_bin_label('payroll_payment_months_12m', 11, 12) == '11'
    assert numeric_bin_label('business_age_months', 87, None) == '87 и больше'
    assert numeric_bin_label('inflow_cv_12m', 0.142, 0.174) == '0,142–0,174'
    assert numeric_bin_label('inflow_cv_12m', None, 0.142) == 'меньше 0,142'


# --- Training on the world ----------------------------------------------------------

@pytest.fixture(scope='module')
def world():
    return generate_world(seed=7, n=3000, as_of=AS_OF)


@pytest.fixture(scope='module')
def trained(world):
    return train_scorecard(world, created_at=CREATED)


def test_scorecard_follows_the_contract(trained):
    scorecard = trained.scorecard
    assert Scorecard.model_validate_json(scorecard.model_dump_json()) == scorecard
    assert scorecard.origin == 'trained'
    assert scorecard.grades == GRADES
    assert len(scorecard.features) >= 4
    assert [f.name for f in scorecard.features] == [f.name for f in trained.report.features if f.selected]
    assert trained.report.feature_set_version == default_policy().version


def test_points_are_calibrated_to_the_default_rate(world, trained):
    # The regression's mean PD equals the default rate of the training sample; points only rescale it.
    features, bads = training_sample(world)
    pds = [trained.scorecard.pd_from_points(total_points(trained.scorecard, f)) for f in features]
    assert sum(pds) / len(pds) == pytest.approx(sum(bads) / len(bads), abs=0.003)


def test_safer_than_average_bins_add_points(trained):
    # Every coefficient is positive, so the points of a bin have the sign of its WoE.
    for f in trained.scorecard.features:
        for b in f.bins:
            assert b.points * (b.woe or 0) >= 0
        assert next(b for b in f.bins if b.missing).woe is not None or f.bins[-1].points == 0


def test_scorecard_discriminates_on_the_holdout(world, trained):
    _, holdout = train_holdout(world)
    points = [total_points(trained.scorecard, extract_features(SourcePayloads.from_company(c), AS_OF))
              for c in holdout]
    bads = [c.default_12m for c in holdout]
    assert 0.35 < gini(points, bads) < gini([-c.pd_12m for c in holdout], bads)  # below the true-PD ceiling


def test_training_is_reproducible(world, trained):
    assert train_scorecard(world, created_at=CREATED) == trained


# --- Validation metrics -----------------------------------------------------------

def test_psi():
    same = psi('x', 'x', ['a', 'b'], ['a', 'b'] * 50, ['a', 'b'] * 50)
    assert same.value == 0 and same.status == 'stable'
    moved = psi('x', 'x', ['a', 'b'], ['a'] * 50 + ['b'] * 50, ['a'] * 70 + ['b'] * 30)
    expected = (0.7 - 0.5) * math.log(0.7 / 0.5) + (0.3 - 0.5) * math.log(0.3 / 0.5)
    assert moved.value == pytest.approx(expected, abs=1e-4)
    assert moved.status == 'watch'
    # A bucket empty in one population doesn't make PSI infinite.
    empty = psi('x', 'x', ['a', 'b', 'c'], ['a', 'b'] * 50, ['a', 'b', 'c'] * 30)
    assert math.isfinite(empty.value) and empty.status == 'shift'
    assert [psi_status(v) for v in (0.099, 0.1, 0.25)] == ['stable', 'watch', 'shift']


def test_wilson_interval():
    low, high = wilson_interval(10, 100)
    assert (low, high) == (pytest.approx(0.0552, abs=1e-4), pytest.approx(0.1744, abs=1e-4))
    low, high = wilson_interval(0, 50)
    assert low == 0 and 0 < high < 0.1  # no defaults still leaves room for some risk


def test_gini_interval_narrows_with_more_defaults():
    small, large = gini_interval(0.5, 20, 300), gini_interval(0.5, 200, 3000)
    assert small[0] < large[0] < 0.5 < large[1] < small[1]


def test_cap_curve():
    perfect = cap_curve([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [True, True] + [False] * 8, points=5)
    assert [(p.companies_share, p.defaults_share) for p in perfect] == [
        (0, 0), (0.2, 1), (0.4, 1), (0.6, 1), (0.8, 1), (1, 1)]


def test_calibration_by_grade():
    scorecard = Scorecard.model_validate_json((ML / 'scorecard_v1.json').read_text('utf-8'))
    pds = [0.01] * 100 + [0.2] * 100
    bads = [i < 1 for i in range(100)] + [i < 40 for i in range(100)]  # A: 1% as predicted; E: 40% against 20%
    result = calibration(scorecard, pds, bads)
    by_grade = {g.grade: g for g in result.grades}
    assert by_grade['A'].predicted_vs_actual == 'within' and by_grade['E'].predicted_vs_actual == 'below'
    assert by_grade['B'].companies == 0 and by_grade['B'].predicted_pd is None
    assert [(g.pd_min, g.pd_max) for g in result.grades][:2] == [(0, 0.015), (0.015, 0.03)]


def test_score_bands():
    cuts = score_bands(list(range(100)), bands=4)
    assert cuts == [25, 50, 75]
    assert band_labels(cuts) == ['меньше 25', '25–50', '50–75', '75 и больше']
    assert [band_label(cuts, x) for x in (-5, 25, 74.9, 1e6)] == ['меньше 25', '25–50', '50–75', '75 и больше']
    assert band_labels(score_bands([1.5] * 10)) == ['любое значение']


@pytest.fixture(scope='module')
def validation(world, trained):
    return validate_scorecard(world, trained.scorecard)


def test_validation_report(world, trained, validation):
    assert ValidationReport.model_validate_json(validation.model_dump_json()) == validation
    train, holdout = validation.samples
    assert holdout.companies == len(train_holdout(world)[1])
    assert validation.gini.train == pytest.approx(trained.report.gini_train, abs=1e-4)
    assert 0.35 < validation.gini.holdout < validation.gini.true_pd_ceiling
    assert validation.gini.holdout_ci_low < validation.gini.holdout < validation.gini.holdout_ci_high
    assert validation.cap[0].defaults_share == 0 and validation.cap[-1].defaults_share == 1
    assert sum(g.companies for g in validation.calibration.grades) == holdout.companies
    assert sum(g.defaults for g in validation.calibration.grades) == holdout.defaults
    # A random split of one snapshot: the holdout is the same population as the training sample.
    for p in [validation.psi_grades, validation.psi_points, *validation.psi_features]:
        assert p.status == 'stable'
        assert sum(b.expected_share for b in p.buckets) == pytest.approx(1, abs=1e-3)
    assert [p.name for p in validation.psi_features] == [f.name for f in trained.scorecard.features]
    assert len(validation.psi_points.buckets) == 10


def test_validation_needs_only_the_contracts():
    # The API can read the report for the risk-manager page (T32) without the world.
    code = 'import sys, apps.ml.validation; assert not any(m.startswith("apps.world") for m in sys.modules)'
    subprocess.run([sys.executable, '-c', code], check=True, cwd=ML.parents[1])


# --- The committed scorecard --------------------------------------------------------

def test_scoring_needs_only_the_contracts():
    # The scoring adapter (T15) imports apps.ml in the image, where apps/world isn't copied.
    code = 'import sys, apps.ml; assert not any(m.startswith("apps.world") for m in sys.modules)'
    subprocess.run([sys.executable, '-c', code], check=True, cwd=ML.parents[1])


def test_committed_scorecard_matches_the_current_features():
    report = TrainingReport.model_validate_json((ML / 'scorecard_v1.training.json').read_text('utf-8'))
    assert report.feature_set_version == default_policy().version, \
        'config/features.json changed: retrain with `python -m apps.ml`'


def test_committed_scorecard_is_what_training_gives(tmp_path):
    committed = Scorecard.model_validate_json((ML / 'scorecard_v1.json').read_text('utf-8'))
    main(['--out', str(tmp_path), '--created-at', committed.created_at.isoformat()])
    for name in ('scorecard_v1.json', 'scorecard_v1.training.json', 'scorecard_v1.validation.json'):
        assert (tmp_path / name).read_text('utf-8') == (ML / name).read_text('utf-8'), \
            f'{name} is stale: retrain with `python -m apps.ml`'
