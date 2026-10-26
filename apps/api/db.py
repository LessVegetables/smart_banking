import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, Boolean, String, Integer, JSON, Float, ForeignKey, UniqueConstraint, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATA=Path(os.environ.get('DATA_DIR','data')).resolve(); DATA.mkdir(parents=True,exist_ok=True)
URL=os.environ.get('DATABASE_URL',f'sqlite:///{(DATA / "alfa.db").as_posix()}')
engine=create_engine(URL,connect_args={'check_same_thread':False,'timeout':30} if URL.startswith('sqlite') else {},pool_pre_ping=True)
Session=sessionmaker(engine,expire_on_commit=False)
def uid():return uuid.uuid4().hex
def now():return datetime.now(timezone.utc).isoformat()

class Base(DeclarativeBase):pass
class User(Base):
    __tablename__='users'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    name:Mapped[str]=mapped_column(String,default='Предприниматель')
    role:Mapped[str]=mapped_column(String,default='customer')
    identity:Mapped[str|None]=mapped_column(String,unique=True,nullable=True)
    staff_position:Mapped[str|None]=mapped_column(String,nullable=True)  # staff only: underwriter | risk_manager (T20)
class Login(Base):
    __tablename__='sessions'
    token:Mapped[str]=mapped_column(String,primary_key=True)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    expires:Mapped[float]=mapped_column(Float)
class LoanApplication(Base):
    """A v2 application (T19): the client's `ApplicationRequest`, its status (SPEC §13) and the engine's results."""
    __tablename__='loan_applications'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    idempotency_key:Mapped[str]=mapped_column(String,unique=True)
    status:Mapped[str]=mapped_column(String,index=True)
    revision:Mapped[int]=mapped_column(Integer,default=1)
    request:Mapped[dict]=mapped_column(JSON)  # ApplicationRequest
    enrichment:Mapped[dict|None]=mapped_column(JSON,nullable=True)  # Enrichment without facts (they're in the fact store)
    data_quality:Mapped[dict|None]=mapped_column(JSON,nullable=True)  # DataQuality
    decision:Mapped[dict|None]=mapped_column(JSON,nullable=True)  # the latest Decision
    offer_valid_until:Mapped[str|None]=mapped_column(String,nullable=True)  # ISO date; offered → expired after it
    selected_offer_id:Mapped[str|None]=mapped_column(String,nullable=True)
    pending_review:Mapped[dict|None]=mapped_column(JSON,nullable=True)  # committee proposal waiting for its second signature (T20)
    closing:Mapped[dict|None]=mapped_column(JSON,nullable=True)  # documents, signature, conditions, payout (T21, disbursement.py)
    statements:Mapped[list|None]=mapped_column(JSON,nullable=True)  # UploadedStatement list (T23); [] = the client went on without one
    attempts:Mapped[int]=mapped_column(Integer,default=0)  # automatic runs started
    lease_until:Mapped[float|None]=mapped_column(Float,nullable=True)  # worker lease, or "not before" for a retry
    created:Mapped[str]=mapped_column(String,default=now)
    updated:Mapped[str]=mapped_column(String,default=now)
    __mapper_args__={'version_id_col':revision}

class ApplicationEvent(Base):
    """Append-only audit log of a v2 application: status changes, source calls, decisions, actions."""
    __tablename__='application_events'
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    application_id:Mapped[str]=mapped_column(ForeignKey('loan_applications.id',ondelete='CASCADE'),index=True)
    at:Mapped[str]=mapped_column(String,default=now)
    actor:Mapped[str]=mapped_column(String)  # 'system', or 'client:<user id>' / 'staff:<user id>'
    kind:Mapped[str]=mapped_column(String)  # status | source | decision | action | error | review | proposal | signature
    from_status:Mapped[str|None]=mapped_column(String,nullable=True)
    to_status:Mapped[str|None]=mapped_column(String,nullable=True)
    message:Mapped[str]=mapped_column(String)
    data:Mapped[dict|None]=mapped_column(JSON,nullable=True)

class ApplicationFact(Base):
    __tablename__='application_facts'
    __table_args__=(UniqueConstraint('application_id','name','source_key',name='uq_application_fact_source'),)
    id:Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    application_id:Mapped[str]=mapped_column(ForeignKey('loan_applications.id',ondelete='CASCADE'),index=True)
    name:Mapped[str]=mapped_column(String,index=True)
    source_key:Mapped[str]=mapped_column(String)
    declared_value:Mapped[Any|None]=mapped_column(JSON,nullable=True)
    verified_value:Mapped[Any|None]=mapped_column(JSON,nullable=True)
    source:Mapped[str|None]=mapped_column(String,nullable=True)
    trust_level:Mapped[str]=mapped_column(String)
    fetched_at:Mapped[str|None]=mapped_column(String,nullable=True)
    discrepancy:Mapped[float|None]=mapped_column(Float,nullable=True)
class PreApprovalRow(Base):
    """The latest overdraft pre-approval per client (T22), replaced by each batch; `result` is `preapproval.PreApproval`."""
    __tablename__='preapprovals'
    inn:Mapped[str]=mapped_column(String,primary_key=True)
    approved:Mapped[bool]=mapped_column(Boolean)
    amount:Mapped[int|None]=mapped_column(Integer,nullable=True)
    computed_at:Mapped[str]=mapped_column(String,index=True)
    valid_until:Mapped[str|None]=mapped_column(String,nullable=True)  # ISO date
    result:Mapped[dict]=mapped_column(JSON)
class MockSourceConfig(Base):
    __tablename__='mock_source_config'
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    revision:Mapped[int]=mapped_column(Integer,nullable=False)
    settings:Mapped[dict]=mapped_column(JSON,nullable=False)
    updated_at:Mapped[str|None]=mapped_column(String,nullable=True)
    updated_by:Mapped[str|None]=mapped_column(String,nullable=True)


def init_db():
    """Adopt the pre-Alembic schema when present, then apply versioned migrations."""
    config=Config(str(Path(__file__).resolve().parents[2]/'alembic.ini'))
    config.set_main_option('sqlalchemy.url',URL.replace('%','%%'))
    with engine.begin() as connection:
        config.attributes['connection']=connection
        if not inspect(connection).has_table('alembic_version'):
            tables=set(inspect(connection).get_table_names())
            legacy_tables={'users','sessions','surveys','applications','submission_keys','documents','jobs'}
            existing_legacy=tables & legacy_tables
            if existing_legacy:
                if existing_legacy != legacy_tables:
                    missing=', '.join(sorted(legacy_tables-existing_legacy))
                    raise RuntimeError(f'Cannot adopt partial legacy schema; missing tables: {missing}')
                # Existing installs predate Alembic. Mark their intact schema without
                # recreating tables or touching their data.
                command.stamp(config,'0001_baseline')
            else:
                # A new install builds its initial schema from the immutable baseline.
                command.upgrade(config,'0001_baseline')
        command.upgrade(config,'head')
    configure_logging()

def configure_logging():
    """Alembic's fileConfig in init_db sets the root logger to WARN; the app's level comes from LOG_LEVEL."""
    root=logging.getLogger()
    if not root.handlers:logging.basicConfig(format='%(levelname)-5.5s [%(name)s] %(message)s')
    root.setLevel(os.environ.get('LOG_LEVEL','INFO').strip().upper() or 'INFO')
