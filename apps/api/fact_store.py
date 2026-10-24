"""Persistence for contract facts, scoped to one application."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from .contracts import Fact
from .db import ApplicationFact, uid


def save_facts(db: OrmSession, application_id: str, facts: list[Fact]) -> None:
    """Replace an application's current fact snapshot within the caller's transaction."""
    db.execute(delete(ApplicationFact).where(ApplicationFact.application_id == application_id))
    db.add_all([
        ApplicationFact(
            id=uid(),
            application_id=application_id,
            name=fact.name,
            source_key=fact.source.value if fact.source else 'declared',
            declared_value=fact.declared_value,
            verified_value=fact.verified_value,
            source=fact.source.value if fact.source else None,
            trust_level=fact.trust_level.value,
            fetched_at=fact.fetched_at.isoformat() if fact.fetched_at else None,
            discrepancy=fact.discrepancy,
        )
        for fact in facts
    ])
    db.flush()


def load_facts(db: OrmSession, application_id: str) -> list[Fact]:
    rows = db.scalars(
        select(ApplicationFact)
        .where(ApplicationFact.application_id == application_id)
        .order_by(ApplicationFact.name, ApplicationFact.source_key)
    )
    return [
        Fact.model_validate({
            'name': row.name,
            'declared_value': row.declared_value,
            'verified_value': row.verified_value,
            'source': row.source,
            'trust_level': row.trust_level,
            'fetched_at': row.fetched_at,
            'discrepancy': row.discrepancy,
        })
        for row in rows
    ]
