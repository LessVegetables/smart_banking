"""Scorecard training (T10, SPEC §8) and validation (T11): WoE binning, logistic regression, points; see README.md.

The package itself exports only how a scorecard reads features (`score.py`), which needs
nothing but the contracts: the scoring adapter can import it at runtime. Training lives
in `apps.ml.train` and needs the synthetic world (`apps.world`). Validation (T11) is split the
same way: metrics and the report format in `apps.ml.validation`, the run on the world in
`apps.ml.validate`.
"""
from .score import find_bin, gini, total_points

__all__ = ['find_bin', 'gini', 'total_points']
