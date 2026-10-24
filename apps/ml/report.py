"""Shared parts of the files written next to the scorecard (training passport, validation)."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class _Report(BaseModel):
    model_config = ConfigDict(extra='forbid')


class WorldInfo(_Report):
    generator_version: str
    seed: int
    n: int
    as_of: date
    outcomes_version: str
