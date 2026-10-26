"""Persist demo source overrides; each process reads them before a source call."""
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import update
from .db import MockSourceConfig, Session, now
from apps.sources.service import SOURCES


class SourceOverride(BaseModel):
    model_config=ConfigDict(extra='forbid')
    outcome:Literal['default','ok','error','timeout']='default'
    latency_ms:int|None=Field(default=None,ge=0,le=30000,strict=True)


class Configuration(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1,strict=True)
    sources:dict[str,SourceOverride]

    @model_validator(mode='after')
    def all_sources(self):
        if set(self.sources)!={source.value for source in SOURCES}:
            raise ValueError('Нужны настройки всех восьми источников')
        return self


def read(db):
    row=db.get(MockSourceConfig,1)
    return {'revision':row.revision,'sources':{s.value:row.settings.get(s.value,SourceOverride().model_dump()) for s in SOURCES},
            'updated_at':row.updated_at}


def save(db,body:Configuration,user_id:str):
    changed=db.execute(update(MockSourceConfig).where(MockSourceConfig.id==1,MockSourceConfig.revision==body.revision)
        .values(revision=MockSourceConfig.revision+1,settings={s:v.model_dump() for s,v in body.sources.items()},
                updated_at=now(),updated_by=user_id)).rowcount
    if not changed:
        raise HTTPException(409,'Настройки изменены в другом окне. Обновите страницу и повторите изменения.')
    db.expire_all()
    return read(db)


def source_overrides():
    with Session() as db:
        row=db.get(MockSourceConfig,1)
        return row.settings if row else {}
