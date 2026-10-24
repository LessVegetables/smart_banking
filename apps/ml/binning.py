"""WoE binning (SPEC §8): fine classes → coarse bins with monotonic weight of evidence.

WoE of a bin = ln(share of all goods in the bin / share of all bads in the bin). Positive
WoE: the bin is safer than average. Every feature gets a separate "no data" bin
(SPEC §5.1): with enough companies in it, it gets its own WoE; otherwise WoE 0, the
average risk, because there is nothing to learn from.

Numeric bins are [lower, upper); the first and last are open, so they cover every
value the live engine can see. Cut points are values seen in the training data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Hashable


@dataclass(frozen=True)
class BinningSettings:
    fine_classes: int = 20  # quantile classes before merging
    min_bin_share: float = 0.05  # of the companies with a value
    min_bin_bads: int = 10
    max_bins: int = 5
    smoothing: float = 0.5  # added to goods and bads, so an all-good bin has a finite WoE


@dataclass
class Bin:
    goods: int
    bads: int
    lower: float | None = None  # numeric
    upper: float | None = None
    categories: list[Hashable] = field(default_factory=list)  # categorical
    missing: bool = False
    woe: float | None = None

    @property
    def total(self) -> int:
        return self.goods + self.bads

    def bad_rate(self) -> float:
        return self.bads / self.total if self.total else 0.0


@dataclass
class Binning:
    name: str
    kind: str  # 'numeric' | 'categorical'
    bins: list[Bin]  # value bins in order, then the missing bin
    iv: float

    def find(self, value) -> Bin:
        if value is None:
            return next(b for b in self.bins if b.missing)
        for b in self.bins:
            if b.missing:
                continue
            if self.kind == 'categorical':
                if value in b.categories:
                    return b
            elif (b.lower is None or value >= b.lower) and (b.upper is None or value < b.upper):
                return b
        raise ValueError(f'{self.name}: no bin for {value!r}')

    def woe(self, value) -> float:
        return self.find(value).woe or 0.0


def _woe(b: Bin, goods: int, bads: int, s: BinningSettings) -> float:
    return math.log((b.goods + s.smoothing) / (goods + s.smoothing)) - math.log((b.bads + s.smoothing) / (bads + s.smoothing))


def _merge(a: Bin, b: Bin) -> Bin:
    return Bin(goods=a.goods + b.goods, bads=a.bads + b.bads, lower=a.lower, upper=b.upper,
               categories=a.categories + b.categories)


def _merge_at(bins: list[Bin], i: int) -> None:
    """Merge bins i and i+1 in place."""
    bins[i:i + 2] = [_merge(bins[i], bins[i + 1])]


def _closest_neighbour(bins: list[Bin], i: int) -> int:
    """Index of the left one of the pair (i, neighbour) whose bad rates are closest."""
    if i == 0:
        return 0
    if i == len(bins) - 1:
        return i - 1
    left = abs(bins[i - 1].bad_rate() - bins[i].bad_rate())
    right = abs(bins[i + 1].bad_rate() - bins[i].bad_rate())
    return i - 1 if left <= right else i


def _enforce_size(bins: list[Bin], s: BinningSettings, total: int) -> None:
    while len(bins) > 1:
        small = [i for i, b in enumerate(bins) if b.total < s.min_bin_share * total or b.bads < s.min_bin_bads]
        if not small:
            return
        i = min(small, key=lambda j: (bins[j].total, j))
        _merge_at(bins, _closest_neighbour(bins, i))


def _enforce_monotonic(bins: list[Bin]) -> None:
    """Pool adjacent violators: bad rates move one way along the value axis. The direction
    is the one of the two that keeps more bins."""
    def pooled(direction: int) -> list[Bin]:
        out: list[Bin] = []
        for b in bins:
            out.append(b)
            while len(out) > 1 and direction * (out[-1].bad_rate() - out[-2].bad_rate()) < 0:
                out[-2:] = [_merge(out[-2], out[-1])]
        return out

    up, down = pooled(1), pooled(-1)
    bins[:] = up if len(up) >= len(down) else down


def _enforce_max_bins(bins: list[Bin], s: BinningSettings) -> None:
    while len(bins) > s.max_bins:
        diffs = [abs(bins[i].bad_rate() - bins[i + 1].bad_rate()) for i in range(len(bins) - 1)]
        _merge_at(bins, diffs.index(min(diffs)))


def _fine_numeric(pairs: list[tuple[float, bool]], s: BinningSettings) -> list[Bin]:
    values = sorted(v for v, _ in pairs)
    cuts = sorted({values[len(values) * k // s.fine_classes] for k in range(1, s.fine_classes)} - {values[0]})
    edges = [None, *cuts, None]
    bins = [Bin(goods=0, bads=0, lower=lo, upper=hi) for lo, hi in zip(edges, edges[1:])]
    for v, bad in pairs:
        b = next(b for b in bins if b.upper is None or v < b.upper)
        if bad:
            b.bads += 1
        else:
            b.goods += 1
    return bins


def _finish(name: str, kind: str, bins: list[Bin], missing: Bin, goods: int, bads: int,
            s: BinningSettings) -> Binning:
    if kind == 'numeric':
        bins[0].lower, bins[-1].upper = None, None
    for b in bins:
        b.woe = _woe(b, goods, bads, s)
    enough = missing.total >= s.min_bin_share * (goods + bads) and missing.bads >= s.min_bin_bads
    missing.woe = _woe(missing, goods, bads, s) if enough else None
    iv = sum((b.goods / goods - b.bads / bads) * b.woe for b in [*bins, missing] if b.woe is not None)
    return Binning(name=name, kind=kind, bins=[*bins, missing], iv=iv)


def bin_feature(name: str, values: list, bads: list[bool], *, categories: list[Hashable] | None = None,
                settings: BinningSettings | None = None) -> Binning:
    """Bins one feature. `categories` (the full domain) makes it categorical: every value the
    engine can see gets a bin, including ones absent from the training data."""
    s = settings or BinningSettings()
    goods_total = sum(not b for b in bads)
    bads_total = sum(bads)
    missing = Bin(goods=sum(v is None and not b for v, b in zip(values, bads)),
                  bads=sum(v is None and b for v, b in zip(values, bads)), missing=True)
    pairs = [(v, b) for v, b in zip(values, bads) if v is not None]
    if not pairs:
        bins = [Bin(goods=0, bads=0, categories=list(categories))] if categories else [Bin(goods=0, bads=0)]
        return _finish(name, 'categorical' if categories else 'numeric', bins, missing, goods_total, bads_total, s)

    if categories is not None:
        bins = [Bin(goods=sum(v == c and not b for v, b in pairs), bads=sum(v == c and b for v, b in pairs),
                    categories=[c]) for c in categories]
        bins.sort(key=lambda b: b.bad_rate())  # neighbours by risk, so merging joins similar categories
        _enforce_size(bins, s, len(pairs))
        _enforce_max_bins(bins, s)
        return _finish(name, 'categorical', bins, missing, goods_total, bads_total, s)

    bins = _fine_numeric(pairs, s)  # every class holds at least its lower cut point
    _enforce_size(bins, s, len(pairs))
    _enforce_monotonic(bins)
    _enforce_max_bins(bins, s)
    return _finish(name, 'numeric', bins, missing, goods_total, bads_total, s)
