"""Scorecard training (T10): WoE binning, logistic regression, points, the committed scorecard_v1."""
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
    for name in ('scorecard_v1.json', 'scorecard_v1.training.json'):
        assert (tmp_path / name).read_text('utf-8') == (ML / name).read_text('utf-8'), \
            f'{name} is stale: retrain with `python -m apps.ml`'
