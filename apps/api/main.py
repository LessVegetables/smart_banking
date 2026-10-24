import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.exc import IntegrityError

from .db import Session, User, Login, Survey, Application, SubmissionKey, Document, Job, LoanApplication, DATA, now, init_db, uid
from .catalog import RULES, PRODUCTS, QUESTIONS, active_questions, clean_answers, validate_answer, business_age_months, ROOT
from .engine import evaluate
from .personas import PERSONAS, persona_answers
from .worker import loop, event, save_decision
from .storage import storage
from .assistant import explain
from .contracts import CollateralType, ProductId
from . import disbursement, staff_actions, workflow

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

app=FastAPI(title='Альфа · Бизнес — Demo API',version='1.0.0',lifespan=lifespan)

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

def own(db,model,id,user):
    obj=db.get(model,id)
    if not obj or (user.role!='staff' and obj.user_id!=user.id):raise HTTPException(404,'Объект не найден')
    return obj

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
def health():return {'ok':True,'demo':DEMO,'rules':RULES['version']}

@app.post('/api/auth/demo')
def demo_login(body:DemoLogin,response:Response,db=Depends(db_session)):
    if not DEMO:raise HTTPException(404)
    user=User(name=body.name.strip());db.add(user);db.flush()
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
    for model in (Survey,Application):db.execute(update(model).where(model.user_id==user.id).values(user_id=owner.id))
    db.execute(update(Login).where(Login.user_id==user.id).values(user_id=owner.id))
    return {'ok':True}

@app.get('/api/catalog')
def catalog():return {'products':list(PRODUCTS.values()),'sections':['Задача','Бизнес','Финансы','Обязательства','Детали'],'version':RULES['version'],'questionnaire_version':RULES['questionnaire_version'],'demo':DEMO}

@app.get('/api/personas')
def personas():return [{k:v for k,v in p.items() if k!='answers'} for p in PERSONAS] if DEMO else []

def survey_json(s):
    active=active_questions(s.answers)
    return {'id':s.id,'version':s.version,'answers':clean_answers(s.answers),'questions':active,'all_questions_count':len(QUESTIONS),'complete':all(q['id'] in s.answers for q in active)}

@app.get('/api/surveys/current')
def current_survey(user=Depends(current_user),db=Depends(db_session)):
    s=db.scalar(select(Survey).where(Survey.user_id==user.id).order_by(Survey.created.desc()))
    if not s:s=Survey(user_id=user.id,answers={},history=[]);db.add(s);db.flush()
    return survey_json(s)

@app.post('/api/surveys')
def new_survey(user=Depends(current_user),db=Depends(db_session)):
    s=Survey(user_id=user.id,answers={},history=[]);db.add(s);db.flush();return survey_json(s)

class AnswerInput(Input):version:int;question_id:str;value:Any
@app.patch('/api/surveys/{id}/answers')
def answer(id:str,body:AnswerInput,user=Depends(current_user),db=Depends(db_session)):
    s=own(db,Survey,id,user)
    question=next((q for q in active_questions(s.answers) if q['id']==body.question_id),None)
    if not question:raise HTTPException(422,'Вопрос не относится к текущему сценарию')
    try:value=validate_answer(question,body.value,s.answers)
    except ValueError as e:raise HTTPException(422,str(e))
    changed={**s.answers,body.question_id:value};changed=clean_answers(changed)
    # Invalidate derived dependent values when their source changes.
    if body.question_id=='revenue':changed.pop('monthly',None)
    if body.question_id=='form':changed.pop('inn',None);changed.pop('ogrn',None)
    hist=[*s.history,{'at':now(),'question':body.question_id,'previous':s.answers.get(body.question_id),'value':value}]
    count=db.execute(update(Survey).where(Survey.id==id,Survey.version==body.version).values(answers=changed,history=hist,version=Survey.version+1)).rowcount
    if not count:raise HTTPException(409,'Анкета изменилась в другом окне. Обновите страницу.')
    db.expire(s);return survey_json(s)

@app.post('/api/surveys/{id}/persona/{persona}')
def fill_persona(id:str,persona:str,user=Depends(current_user),db=Depends(db_session)):
    if not DEMO:raise HTTPException(404)
    s=own(db,Survey,id,user)
    try:a=persona_answers(persona)
    except ValueError:raise HTTPException(404)
    s.answers=clean_answers(a);s.version+=1;s.history=[*s.history,{'at':now(),'action':'demo_persona','persona':persona}]
    return survey_json(s)

def complete(s):
    a=clean_answers(s.answers)
    for q in active_questions(a):
        if q['id'] not in a:raise HTTPException(422,f'Заполните вопрос: {q["title"]}')
        try:validate_answer(q,a[q['id']],a)
        except ValueError as e:raise HTTPException(422,str(e))
    if a.get('has_debt')=='yes' and a.get('payments',0)>a.get('debt',0):raise HTTPException(422,'Месячные платежи не могут превышать указанный остаток долга')
    if a.get('purpose')=='equipment' and a.get('down_payment',0)>a.get('equipment_cost',0):raise HTTPException(422,'Собственные средства превышают стоимость покупки')
    a['age']=business_age_months(a['registered_at'])
    return a

