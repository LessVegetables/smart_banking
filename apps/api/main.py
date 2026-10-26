import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, Form, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.exc import IntegrityError

from .db import Session, User, Login, LoanApplication, init_db
from .worker import loop
from .credit_policy import default_policy
from .assistant import explain
from .contracts import CollateralType, EgrulPayload, ProductId, SourceId
from . import disbursement, preapproval, staff_actions, statements, workflow
from apps.sources import SourceNotFound, SourceTimeout, SourceUnavailable
from apps.ml.validation import ValidationReport

ROOT=Path(__file__).resolve().parents[2]

DEMO=os.environ.get('DEMO_MODE','true').lower()=='true'
COOKIE_SECURE=os.environ.get('COOKIE_SECURE','false').lower()=='true'

@asynccontextmanager
async def lifespan(app):
    init_db();stop=threading.Event();thread=None
    if os.environ.get('WORKER_ENABLED','true')=='true':
        thread=threading.Thread(target=loop,args=(stop,),daemon=True);thread.start()
    yield
    stop.set()
    if thread:thread.join(timeout=3)

app=FastAPI(title='Эта · Бизнес — Demo API',version='1.0.0',lifespan=lifespan)

@app.exception_handler(StaleDataError)
async def stale_error(request,exc):return JSONResponse(status_code=409,content={'detail':'Данные изменились в другом окне. Обновите страницу.'})

@app.exception_handler(IntegrityError)
async def duplicate_error(request,exc):return JSONResponse(status_code=409,content={'detail':'Операция уже обрабатывается. Обновите данные и повторите запрос с тем же ключом.'})

