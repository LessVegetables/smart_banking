"""Persist demo source overrides; each process reads them before a source call.

Also hands organizers a random company of the synthetic world to try in the cabinet."""
import random
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import update
from .db import MockSourceConfig, Session, now
from apps.sources.service import SOURCES
from apps.world import World
from apps.world.industries import BY_KEY


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


def random_company(world:World,demo_titles:dict[str,str]):
    """Only what the registries and the tax office show; the hidden quality, archetype and outcome stay hidden."""
    company=random.choice(world.companies)
    egrul,fns=company.egrul,company.fns
    report=fns.financials or fns.tax_return
    industry=BY_KEY.get(company.industry)
    return {'inn':company.inn,'ogrn':egrul.ogrn,'legal_form':egrul.legal_form,'name':egrul.name,'status':egrul.status,
            'registered_at':egrul.registered_at,'okved_main':egrul.okved_main,'industry_key':company.industry,'industry':industry.name if industry else company.industry,
            'region':egrul.region,'address':egrul.address,'director':egrul.director.full_name,'size':company.size,
            'employees':fns.employees,'revenue':(getattr(report,'revenue',None) or getattr(report,'income',None)) if report else None,
            'revenue_year':report.year if report else None,'demo_persona':demo_titles.get(company.inn)}
