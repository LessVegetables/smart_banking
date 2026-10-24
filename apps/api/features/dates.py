"""Date arithmetic shared by the features and the facts (apps/sources/facts.py)."""
from __future__ import annotations

import calendar
from datetime import date


def months_between(start: date, end: date) -> int:
    """Whole months from start to end."""
    return (end.year - start.year) * 12 + end.month - start.month - (end.day < start.day)


def months_before(day: date, months: int) -> date:
    """The same day `months` months earlier, or the last day of that month if it is shorter."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def changed_recently(since: date | None, registered_at: date, today: date, window_months: int) -> bool:
    """A person who came after registration, within the window. Founding people are no change."""
    return since is not None and since > registered_at and months_between(since, today) < window_months
