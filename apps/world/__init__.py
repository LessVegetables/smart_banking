"""Synthetic world (SPEC §14): the seeded companies behind the mock sources and the ML."""
from .generator import Company, World, company_rng, generate_world

__all__ = ['Company', 'World', 'company_rng', 'generate_world']