class VersionInput(Input):version:int
@app.post('/api/surveys/{id}/recommendations')
def recommend(id:str,body:VersionInput,user=Depends(current_user),db=Depends(db_session)):
    s=own(db,Survey,id,user)
    if s.version!=body.version:raise HTTPException(409,'Версия анкеты изменилась')
    return {'preliminary':True,'survey_version':s.version,**evaluate(complete(s))}

class Submission(Input):survey_id:str;version:int;confirmed:bool;idempotency_key:str=Field(min_length=10,max_length=100)
@app.post('/api/applications')
def submit(body:Submission,user=Depends(current_user),db=Depends(db_session)):
    if not body.confirmed:raise HTTPException(422,'Подтвердите подачу заявки')
    key=f'{user.id}:{body.idempotency_key}'
    saved_key=db.get(SubmissionKey,key)
    if saved_key:return application_json(own(db,Application,saved_key.application_id,user),db)
    s=own(db,Survey,body.survey_id,user)
    if s.version!=body.version:raise HTTPException(409,'Анкета изменилась. Проверьте сводку ещё раз.')
    # One application per survey snapshot, even if another client uses a new key.
    snapshot_key=f'{user.id}:{s.id}:{s.version}'
    old=db.scalar(select(Application).where(Application.submission_key==snapshot_key))
    if old:
        db.add(SubmissionKey(key=key,application_id=old.id))
        return application_json(old,db)
    a=Application(user_id=user.id,survey_id=s.id,submission_key=snapshot_key,answers=complete(s),policy_snapshot=RULES,events=[],decisions=[])
    db.add(a);db.flush();event(a,'submitted','Заявка подтверждена и подана');db.add(Job(application_id=a.id));db.add(SubmissionKey(key=key,application_id=a.id))
    return application_json(a,db)

def application_json(a,db):
    db.flush()
    docs=db.scalars(select(Document).where(Document.application_id==a.id)).all()
    return {'id':a.id,'revision':a.revision,'status':a.status,'name':a.answers.get('name'),'answers':a.answers,'created':a.created,'events':a.events,'decision':a.decisions[-1] if a.decisions else None,'decisions':a.decisions,'selected_offer':a.selected_offer,'offer_expires':a.offer_expires,'documents':[{'id':d.id,'name':d.name,'size':d.size,'status':d.status,'created':d.created} for d in docs]}

@app.get('/api/applications')
def applications(user=Depends(current_user),db=Depends(db_session)):
    rows=db.scalars(select(Application).where(Application.user_id==user.id).order_by(Application.created.desc())).all()
    return [application_json(a,db) for a in rows]

@app.get('/api/applications/{id}')
def application(id:str,user=Depends(current_user),db=Depends(db_session)):return application_json(own(db,Application,id,user),db)

class Action(Input):action:str;offer_index:int=0;revision:int
@app.post('/api/applications/{id}/actions')
def action(id:str,body:Action,user=Depends(current_user),db=Depends(db_session)):
    a=own(db,Application,id,user)
    if a.revision!=body.revision:raise HTTPException(409,'Заявка изменилась. Обновите её.')
    if body.action=='accept' and a.status in ('approved','counteroffer'):
        if not a.offer_expires or a.offer_expires<time.time():raise HTTPException(409,'Срок предложения истёк. Подайте новую версию заявки.')
        offers=a.decisions[-1]['offers']
        if not 0<=body.offer_index<len(offers):raise HTTPException(422,'Предложение не найдено')
        a.selected_offer=offers[body.offer_index];event(a,'signing','Условия приняты. Доступно демонстрационное подписание.')
    elif body.action=='sign' and a.status=='signing':event(a,'issued','Демонстрационное подписание и выдача завершены. Реальные деньги не переводились.')
    elif body.action=='decline' and a.status=='counteroffer':event(a,'closed','Клиент отклонил предложение')
    elif body.action=='retry' and a.status=='checking':db.add(Job(application_id=a.id));event(a,'checking','Повтор проверки запрошен')
    else:raise HTTPException(409,'Действие недоступно в текущем статусе')
    a.revision+=1;return application_json(a,db)

@app.post('/api/applications/{id}/documents')
async def upload(id:str,file:UploadFile=File(...),user=Depends(current_user),db=Depends(db_session)):
    a=own(db,Application,id,user)
    if a.status not in ('needs_data','manual_review'):raise HTTPException(409,'Документы сейчас не запрашиваются')
    name=Path(file.filename or '').name
    if Path(name).suffix.lower() not in ('.pdf','.png','.jpg','.jpeg'):raise HTTPException(422,'Поддерживаются PDF, PNG и JPEG')
    content=await file.read(10*1024*1024+1)
    if len(content)>10*1024*1024:raise HTTPException(413,'Файл должен быть меньше 10 МБ')
    media='application/pdf' if content.startswith(b'%PDF-') else 'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if content.startswith(b'\xff\xd8\xff') else None
    if not media:raise HTTPException(422,'Содержимое файла не соответствует PDF или изображению')
    d=Document(id=uid(),application_id=a.id,name=name[:200],media_type=media,size=len(content));db.add(d)
    storage().put(d.id,content,media);a.revision+=1;a.events=[*a.events,{'at':now(),'status':a.status,'message':f'Документ загружен: {d.name}. Ожидает проверки.'}]
    db.flush();return application_json(a,db)

