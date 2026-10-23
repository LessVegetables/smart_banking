import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, String, Integer, JSON, Float, ForeignKey
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
class Login(Base):
    __tablename__='sessions'
    token:Mapped[str]=mapped_column(String,primary_key=True)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'))
    expires:Mapped[float]=mapped_column(Float)
class Survey(Base):
    __tablename__='surveys'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    version:Mapped[int]=mapped_column(Integer,default=1)
    answers:Mapped[dict]=mapped_column(JSON,default=dict)
    history:Mapped[list]=mapped_column(JSON,default=list)
    created:Mapped[str]=mapped_column(String,default=now)
class Application(Base):
    __tablename__='applications'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    user_id:Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    submission_key:Mapped[str]=mapped_column(String,unique=True)
    survey_id:Mapped[str]=mapped_column(String)
    revision:Mapped[int]=mapped_column(Integer,default=1)
    status:Mapped[str]=mapped_column(String,default='submitted')
    answers:Mapped[dict]=mapped_column(JSON)
    policy_snapshot:Mapped[dict]=mapped_column(JSON)
    decisions:Mapped[list]=mapped_column(JSON,default=list)
    events:Mapped[list]=mapped_column(JSON,default=list)
    selected_offer:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    offer_expires:Mapped[float|None]=mapped_column(Float,nullable=True)
    documents_reviewed:Mapped[int]=mapped_column(Integer,default=0)
    created:Mapped[str]=mapped_column(String,default=now)
    __mapper_args__={'version_id_col':revision,'version_id_generator':False}
class SubmissionKey(Base):
    __tablename__='submission_keys'
    key:Mapped[str]=mapped_column(String,primary_key=True)
    application_id:Mapped[str]=mapped_column(ForeignKey('applications.id'))
class Document(Base):
    __tablename__='documents'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    application_id:Mapped[str]=mapped_column(ForeignKey('applications.id'),index=True)
    name:Mapped[str]=mapped_column(String)
    media_type:Mapped[str]=mapped_column(String)
    size:Mapped[int]=mapped_column(Integer)
    status:Mapped[str]=mapped_column(String,default='uploaded')
    created:Mapped[str]=mapped_column(String,default=now)
class Job(Base):
    __tablename__='jobs'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    application_id:Mapped[str]=mapped_column(ForeignKey('applications.id'),index=True)
    state:Mapped[str]=mapped_column(String,default='pending')
    attempts:Mapped[int]=mapped_column(Integer,default=0)
    error:Mapped[str|None]=mapped_column(String,nullable=True)
    available:Mapped[float]=mapped_column(Float,default=0)

def init_db():Base.metadata.create_all(engine)
