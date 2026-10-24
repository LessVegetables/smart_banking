"""Logistic regression by Newton's method, in plain Python.

A scorecard has a few thousand rows and a dozen or two columns, so this is fast enough
and keeps numpy and scikit-learn out of the image. The model is
ln(odds_good) = b0 + sum(b_i * x_i), with y = 1 for a good company.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Fit:
    intercept: float
    coefficients: list[float]
    std_errors: list[float]  # of the coefficients, from the inverse Hessian
    iterations: int

    def z(self, i: int) -> float:
        return self.coefficients[i] / self.std_errors[i] if self.std_errors[i] > 0 else 0.0


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def _inverse(a: list[list[float]]) -> list[list[float]]:
    """Gauss–Jordan with partial pivoting. The Hessian is symmetric positive definite
    (the ridge term makes sure), so pivots don't vanish."""
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        p = m[col][col]
        m[col] = [x / p for x in m[col]]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [row[n:] for row in m]


def fit_logistic(x: list[list[float]], y: list[int], *, ridge: float = 1e-3, tol: float = 1e-10,
                 max_iter: int = 100) -> Fit:
    """`ridge` is an L2 penalty on the coefficients, not the intercept: it keeps the
    Hessian invertible when two features carry the same information."""
    k = len(x[0]) + 1
    rows = [[1.0, *r] for r in x]
    beta = [0.0] * k
    for iteration in range(1, max_iter + 1):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for r, target in zip(rows, y):
            p = _sigmoid(sum(b * v for b, v in zip(beta, r)))
            w = p * (1 - p)
            for i in range(k):
                grad[i] += (target - p) * r[i]
                wi = w * r[i]
                for j in range(i, k):
                    hess[i][j] += wi * r[j]
        for i in range(k):
            for j in range(i):
                hess[i][j] = hess[j][i]
            if i:
                grad[i] -= ridge * beta[i]
                hess[i][i] += ridge
        inv = _inverse(hess)
        step = [sum(inv[i][j] * grad[j] for j in range(k)) for i in range(k)]
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < tol:
            break
    se = [math.sqrt(max(inv[i][i], 0.0)) for i in range(1, k)]
    return Fit(intercept=beta[0], coefficients=beta[1:], std_errors=se, iterations=iteration)