@app.get('/api/documents/{id}')
def download(id:str,user=Depends(current_user),db=Depends(db_session)):
    d=db.get(Document,id)
    if not d:raise HTTPException(404)
    own(db,Application,d.application_id,user)
    try:content=storage().get(d.id)
    except FileNotFoundError:raise HTTPException(404,'Файл отсутствует в хранилище')
    return Response(content,media_type=d.media_type,headers={'Content-Disposition':f'attachment; filename="document{Path(d.name).suffix}"'})

@app.get('/api/staff/reviews')
def reviews(user=Depends(staff),db=Depends(db_session)):
    rows=db.scalars(select(Application).where(Application.status.in_(['manual_review','needs_data'])).order_by(Application.created)).all()
    return [application_json(a,db) for a in rows]

class Review(Input):
    action:str
    comment:str=Field(min_length=10,max_length=2000)
    revision:int
    verified_revenue:float|None=Field(default=None,gt=0,le=1e10)
    verified_trend:str|None=None
    offer_index:int=0

@app.post('/api/staff/reviews/{id}')
def review(id:str,body:Review,user=Depends(staff),db=Depends(db_session)):
    a=own(db,Application,id,user)
    if a.revision!=body.revision or a.status not in ('needs_data','manual_review'):raise HTTPException(409,'Состояние заявки изменилось')
    if body.action=='documents':
        docs=db.scalars(select(Document).where(Document.application_id==id)).all()
        if not docs:raise HTTPException(422,'Сначала загрузите подтверждающий документ')
        revised=dict(a.answers)
        if body.verified_revenue is not None:
            revised['revenue']=body.verified_revenue
            revised.pop('monthly',None)
        if body.verified_trend is not None:
            if body.verified_trend not in ('growing','stable','falling'):raise HTTPException(422,'Неверная динамика выручки')
            revised['trend']=body.verified_trend
        a.answers=revised;a.documents_reviewed=1
        for d in docs:d.status='verified'
        a.revision+=1
        result=evaluate(revised,documents_reviewed=True,policy=a.policy_snapshot)
    elif body.action=='approve':
        result=evaluate(a.answers,documents_reviewed=bool(a.documents_reviewed),manual=True,policy=a.policy_snapshot)
        if result['status'] not in ('approved','counteroffer'):raise HTTPException(422,'Заявка не проходит обязательные ограничения. Одобрение недоступно.')
        a.revision+=1
    elif body.action=='decline':
        a.revision+=1
        result={'status':'declined','rule_version':a.policy_snapshot['version'],'policy_snapshot':a.policy_snapshot,'offers':[],'checks':[],'factors':[],'score':None,'risk':None,'reason_codes':['manual_decline'],'reasons':['По результатам индивидуального рассмотрения финансирование сейчас недоступно.'],'documents':[]}
    else:raise HTTPException(422,'Неизвестное действие')
    a.events=[*a.events,{'at':now(),'status':'review','message':body.comment,'actor':user.id}]
    save_decision(a,result);return application_json(a,db)

# --- v2 applications (T19): statuses SPEC §13, audit log; staff actions (T20) below ---
class V2Submission(Input):idempotency_key:str=Field(min_length=8,max_length=200);request:dict[str,Any]
class V2Revision(Input):revision:int
class V2Accept(V2Revision):offer_id:str=Field(min_length=1,max_length=200)

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

class Message(Input):message:str=Field(min_length=1,max_length=1000);question_id:str|None=None;application_id:str|None=None
@app.post('/api/assistant/messages')
def assistant(body:Message,user=Depends(current_user),db=Depends(db_session)):
    # Complete offline fallback: no model key and no fabricated credit decision.
    text='Помогу разобраться с анкетой. Выручка — все продажи до расходов. Остаток долга и ежемесячный платёж указываются отдельно. Все предложения здесь рассчитываются по демонстрационным правилам.'
    if body.application_id:
        a=own(db,Application,body.application_id,user)
        if a.decisions:text=' '.join(a.decisions[-1]['reasons'])+' Решение рассчитано сервером по версии '+RULES['version']+'.'
    elif body.question_id:
        q=next((q for q in QUESTIONS if q['id']==body.question_id),None)
        if q:text=q['hint'] or f'Вопрос «{q["title"]}» помогает уточнить профиль бизнеса. Укажите фактические данные; вернуться и исправить ответ можно до подачи.'
    lower=body.message.lower()
    if 'ставк' in lower:text='Условные годовые ставки демо: оборотный кредит — 24%, оборудование — 21%, линия — 25%. Это параметры учебной модели, не банковское предложение.'
    elif 'линия' in lower or 'линию' in lower:text='Кредитная линия — лимит для использования частями. В демо платёж проверяется при полной выборке: равномерное погашение долга плюс проценты на остаток. Ожидаемое использование показывается отдельно.'
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
