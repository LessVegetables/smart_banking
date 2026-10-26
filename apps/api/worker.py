"""Run the v2 workflow, disbursement and pre-approval jobs."""
from .db import init_db

def loop(stop):
    from . import disbursement, preapproval, workflow
    while not stop.wait(0.5):
        try:workflow.tick()  # v2 applications (T19): the status itself is the queue
        except Exception:workflow.log.exception('workflow tick failed')
        try:disbursement.tick()  # conditions -> disbursed (T21)
        except Exception:disbursement.log.exception('disbursement tick failed')
        try:preapproval.tick()  # the overdraft pre-approval batch (T22), in its own thread once a day
        except Exception:preapproval.log.exception('pre-approval tick failed')

if __name__=='__main__':
    import threading
    init_db();loop(threading.Event())
