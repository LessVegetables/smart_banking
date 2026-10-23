import math
import pytest
from apps.api.engine import evaluate, score, payment, capacity
from apps.api.personas import persona_answers

@pytest.mark.parametrize('persona,status',[
 ('healthy','approved'),('medium','counteroffer'),('equipment','counteroffer'),
 ('documents','needs_data'),('manual','needs_data'),('alternative','counteroffer'),('stop','declined')])
def test_seven_personas(persona,status):assert evaluate(persona_answers(persona))['status']==status

def test_confirmed_financials_change_decision():
    a=persona_answers('documents');a.update(revenue=36000000,trend='stable')
    assert evaluate(a,documents_reviewed=True)['status']=='approved'
    assert evaluate(persona_answers('manual'),documents_reviewed=True)['status']=='manual_review'

def test_manual_high_risk_is_not_automatic_approval():
    a=persona_answers('manual');r=evaluate(a,documents_reviewed=True,manual=True)
    assert r['status']=='counteroffer';assert r['offers'][0]['amount']<=a['amount']*.5

def test_manual_cannot_skip_high_risk_documents():
    assert evaluate(persona_answers('manual'),manual=True)['status']=='needs_data'

def test_saved_policy_can_reproduce_an_older_decision():
    from copy import deepcopy
    from apps.api.catalog import RULES
    old=deepcopy(RULES);a=persona_answers('healthy')
    expected=evaluate(a,policy=old)
    changed=deepcopy(old);changed['products'][0]['annual_rate']=.9;changed['version']='test-new-policy'
    assert evaluate(a,policy=changed)['offers'][0]['monthly_payment']!=expected['offers'][0]['monthly_payment']
    assert evaluate(a,policy=old)==expected

def test_alternative_is_compatible_and_needs_confirmation():
    r=evaluate(persona_answers('alternative'))
    assert r['status']=='counteroffer';assert r['offers'][0]['product_id']=='working';assert r['offers'][0]['alternative']

def test_low_risk_still_obeys_payment_capacity():
    a=persona_answers('healthy');a.update(has_debt='yes',debt=1000000,payments=300000)
    r=evaluate(a);assert r['risk']=='low';assert r['status']=='counteroffer'
    assert r['offers'][0]['monthly_payment']+300000<=a['revenue']/12*.35+.01

def test_debt_stop_boundary():
    a=persona_answers('healthy');a.update(has_debt='yes',debt=a['revenue']*.8,payments=1)
    assert 'debt_stop' not in evaluate(a)['reason_codes']
    a['debt']+=1;assert evaluate(a)['reason_codes']==['debt_stop']

@pytest.mark.parametrize('days,months,amount,current,stop',[(89,12,50000,'no',False),(90,36,50000,'no',True),(90,37,50000,'no',False),(95,12,999,'yes',False),(5,1,50000,'yes',False),(6,1,50000,'yes',True)])
def test_overdue_boundaries(days,months,amount,current,stop):
    a=persona_answers('healthy');a.update(has_overdue='yes',overdue_days=days,overdue_months=months,overdue_amount=amount,overdue_payment=100000,overdue_current=current,episodes=0)
    assert ('overdue_stop' in evaluate(a)['reason_codes'])==stop

def test_older_overdue_half_weight():
    a=persona_answers('healthy');a.update(has_overdue='yes',overdue_days=60,overdue_months=25,overdue_amount=5000,overdue_payment=100000,overdue_current='no',episodes=0)
    assert next(f['points'] for f in score(a)[2] if f['code']=='overdue')==12.5

@pytest.mark.parametrize('points,risk',[(30,'low'),(30.5,'medium'),(60,'medium'),(60.5,'high'),(90,'high'),(90.5,'very_high')])
def test_risk_boundaries(monkeypatch,points,risk):
    import apps.api.engine as e
    a=persona_answers('healthy');a.update(alfa='no',trend='stable')
    monkeypatch.setitem(e.RULES,'age_weights',[[1000,points]])
    assert score(a)[1]==risk

def test_unavailable_bki_is_not_a_credit_decision():
    r=evaluate(persona_answers('healthy'),check_mode='bki_error')
    assert r['status']=='checking';assert not r['offers'];assert r['score'] is None

def test_stop_short_circuits_scoring():
    r=evaluate(persona_answers('stop'));assert r['score'] is None;assert not r['factors']

def test_equipment_collateral_is_a_real_limit():
    a=persona_answers('equipment');a['collateral_value']=1000000
    r=evaluate(a);assert r['offers'][0]['amount']<=700000

@pytest.mark.parametrize('line',[False,True])
def test_payment_capacity_roundtrip(line):
    a=capacity(50000,12,.24,line);assert math.isclose(payment(a,12,.24,line),50000,rel_tol=1e-10)

def test_line_is_full_draw_stress_test():
    a=persona_answers('alternative');a.update(revenue=12000000,payments=10000)
    r=evaluate(a);o=r['offers'][0]
    assert o['product_id']=='line';assert o['expected_payment']<o['monthly_payment']
    assert o['monthly_payment']==round(o['amount']/o['term']+o['amount']*o['annual_rate']/12,2)

def test_zero_revenue_has_no_division_error():
    a=persona_answers('healthy');a['revenue']=0;assert evaluate(a)['status']=='manual_review'

@pytest.mark.parametrize('code,status',[('92.10','declined'),('64.90','manual_review'),('56.10','approved')])
def test_synthetic_okved_directory(code,status):
    a=persona_answers('healthy');a['okved']=code;assert evaluate(a)['status']==status
