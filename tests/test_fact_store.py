from datetime import datetime

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from apps.api import db
from apps.api.contracts import Fact, SourceId, TrustLevel
from apps.api.fact_store import load_facts, save_facts


def test_fact_store_round_trip_and_replace(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    legacy_tables = [table for table in db.Base.metadata.sorted_tables if table.name != 'application_facts']
    db.Base.metadata.create_all(engine, tables=legacy_tables)
    with Session(engine) as session, session.begin():
        session.add(db.User(id='preserved-user', name='Existing user'))

    monkeypatch.setattr(db, 'engine', engine)
    db.init_db()
    db.init_db()

    assert inspect(engine).has_table('application_facts')
    assert inspect(engine).has_table('alembic_version')
    with Session(engine) as session:
        assert session.get(db.User, 'preserved-user').name == 'Existing user'

        facts = [
            Fact(name='annual_revenue', declared_value=1200, verified_value=1250, source=SourceId.FNS,
                 trust_level=TrustLevel.REGISTRY, fetched_at=datetime.fromisoformat('2026-10-24T10:15:00+03:00')),
            Fact(name='loan_purpose', declared_value='inventory', verified_value=None, source=None,
                 trust_level=TrustLevel.DECLARED, fetched_at=None),
        ]
        save_facts(session, 'application-1', facts)
        first = load_facts(session, 'application-1')
        assert first == sorted(facts, key=lambda fact: (fact.name, fact.source.value if fact.source else 'declared'))

        save_facts(session, 'application-1', [facts[0]])
        assert load_facts(session, 'application-1') == [facts[0]]
        assert load_facts(session, 'application-2') == []

        stored = session.scalars(select(db.ApplicationFact)).all()
        assert len(stored) == 1
        assert stored[0].fetched_at == '2026-10-24T10:15:00+03:00'

    engine.dispose()


def test_fresh_database_uses_baseline_migration(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(db, 'engine', engine)

    db.init_db()

    tables = set(inspect(engine).get_table_names())
    assert {'users', 'applications', 'application_facts', 'alembic_version'} <= tables
    with engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == '0002_fact_store'
    engine.dispose()
