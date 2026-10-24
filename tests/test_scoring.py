"""T15 scoring adapter: scorecard files → ScoreResult, versioning, challenger in shadow mode."""
import json
import logging
from datetime import date
from pathlib import Path

import pytest

from apps.api.contracts import Decision, Features, Scorecard, ScoreResult
from apps.api.features import SourcePayloads, default_policy, extract_features
from apps.api.scoring import (DEFAULT_CHALLENGER, DEFAULT_CHAMPION, FeatureSetMismatch, Scorer, ScorecardModel,
                              load_scorecard_model, scorer_from_env)
from apps.ml import total_points
from apps.world import generate_world

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures'


def features(**changes) -> Features:
    value = json.loads((FIXTURES / 'features.json').read_text('utf-8'))
    value.update(changes)
    return Features.model_validate(value)


def all_missing() -> Features:
    return Features.model_validate({name: None for name in Features.model_fields} | {'feature_set_version': 'features_v0'})


@pytest.fixture(scope='module')
def champion():
    return load_scorecard_model(DEFAULT_CHAMPION)


@pytest.fixture(scope='module')
def challenger():
    return load_scorecard_model(DEFAULT_CHALLENGER)


@pytest.fixture(scope='module')
def world_features():
    world = generate_world(seed=11, n=300, as_of=date(2026, 10, 1))
    return [extract_features(SourcePayloads.from_company(c), world.meta.as_of) for c in world.companies]


class Broken:
    version = 'broken_v0'
    feature_set_version = 'features_v0'

    def predict(self, features):
        raise RuntimeError('model crashed')


# --- One card -----------------------------------------------------------------------

def test_points_match_training(champion, challenger, world_features):
    for model in (champion, challenger):
        for f in world_features:
            assert model.predict(f).points == pytest.approx(total_points(model.scorecard, f), abs=0.05)


def test_result_is_consistent(champion, world_features):
    for f in [features(), all_missing(), *world_features[:50]]:
        result = champion.predict(f)
        assert result.points == pytest.approx(champion.scorecard.intercept_points + sum(c.points for c in result.contributions))
        assert [c.feature for c in result.contributions] == [x.name for x in champion.scorecard.features]
        assert result.pd == champion.scorecard.pd_from_points(result.points)
        assert result.grade == champion.scorecard.grade_for_pd(result.pd)
        assert result.model_version == 'scorecard_v1'
        ScoreResult.model_validate(result.model_dump())


def test_missing_values_go_to_missing_bins(champion):
    result = champion.predict(all_missing())
    assert {c.bin_label for c in result.contributions} == {'нет данных'}
    assert all(c.value is None for c in result.contributions)


def test_contributions_explain_the_value():
    model = load_scorecard_model(FIXTURES / 'scorecard_example.json', feature_set_version='features_v0')
    result = model.predict(features())
    legal_form = next(c for c in result.contributions if c.feature == 'legal_form')
    assert (legal_form.value, legal_form.bin_label, legal_form.points) == ('ooo', 'ООО', 28.0)
    assert result.model_version == 'example_v0'


def test_refuses_other_feature_set(champion):
    with pytest.raises(FeatureSetMismatch):
        champion.predict(features(feature_set_version='features_v1'))


# --- Loading ------------------------------------------------------------------------

def test_feature_set_comes_from_the_passport(champion, challenger):
    assert champion.feature_set_version == default_policy().version
    assert challenger.feature_set_version == default_policy().version


def test_hand_set_card(challenger):
    card = challenger.scorecard
    assert (card.version, card.origin) == ('scorecard_v0', 'hand_set')
    assert all(b.woe is None for f in card.features for b in f.bins)
    assert challenger.predict(features()).grade.value in 'AB'  # the fixture company is a healthy one


def test_card_without_passport_needs_feature_set():
    with pytest.raises(FileNotFoundError):
        load_scorecard_model(FIXTURES / 'scorecard_example.json')


def test_passport_of_another_card(tmp_path):
    card = tmp_path / 'scorecard_v9.json'
    card.write_text((ROOT / DEFAULT_CHAMPION).read_text('utf-8'), 'utf-8')
    (tmp_path / 'scorecard_v9.training.json').write_text(
        json.dumps({'scorecard_version': 'scorecard_v9', 'feature_set_version': 'features_v0'}), 'utf-8')
    with pytest.raises(ValueError, match='describes scorecard_v9'):
        load_scorecard_model(card)


# --- Champion and challenger --------------------------------------------------------

def test_default_scorer_runs_v1_with_v0_in_shadow():
    scorer = scorer_from_env({})
    assert (scorer.champion.version, scorer.challenger.version) == ('scorecard_v1', 'scorecard_v0')
    scoring = scorer.score(features(), 'app-1')
    assert scoring.score == scorer.champion.predict(features())
    shadow = scorer.challenger.predict(features())
    assert (scoring.challenger.points, scoring.challenger.grade, scoring.challenger.model_version) == \
        (shadow.points, shadow.grade, 'scorecard_v0')


def test_challenger_off():
    assert scorer_from_env({'SCORECARD_CHALLENGER': 'none'}).challenger is None
    assert scorer_from_env({'SCORECARD_CHALLENGER': 'none'}).score(features()).challenger is None


def test_champion_from_env():
    scorer = scorer_from_env({'SCORECARD_CHAMPION': DEFAULT_CHALLENGER, 'SCORECARD_CHALLENGER': DEFAULT_CHAMPION})
    assert scorer.score(features()).score.model_version == 'scorecard_v0'


def test_broken_challenger_never_affects_champion(champion, caplog):
    expected = champion.predict(features())
    with caplog.at_level(logging.WARNING, logger='apps.api.scoring'):
        scoring = Scorer(champion, Broken()).score(features(), 'app-2')
    assert scoring.score == expected and scoring.challenger is None
    assert 'challenger broken_v0 failed for application app-2' in caplog.text


def test_challenger_on_another_feature_set_is_skipped(champion, challenger):
    other = ScorecardModel(challenger.scorecard, 'features_v1')
    assert Scorer(champion, other).score(features()).challenger is None


def test_unloadable_challenger_is_skipped(caplog):
    with caplog.at_level(logging.WARNING, logger='apps.api.scoring'):
        scorer = scorer_from_env({'SCORECARD_CHALLENGER': 'tests/fixtures/scorecard_example.json'})
    assert scorer.challenger is None and 'shadow mode off' in caplog.text


def test_champion_mismatch_refuses_to_score(champion, challenger):
    with pytest.raises(FeatureSetMismatch):
        Scorer(champion, challenger).score(features(feature_set_version='features_v1'))


def test_scoring_fills_the_decision():
    scoring = scorer_from_env({}).score(features())
    decision = json.loads((FIXTURES / 'decision_offered.json').read_text('utf-8'))
    decision.update(score=scoring.score.model_dump(mode='json'), challenger=scoring.challenger.model_dump(mode='json'),
                    model_version=scoring.score.model_version)
    Decision.model_validate(decision)


def test_v0_file_is_a_valid_scorecard():
    Scorecard.model_validate_json((ROOT / DEFAULT_CHALLENGER).read_text('utf-8'))
