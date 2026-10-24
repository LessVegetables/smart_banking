"""Applying a scorecard file to features, and the Gini coefficient.

The reference for how a `Scorecard` reads `Features`; the scoring adapter (T15) must give
the same points.
"""
from __future__ import annotations

from apps.api.contracts import Features, Scorecard, ScorecardBin, ScorecardFeature


def find_bin(feature: ScorecardFeature, value) -> ScorecardBin:
    for b in feature.bins:
        if b.missing:
            if value is None:
                return b
        elif value is None:
            continue
        elif feature.kind == 'categorical':
            if value in b.categories:
                return b
        elif (b.lower is None or value >= b.lower) and (b.upper is None or value < b.upper):
            return b
    raise ValueError(f'{feature.name}: no bin for {value!r}')


def total_points(scorecard: Scorecard, features: Features) -> float:
    return scorecard.intercept_points + sum(find_bin(f, getattr(features, f.name)).points for f in scorecard.features)


def gini(scores: list[float], bads: list[bool]) -> float:
    """2·AUC − 1, where a higher score means a safer company; ties count half."""
    ranked = sorted(zip(scores, bads), key=lambda p: p[0])
    goods_total = sum(not b for b in bads)
    bads_total = len(bads) - goods_total
    concordant = 0.0  # pairs where the good company scores higher than the bad one
    goods_below = 0
    i = 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        goods_tied = sum(not b for _, b in ranked[i:j])
        bads_tied = j - i - goods_tied
        concordant += bads_tied * (goods_total - goods_below - goods_tied) + 0.5 * bads_tied * goods_tied
        goods_below += goods_tied
        i = j
    return 2 * concordant / (goods_total * bads_total) - 1
