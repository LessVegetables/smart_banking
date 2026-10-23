"""Synthetic world (SPEC §14): the seeded companies behind the mock sources and the ML."""
from .generator import Company, World, company_rng, generate_world
from .outcomes import train_holdout

__all__ = ['Company', 'World', 'company_rng', 'generate_world', 'train_holdout']
