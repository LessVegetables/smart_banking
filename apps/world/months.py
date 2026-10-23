"""Month arithmetic for the synthetic world. A month is an integer index: year * 12 + month - 1."""
from __future__ import annotations

from datetime import date


def month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def month_start(index: int) -> date:
    return date(index // 12, index % 12 + 1, 1)


def month_name(index: int) -> str:
    """'YYYY-MM', the contract format."""
    return f'{index // 12:04d}-{index % 12 + 1:02d}'


def calendar_month(index: int) -> int:
    """1..12"""
    return index % 12 + 1


def last_full_month(as_of: date) -> int:
    """The month that contains as_of is never complete yet."""
    return month_index(as_of) - 1


def first_full_month(opened_at: date) -> int:
    """The first month the account was open for every day of."""
    return month_index(opened_at) + (0 if opened_at.day == 1 else 1)
