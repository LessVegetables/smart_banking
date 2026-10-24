"""Scoring adapter (T15, SPEC §8): features → PD, points, grade, contributions, model version.

One interface, `ScoringModel.predict(features) -> ScoreResult`. The scorecard implementation reads
bins exactly as training does (`apps.ml.find_bin`), so live points match `apps/ml/`. The main model
decides; an optional challenger runs in shadow mode: its result is logged and returned for the
decision record, and it never changes the main result. See SCORING.md.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Mapping, NamedTuple, Protocol

from apps.api.contracts import ChallengerResult, Features, Scorecard, ScoreContribution, ScoreResult
from apps.ml import find_bin

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHAMPION = 'apps/ml/scorecard_v1.json'
# SIMPLIFICATION: the challenger is our own hand-set card; in a bank it would be another model version.
DEFAULT_CHALLENGER = 'apps/api/scorecards/scorecard_v0.json'
NO_CHALLENGER = 'none'


class FeatureSetMismatch(ValueError):
    """The features were computed by a different feature set than the model was built on."""


class ScoringModel(Protocol):
    """What the engine needs from any model; a bank's model plugs in by implementing this."""

    version: str
    feature_set_version: str

    def predict(self, features: Features) -> ScoreResult: ...


@dataclass(frozen=True)
class ScorecardModel:
    scorecard: Scorecard
    feature_set_version: str

    @property
    def version(self) -> str:
        return self.scorecard.version

    def predict(self, features: Features) -> ScoreResult:
        if features.feature_set_version != self.feature_set_version:
            raise FeatureSetMismatch(f'{self.version} needs features {self.feature_set_version}, '
                                     f'got {features.feature_set_version}')
        contributions = []
        for feature in self.scorecard.features:
            value = getattr(features, feature.name)
            bin_ = find_bin(feature, value)
            contributions.append(ScoreContribution(feature=feature.name, label=feature.label,
                                                   value=value.value if isinstance(value, Enum) else value,
                                                   bin_label=bin_.label, points=bin_.points))
        # Bin points are in tenths (apps/ml/README.md); rounding drops float noise from the sum.
        points = round(self.scorecard.intercept_points + sum(c.points for c in contributions), 1)
        pd = self.scorecard.pd_from_points(points)
        return ScoreResult(pd=pd, points=points, grade=self.scorecard.grade_for_pd(pd),
                           contributions=contributions, model_version=self.version)


def load_scorecard_model(path: str | Path, feature_set_version: str | None = None) -> ScorecardModel:
    """Load `scorecard_vN.json`; the feature-set version comes from its passport `scorecard_vN.training.json`.

    The `Scorecard` contract has no field for it (see the T10 decision in TASKS.md), so a card
    without a passport needs `feature_set_version` passed explicitly.
    """
    path = _resolve(path)
    scorecard = Scorecard.model_validate_json(path.read_text(encoding='utf-8'))
    if feature_set_version is None:
        passport = path.with_name(f'{path.stem}.training.json')
        if not passport.exists():
            raise FileNotFoundError(f'{passport.name} not found: cannot tell which feature set {scorecard.version} needs')
        meta = json.loads(passport.read_text(encoding='utf-8'))
        if meta.get('scorecard_version') != scorecard.version:
            raise ValueError(f'{passport.name} describes {meta.get("scorecard_version")}, not {scorecard.version}')
        feature_set_version = meta['feature_set_version']
    return ScorecardModel(scorecard, feature_set_version)


def _resolve(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


class Scoring(NamedTuple):
    score: ScoreResult
    challenger: ChallengerResult | None


@dataclass(frozen=True)
class Scorer:
    champion: ScoringModel
    challenger: ScoringModel | None = None

    def score(self, features: Features, application_id: str | None = None) -> Scoring:
        """Score with the main model; FeatureSetMismatch from it propagates (refuse to score).

        Challenger failures of any kind are logged and give `challenger=None`.
        """
        score = self.champion.predict(features)
        challenger = None
        if self.challenger is not None:
            try:
                shadow = self.challenger.predict(features)
                challenger = ChallengerResult(pd=shadow.pd, points=shadow.points, grade=shadow.grade,
                                              model_version=shadow.model_version)
            except Exception:
                log.warning('challenger %s failed for application %s', self.challenger.version, application_id,
                            exc_info=True)
        log.info('scored application %s: %s %.1f pts pd=%.4f grade=%s; challenger %s', application_id,
                 score.model_version, score.points, score.pd, score.grade.value,
                 f'{challenger.model_version} {challenger.points:.1f} pts pd={challenger.pd:.4f} '
                 f'grade={challenger.grade.value}' if challenger else '-')
        return Scoring(score, challenger)


def scorer_from_env(env: Mapping[str, str] | None = None) -> Scorer:
    """`SCORECARD_CHAMPION` / `SCORECARD_CHALLENGER`: card paths, relative to the repo root.

    Empty uses the default; `SCORECARD_CHALLENGER=none` turns shadow mode off. A challenger that
    fails to load is logged and skipped: shadow mode never blocks the main model.
    """
    env = os.environ if env is None else env
    champion = load_scorecard_model(env.get('SCORECARD_CHAMPION') or DEFAULT_CHAMPION)
    path = env.get('SCORECARD_CHALLENGER') or DEFAULT_CHALLENGER
    challenger = None
    if path.lower() != NO_CHALLENGER:
        try:
            challenger = load_scorecard_model(path)
        except Exception:
            log.warning('challenger %s not loaded; shadow mode off', path, exc_info=True)
    return Scorer(champion, challenger)


@lru_cache(maxsize=1)
def default_scorer() -> Scorer:
    return scorer_from_env()


def score(features: Features, application_id: str | None = None) -> Scoring:
    return default_scorer().score(features, application_id)
