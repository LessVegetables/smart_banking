"""Durable, retryable jobs. The conditional UPDATE claims each job once."""
import time
from sqlalchemy import select, update
from .db import Session, Job, Application, now, init_db
from .engine import evaluate
from .catalog import RULES

def event(app,status,message):
    app.status=status
    app.events=[*app.events,{'at':now(),'status':status,'message':message}]

def save_decision(app,result):
    app.revision+=1
    app.decisions=[*app.decisions,{'at':now(),'revision':app.revision,'input':app.answers,**result}]
    app.offer_expires=time.time()+app.policy_snapshot['offer_days']*86400 if result['offers'] else None
    event(app,result['status'],result['reasons'][-1] if result['reasons'] else 'Расчёт завершён')

def run_once():
    with Session.begin() as db:
        job=db.scalar(select(Job).where(Job.state=='pending',Job.available<=time.time()).order_by(Job.available))
        if not job:return False
        job_id=job.id
        claimed=db.execute(update(Job).where(Job.id==job_id,Job.state=='pending').values(state='running',attempts=Job.attempts+1,available=time.time()+60)).rowcount
        if not claimed:return True
    try:
        with Session.begin() as db:
            job=db.get(Job,job_id);app=db.get(Application,job.application_id)
            if app.status not in ('submitted','checking'):job.state='done';return True
            event(app,'checking','Демонстрационные проверки ЕГРЮЛ, ФНС и БКИ')
            result=evaluate(app.answers,documents_reviewed=bool(app.documents_reviewed),policy=app.policy_snapshot)
            if result['score'] is not None:event(app,'scoring','Расчёт риска и доступных условий')
            save_decision(app,result);job.state='done'
    except Exception:
        with Session.begin() as db:
            job=db.get(Job,job_id);job.error='Ошибка обработки. Подробности скрыты.'
            job.state='pending' if job.attempts<3 else 'failed';job.available=time.time()+5*job.attempts
            if job.state=='failed':event(db.get(Application,job.application_id),'manual_review','Автоматический расчёт требует проверки оператором')
    return True

def loop(stop):
    while not stop.wait(0.5):
        # Recover a worker crash after the lease expires.
        with Session.begin() as db:
            db.execute(update(Job).where(Job.state=='running',Job.available<time.time()).values(state='pending'))
        run_once()

if __name__=='__main__':
    import threading
    init_db();loop(threading.Event())