@app.middleware('http')
async def origin_guard(request,call_next):
    origin=request.headers.get('origin')
    allowed={os.environ.get('APP_ORIGIN','http://127.0.0.1:5173'),'http://localhost:5173','http://127.0.0.1:8000','http://localhost:8000'}
    allowed.add(str(request.base_url).rstrip('/'))
    if request.method not in ('GET','HEAD','OPTIONS') and origin and origin not in allowed:
        return Response('Недопустимый источник запроса',status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    if request.url.path.startswith('/api'):response.headers['Cache-Control']='no-store'
    return response

def db_session():
    with Session.begin() as db:yield db

def current_user(request:Request,db=Depends(db_session)):
    token=request.cookies.get('alfa_session','')
    login=db.get(Login,hashlib.sha256(token.encode()).hexdigest()) if token else None
    if not login or login.expires<time.time():raise HTTPException(401,'Войдите в кабинет')
    return db.get(User,login.user_id)

def staff(user=Depends(current_user)):
    if user.role!='staff':raise HTTPException(403,'Нужен доступ сотрудника')
    return user

def risk_manager(user=Depends(staff)):
    if user.staff_position!='risk_manager':raise HTTPException(403,'Нужен доступ риск-менеджера')
    return user

def login_response(db,user,response):
    token=secrets.token_urlsafe(40)
    db.add(Login(token=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,expires=time.time()+7*86400))
    response.set_cookie('alfa_session',token,httponly=True,samesite='lax',secure=COOKIE_SECURE,max_age=7*86400,path='/')
    return {'id':user.id,'name':user.name,'role':user.role}

class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class DemoLogin(Input):name:str=Field(default='Алексей',min_length=1,max_length=80)
class TelegramLogin(Input):init_data:str=Field(max_length=10000)
class StaffLogin(Input):password:str=Field(max_length=200);position:staff_actions.Position=staff_actions.Position.UNDERWRITER

@app.get('/api/health')
def health():return {'ok':True,'demo':DEMO,'policy_version':default_policy().version}

@app.post('/api/auth/demo')
def demo_login(body:DemoLogin,response:Response,db=Depends(db_session)):
    if not DEMO:raise HTTPException(404)
    # SIMPLIFICATION: a demo profile name is its stable local identity. This preserves its synthetic
    # companies and application history across logout/login; it is not real-user authentication.
    name=' '.join(body.name.split())
    if not name:raise HTTPException(422,'Введите имя демо-профиля')
    identity='demo:'+name.casefold()
    user=db.scalar(select(User).where(User.identity==identity))
    legacy=db.scalars(select(User).where(User.identity.is_(None),User.role=='customer',User.name==name)).all()
    if user is None and legacy:
        user=legacy.pop(0)
        user.identity=identity
        db.flush()
    elif user is None:
        user=User(name=name,identity=identity);db.add(user);db.flush()
    duplicate_ids=[old.id for old in legacy if old.id!=user.id]
    if duplicate_ids:
        # Merge prior anonymous demo sessions so their saved applications remain visible to this profile.
        for model in (Login,LoanApplication):
            db.execute(update(model).where(model.user_id.in_(duplicate_ids)).values(user_id=user.id))
    return login_response(db,user,response)

@app.post('/api/auth/telegram')
def telegram_login(body:TelegramLogin,response:Response,db=Depends(db_session)):
    token=os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:raise HTTPException(503,'Для входа через Telegram настройте TELEGRAM_BOT_TOKEN. В локальной версии доступен демо-вход.')
    fields=dict(parse_qsl(body.init_data,keep_blank_values=True));signature=fields.pop('hash','')
    check='\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    try:age=time.time()-int(fields['auth_date']);tg=json.loads(fields['user'])
    except (KeyError,ValueError):raise HTTPException(401,'Неверные данные Telegram')
    if not hmac.compare_digest(signature,expected) or not -30<=age<=300:raise HTTPException(401,'Данные Telegram недействительны или устарели')
    identity=f'telegram:{tg["id"]}'
    user=db.scalar(select(User).where(User.identity==identity))
    if not user:user=User(name=tg.get('first_name','Предприниматель'),identity=identity);db.add(user);db.flush()
    return login_response(db,user,response)

@app.post('/api/auth/staff')
def staff_login(body:StaffLogin,response:Response,db=Depends(db_session)):
    expected=os.environ.get('STAFF_PASSWORD','')
    if len(expected)<12 or not hmac.compare_digest(body.password,expected):raise HTTPException(403,'Неверный пароль сотрудника или доступ не настроен')
    # SIMPLIFICATION: one STAFF_PASSWORD for every position; the employee picks underwriter or risk manager at
    # login (T20). A bank takes the position from its staff directory.
    user=User(name=staff_actions.POSITION_TEXT[body.position].capitalize(),role='staff',staff_position=body.position.value);db.add(user);db.flush()
    return login_response(db,user,response)

@app.get('/api/auth/me')
def me(user=Depends(current_user)):return {'id':user.id,'name':user.name,'role':user.role,'staff_position':user.staff_position}

@app.post('/api/auth/logout')
def logout(request:Request,response:Response,db=Depends(db_session)):
    token=request.cookies.get('alfa_session','');row=db.get(Login,hashlib.sha256(token.encode()).hexdigest())
    if row:db.delete(row)
    response.delete_cookie('alfa_session');return {'ok':True}

class LinkToken(Input):token:str=Field(min_length=10,max_length=200)
LINKS={}
@app.post('/api/auth/link-code')
def link_code(user=Depends(current_user)):
    code=secrets.token_urlsafe(32);LINKS[hashlib.sha256(code.encode()).hexdigest()]=(user.id,time.time()+120)
    return {'token':code,'expires_in':120}

@app.post('/api/auth/link')
def link(body:LinkToken,user=Depends(current_user),db=Depends(db_session)):
    key=hashlib.sha256(body.token.encode()).hexdigest();target=LINKS.pop(key,None)
    if not target or target[1]<time.time():raise HTTPException(400,'Код недействителен или истёк')
    owner=db.get(User,target[0])
    if user.role!='customer' or not owner or owner.role!='customer' or not user.identity or not user.identity.startswith('telegram:') or owner.identity:
        raise HTTPException(409,'Привязку нужно подтвердить из нового Telegram-аккаунта к веб-кабинету без существующей привязки')
    identity=user.identity;user.identity=None;db.flush();owner.identity=identity
    for model in (LoanApplication,):db.execute(update(model).where(model.user_id==user.id).values(user_id=owner.id))
    db.execute(update(Login).where(Login.user_id==user.id).values(user_id=owner.id))
    return {'ok':True}

class V2Submission(Input):idempotency_key:str=Field(min_length=8,max_length=200);request:dict[str,Any]
class V2Revision(Input):revision:int
class V2Accept(V2Revision):offer_id:str=Field(min_length=1,max_length=200)

@app.get('/api/v2/companies/{inn}')
async def company_card_v2(inn:str,user=Depends(current_user)):
    """MOCK: look up the synthetic ЕГРЮЛ/ЕГРИП record for the client's prefilled card."""
    # SIMPLIFICATION: non-demo users are not yet bound to a company; demo profiles are limited to T12.
    if not re.fullmatch(r'\d{10}(?:\d{2})?',inn):
        raise HTTPException(422,'Введите ИНН компании из 10 или 12 цифр')
    if user.identity and user.identity.startswith('demo:') and DEMO:
        from .demo_companies import available_demo_inns
        if inn not in available_demo_inns(workflow.default_pipeline().client.world):
            raise HTTPException(403,'Выберите компанию из демо-списка.')
    try:
        record=await workflow.default_pipeline().client.fetch(SourceId.EGRUL,inn)
    except SourceNotFound as e:
        raise HTTPException(404,'Компания с таким ИНН не найдена') from e
    except (SourceTimeout,SourceUnavailable) as e:
        raise HTTPException(503,'Не удалось получить сведения из реестра. Повторите попытку позже.') from e
    if not isinstance(record,EgrulPayload):
        raise HTTPException(503,'Источник вернул неожиданный формат сведений')
    if record.inn!=inn:
        raise HTTPException(503,'Источник вернул сведения для другого ИНН')
    owner=record.director if record.legal_form.value=='ip' else (record.founders[0] if record.founders else None)
    if owner is None:
        raise HTTPException(503,'В сведениях реестра не найден собственник компании')
    return {'inn':record.inn,'ogrn':record.ogrn,'legal_form':record.legal_form,'name':record.name,
            'status':record.status,'registered_at':record.registered_at,'okved_main':record.okved_main,
            'region':record.region,'address':record.address,'owner_name':owner.full_name,'owner_inn':owner.inn}

@app.get('/api/v2/demo/companies')
def demo_companies_v2(user=Depends(current_user)):
    if not DEMO or not (user.identity or '').startswith('demo:'):
        return []
    from .demo_companies import company_options
    return company_options(workflow.default_pipeline().client.world)

def own_v2(db,id,user):
    a=db.get(LoanApplication,id)
    if not a or (user.role!='staff' and a.user_id!=user.id):raise HTTPException(404,'Заявка не найдена')
    return a

def client_v2(db,id,user,revision):
    a=own_v2(db,id,user)
    if a.user_id!=user.id:raise HTTPException(403,'Действие доступно только клиенту')
    if a.revision!=revision:raise HTTPException(409,'Заявка изменилась. Обновите её.')
    return a

@app.post('/api/v2/applications')
def submit_v2(body:V2Submission,user=Depends(current_user),db=Depends(db_session)):
    if user.identity and user.identity.startswith('demo:') and DEMO:
        from .demo_companies import available_demo_inns
        if body.request.get('inn') not in available_demo_inns(workflow.default_pipeline().client.world):
            raise HTTPException(422,'Выберите компанию из демо-списка.')
    try:a=workflow.submit(db,user.id,f'{user.id}:{body.idempotency_key}',body.request)
    except ValidationError as e:raise HTTPException(422,e.errors(include_url=False,include_context=False,include_input=False))
    except ValueError as e:raise HTTPException(422,str(e))
    return workflow.application_json(db,a,staff=False)

@app.get('/api/v2/applications')
def applications_v2(status:str|None=None,user=Depends(current_user),db=Depends(db_session)):
    q=select(LoanApplication).order_by(LoanApplication.created.desc())
    if user.role!='staff':q=q.where(LoanApplication.user_id==user.id)
    if status:q=q.where(LoanApplication.status==status)
    return [workflow.application_json(db,a,staff=user.role=='staff') for a in db.scalars(q)]

@app.get('/api/v2/applications/{id}')
def application_v2(id:str,user=Depends(current_user),db=Depends(db_session)):
    return workflow.application_json(db,own_v2(db,id,user),staff=user.role=='staff')

@app.get('/api/v2/applications/{id}/events')
def application_events_v2(id:str,user=Depends(staff),db=Depends(db_session)):
    return [workflow.event_json(e) for e in workflow.events(db,own_v2(db,id,user).id)]

@app.post('/api/v2/applications/{id}/accept')
def accept_v2(id:str,body:V2Accept,user=Depends(current_user),db=Depends(db_session)):
    a=client_v2(db,id,user,body.revision)
    try:disbursement.accept(db,a,body.offer_id,actor=workflow.actor_of(user))
    except workflow.IllegalTransition as e:raise HTTPException(409,str(e))
    except LookupError as e:raise HTTPException(422,str(e))
    db.flush()
    # A response, not an exception: the expiry must commit.
    if a.status=='expired':return JSONResponse(status_code=409,content={'detail':'Срок действия предложения истёк. Подайте новую заявку.'})
    return workflow.application_json(db,a,staff=False)

@app.post('/api/v2/applications/{id}/withdraw')
def withdraw_v2(id:str,body:V2Revision,user=Depends(current_user),db=Depends(db_session)):
    a=client_v2(db,id,user,body.revision)
    try:workflow.withdraw(db,a,actor=workflow.actor_of(user))
    except workflow.IllegalTransition as e:raise HTTPException(409,str(e))
    db.flush();return workflow.application_json(db,a,staff=False)

# --- v2 documents, signing and disbursement (T21, DISBURSEMENT.md); conditions run in the worker ---
class V2SignCode(V2Revision):code:str=Field(min_length=1,max_length=20)

@app.get('/api/v2/applications/{id}/documents/{doc_id}')
def document_v2(id:str,doc_id:str,user=Depends(current_user),db=Depends(db_session)):
    a=own_v2(db,id,user)
    doc=next((d for d in (a.closing or {}).get('documents',[]) if d['doc_id']==doc_id),None)
    if not doc:raise HTTPException(404,'Документ не найден')
    return doc

@app.post('/api/v2/applications/{id}/sign/code')
def sign_code_v2(id:str,body:V2Revision,user=Depends(current_user),db=Depends(db_session)):
    a=client_v2(db,id,user,body.revision)
    try:code=disbursement.issue_code(db,a,actor=workflow.actor_of(user))
    except workflow.IllegalTransition as e:raise HTTPException(409,str(e))
    except disbursement.SigningError as e:raise HTTPException(422,str(e))
    db.flush()
    # MOCK: no SMS gateway, so the demo shows the code on screen.
    return {'demo_code':code,'expires_in':disbursement.CODE_TTL_SECONDS,'application':workflow.application_json(db,a,staff=False)}

@app.post('/api/v2/applications/{id}/sign')
def sign_documents_v2(id:str,body:V2SignCode,user=Depends(current_user),db=Depends(db_session)):
    a=client_v2(db,id,user,body.revision)
    try:error=disbursement.sign(db,a,body.code,actor=workflow.actor_of(user))
    except workflow.IllegalTransition as e:raise HTTPException(409,str(e))
    db.flush()
    # A response, not an exception: the used-up attempt must commit.
    if error:return JSONResponse(status_code=422,content={'detail':error,'revision':a.revision})
    return workflow.application_json(db,a,staff=False)

# --- v2 statements from other banks (T23): the answer to a data request (needs_data) ---
@app.post('/api/v2/applications/{id}/statements')
async def statements_v2(id:str,files:list[UploadFile]=File(...),revision:int=Form(...),user=Depends(current_user),db=Depends(db_session)):
    a=client_v2(db,id,user,revision)
    if len(files)>statements.MAX_FILES:raise HTTPException(422,f'Можно загрузить не больше {statements.MAX_FILES} выписок')
    parts=[(Path(f.filename or 'statement').name[:200],await f.read(statements.MAX_FILE_BYTES+1)) for f in files]
    try:workflow.upload_statements(db,a,parts,actor=workflow.actor_of(user))
    except workflow.IllegalTransition as e:raise HTTPException(409,str(e))
    except statements.StatementError as e:raise HTTPException(422,str(e))
    db.flush();return workflow.application_json(db,a,staff=False)

@app.post('/api/v2/applications/{id}/statements/skip')
def skip_statements_v2(id:str,body:V2Revision,user=Depends(current_user),db=Depends(db_session)):
    a=client_v2(db,id,user,body.revision)
    try:workflow.skip_statements(db,a,actor=workflow.actor_of(user))
    except workflow.IllegalTransition as e:raise HTTPException(409,str(e))
    db.flush();return workflow.application_json(db,a,staff=False)

# --- v2 staff actions (T20): review with a reason code, committee with two signatures ---
class V2OfferChange(Input):
    amount:int=Field(gt=0,le=1_000_000_000);term_months:int=Field(ge=1,le=120)
    product:ProductId|None=None;collateral:list[CollateralType]|None=None
class V2Review(V2Revision):
    action:staff_actions.Action;reason_code:str=Field(max_length=100);comment:str=Field(max_length=2000)
    offer_ids:list[str]|None=Field(default=None,max_length=4);changes:list[V2OfferChange]=Field(default=[],max_length=3)
    documents:list[str]=Field(default=[],max_length=10)
class V2Sign(V2Revision):approve:bool;comment:str=Field(max_length=2000);reason_code:str|None=Field(default=None,max_length=100)

def staff_v2(db,id,revision):
    a=db.get(LoanApplication,id)
    if not a:raise HTTPException(404,'Заявка не найдена')
    if a.revision!=revision:raise HTTPException(409,'Заявка изменилась. Обновите её.')
    return a

def staff_action(a,db,run):
    try:outcome=run()
    except workflow.IllegalTransition as e:raise HTTPException(409,str(e))
    except staff_actions.StaffActionError as e:raise HTTPException(422,str(e))
    db.flush();return {'outcome':outcome,'application':workflow.application_json(db,a,staff=True)}

@app.get('/api/v2/staff/reason-codes')
def reason_codes_v2(user=Depends(staff)):return staff_actions.reason_codes()

@app.get('/api/v2/staff/overrides')
def overrides_v2(user=Depends(staff),db=Depends(db_session)):return staff_actions.override_stats(db)

@app.get('/api/v2/staff/model-validation')
def model_validation_v2(user=Depends(risk_manager)):
    report_path=Path(__file__).resolve().parents[1]/'ml'/'scorecard_v1.validation.json'
    try:return ValidationReport.model_validate_json(report_path.read_text(encoding='utf-8')).model_dump(mode='json')
    except FileNotFoundError:raise HTTPException(503,'Отчёт валидации модели пока недоступен')
    except ValidationError:raise HTTPException(503,'Отчёт валидации модели имеет неверный формат')

@app.post('/api/v2/applications/{id}/review')
def review_v2(id:str,body:V2Review,user=Depends(staff),db=Depends(db_session)):
    a=staff_v2(db,id,body.revision)
    changes=[staff_actions.OfferChange(**c.model_dump()) for c in body.changes]
    return staff_action(a,db,lambda:staff_actions.review(db,a,staff_actions.staff_of(user),body.action,body.reason_code,body.comment,
                                                         offer_ids=body.offer_ids,changes=changes,documents=body.documents))

@app.post('/api/v2/applications/{id}/review/sign')
def sign_v2(id:str,body:V2Sign,user=Depends(staff),db=Depends(db_session)):
    a=staff_v2(db,id,body.revision)
    return staff_action(a,db,lambda:staff_actions.sign(db,a,staff_actions.staff_of(user),approve=body.approve,comment=body.comment,reason_code=body.reason_code))

class Message(Input):message:str=Field(min_length=1,max_length=1000);application_id:str|None=None
# --- v2 overdraft pre-approval (T22): the nightly batch's "Вам доступно до X" ---
@app.get('/api/v2/preapproval')
def preapproval_v2(inn:str=Query(pattern=r'^\d{10}(\d{2})?$'),user=Depends(current_user)):
    if user.identity and user.identity.startswith('demo:') and DEMO:
        from .demo_companies import available_demo_inns
        if inn not in available_demo_inns(workflow.default_pipeline().client.world):
            raise HTTPException(403,'Предодобрение доступно только для компании из демо-списка.')
    # SIMPLIFICATION: non-demo client profiles still need a persistent user → company registry (T25).
    result=preapproval.latest(inn)
    if result is None:return None
    return result.model_dump(mode='json') if user.role=='staff' else result.client_view()

@app.post('/api/v2/staff/preapproval/run',status_code=202)
def preapproval_run_v2(user=Depends(staff)):
    last=preapproval.last_run()
    return {'started':preapproval.start_batch(),'last_run':last.isoformat() if last else None}

@app.post('/api/assistant/messages')
def assistant(body:Message,user=Depends(current_user),db=Depends(db_session)):
    text='Помогу разобраться с финансированием. Укажите цель, сумму и срок заявки. Условия рассчитываются по подтверждённым данным компании.'
    lower=body.message.lower()
    if 'ставк' in lower:
        text='Ставка рассчитывается индивидуально: учитываются риск, обеспечение, сумма, срок и расходы на обработку. Точные условия появятся в предложениях по заявке.'
    elif 'линия' in lower or 'линию' in lower:
        text='Кредитная линия — лимит для использования частями. Условия и срок показываются в предложении по заявке.'
    if body.application_id:
        application=own_v2(db,body.application_id,user)
        text='Статус заявки: '+workflow.STATUS_TEXT[workflow.S(application.status)]+'.'
        if application.decision:
            decision=workflow.client_decision(application.decision)
            parts=[text,*decision['route_messages'],*decision['reasons']]
            for offer in decision['offers']:
                parts.append(f"Предложение: {offer['amount']} ₽ на {offer['term_months']} мес., ставка {offer['rate']*100:.2f}% годовых; действительно до {offer['valid_until']}.")
            parts.extend(c['client_text'] for c in decision['counterfactuals'])
            if decision['requested_documents']:
                parts.append('Нужны документы: '+', '.join(decision['requested_documents']))
            if decision['reapply_after']:
                parts.append('Повторная заявка возможна с '+decision['reapply_after'])
            text=' '.join(parts)
    generated=explain(body.message,text)
    return {'message':generated or text,'mode':'model' if generated else 'template','label':'ИИ-помощник' if generated else 'Помощник · справочный режим'}

# Production build is served by the same origin as the API.
DIST=ROOT/'dist'
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')
    @app.get('/{path:path}')
    def frontend(path:str):
        if path.startswith('api/'):raise HTTPException(404)
        return FileResponse(DIST/'index.html')
