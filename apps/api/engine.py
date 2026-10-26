"""Pure, deterministic demo lending policy. No network or database side effects."""
import math
from .catalog import PRODUCTS, RULES

def bucket(value, ranges):
    return next((points for upper,points in ranges if value<=upper),ranges[-1][1])

def significant_overdue(a,policy):
    return a.get('has_overdue')=='yes' and a.get('overdue_days',0)>policy['overdue_technical_days'] and a.get('overdue_amount',0)>=policy['overdue_min_amount'] and a.get('overdue_amount',0)>=a.get('overdue_payment',1)*policy['overdue_min_payment_ratio']

def payment(amount, months, annual_rate, line=False):
    r=annual_rate/12
    if line: return amount/months+amount*r
    if not r: return amount/months
    return amount*r/(1-(1+r)**(-months))

def capacity(budget, months, rate, line=False):
    if line: return budget/(1/months+rate/12)
    r=rate/12
    return budget*months if not r else budget*(1-(1+r)**(-months))/r

def mock_checks(a, mode='normal'):
    return [
        {'source':'ЕГРЮЛ / ЕГРИП','status':'ok' if mode!='inactive' else 'stop','message':'Бизнес активен' if mode!='inactive' else 'Бизнес не действует','provider':'demo'},
        {'source':'ФНС','status':'ok','message':'Демонстрационная проверка пройдена','provider':'demo'},
        {'source':'БКИ','status':'error' if mode=='bki_error' else 'ok','message':'Сервис временно недоступен' if mode=='bki_error' else 'История получена из демо-профиля','provider':'demo'}]

def score(a,policy=None):
    policy=policy or RULES
    factors=[]
    def add(code,label,points): factors.append({'code':code,'label':label,'points':points})
    revenue=a['revenue']; debt=a.get('debt',0) if a.get('has_debt')=='yes' else 0
    add('age','Срок работы бизнеса',next((p for upper,p in policy['age_weights'] if a['age']<upper),0))
    add('debt','Долг к годовой выручке',bucket(debt/revenue,policy['debt_weights']))
    add('request','Запрос к годовой выручке',bucket(a['amount']/revenue,policy['request_weights']))
    add('trend','Динамика выручки',policy['trend_weights'][a['trend']])
    if a.get('has_debt')!='yes' and a.get('credit_history')=='no': add('history','Нет кредитной истории',policy['no_history_weight'])
    if a.get('alfa')=='yes': add('alfa','Обороты по счёту в Эта Банке',policy['alfa_bonus'])
    significant=significant_overdue(a,policy)
    if significant and a.get('overdue_months',0)<=policy['overdue_window_months']:
        weight=bucket(a.get('overdue_days',0),policy['overdue_weights'])
        if a.get('overdue_months',0)>policy['overdue_half_weight_after_months']: weight*=0.5
        add('overdue','История просрочек',weight)
    points=sum(f['points'] for f in factors)
    high_min=policy['risk_thresholds'][1]+1
    if significant and a.get('episodes',0)>=policy['repeat_episode_threshold'] and points<high_min:
        add('episodes','Повторные эпизоды: высокий риск',high_min-points); points=high_min
    low,medium,high=policy['risk_thresholds']
    risk='low' if points<=low else 'medium' if points<=medium else 'high' if points<=high else 'very_high'
    return points,risk,factors

def evaluate(a, *, check_mode='normal', documents_reviewed=False, manual=False,policy=None):
    policy=policy or RULES
    products={p['id']:p for p in policy['products']}
    checks=mock_checks(a,check_mode)
    result={'rule_version':policy['version'],'policy_snapshot':policy,'checks':checks,'factors':[],'offers':[], 'reason_codes':[], 'reasons':[], 'score':None,'risk':None,'documents':[]}
    def outcome(status,code,reason):
        result.update(status=status); result['reason_codes'].append(code);result['reasons'].append(reason);return result
    if check_mode=='inactive': return outcome('declined','inactive','В демонстрационной проверке бизнес не действует.')
    if a.get('industry')=='prohibited' or any(a.get('okved','').startswith(p) for p in policy['okved_stop_prefixes']):return outcome('declined','industry_stop','Для этого вида деятельности в демо-каталоге нет доступного финансирования.')
    significant=significant_overdue(a,policy)
    if significant and (a.get('overdue_current')=='yes' or (a.get('overdue_days',0)>=policy['overdue_stop_days'] and a.get('overdue_months',99)<=policy['overdue_window_months'])):
        return outcome('declined','overdue_stop','Сначала необходимо урегулировать значимые просрочки. Повторная заявка возможна после изменения кредитной истории.')
    revenue=a.get('revenue',0)
    if revenue<=0:return outcome('manual_review','no_revenue','Для бизнеса без выручки требуется индивидуальное рассмотрение.')
    debt=a.get('debt',0) if a.get('has_debt')=='yes' else 0
    if debt/revenue>policy['stop_debt_ratio']:return outcome('declined','debt_stop','Действующий долг превышает допустимую долю годовой выручки. Уменьшите долговую нагрузку перед новой заявкой.')
    if any(c['status']=='error' for c in checks):return outcome('checking','check_unavailable','БКИ временно недоступно. Решение будет рассчитано после повторной проверки.')
    if (a.get('industry')=='manual' or any(a.get('okved','').startswith(p) for p in policy['okved_manual_prefixes'])) and not manual:return outcome('manual_review','industry_review','Для этой деятельности предусмотрено ручное рассмотрение.')
    points,risk,factors=score(a,policy);result.update(score=points,risk=risk,factors=factors)
    if risk=='very_high':return outcome('declined','risk_stop','Демонстрационная модель оценивает риск как очень высокий. Проверьте нагрузку и динамику выручки.')
    if risk=='high' and (not manual or not documents_reviewed):
        if documents_reviewed:return outcome('manual_review','high_review','После проверки документов риск остаётся высоким. Заявка передана специалисту.')
        result['documents']=['Выписка по счетам за 6–12 месяцев','Бухгалтерская или управленческая отчётность']
        if a['purpose']=='equipment':result['documents'].append('Счёт на оборудование и сведения о залоге')
        return outcome('needs_data','high_documents','Для оценки высокой нагрузки нужны подтверждённые финансовые документы.')
    primary=products[a['purpose']]
    if a['purpose']=='equipment' and a.get('collateral')!='yes':return outcome('manual_review','collateral_missing','Для кредита на оборудование нужен залог. Специалист поможет рассмотреть обеспечение или другую цель финансирования.')
    if a['purpose']=='equipment' and a['amount']>a.get('equipment_cost',0)-a.get('down_payment',0):
        result['reasons'].append('Сумма ограничена стоимостью оборудования за вычетом собственных средств.')
    coefficient=policy['risk_coefficients'][risk]
    existing=a.get('payments',0) if a.get('has_debt')=='yes' else 0
    budget=max(0,policy['payment_ratio']*revenue/12-existing)
    def make_offer(product, term):
        limits=[{'code':'risk','label':'По уровню риска','amount':a['amount']*coefficient},
                {'code':'payment','label':'По платёжной нагрузке','amount':capacity(budget,term,product['annual_rate'],product['id']=='line')},
                {'code':'revenue','label':'По годовой выручке','amount':revenue*product['revenue_cap']},
                {'code':'product','label':'Максимум продукта','amount':product['max']}]
        if product['id']=='equipment':
            if a.get('collateral')!='yes':return None
            limits += [{'code':'collateral','label':'По стоимости залога','amount':a.get('collateral_value',0)*policy['collateral_ratio']}, {'code':'purchase','label':'Стоимость покупки минус свои средства','amount':max(0,a.get('equipment_cost',0)-a.get('down_payment',0))}]
        amount=math.floor(min(x['amount'] for x in limits)/1000)*1000
        if amount<product['min']:return None
        binding=min(limits,key=lambda x:x['amount'])
        return {'product_id':product['id'],'name':product['name'],'amount':amount,'term':term,'annual_rate':product['annual_rate'],'monthly_payment':round(payment(amount,term,product['annual_rate'],product['id']=='line'),2),'expected_payment':round(payment(amount*a.get('utilization',100)/100,term,product['annual_rate'],True),2) if product['id']=='line' else None,'limits':limits,'binding':binding['label'],'model':'Полная выборка, равномерное погашение основного долга' if product['id']=='line' else 'Аннуитетный платёж','requires_collateral':product['id']=='equipment','alternative':product['id']!=primary['id']}
    term=max(primary['min_term'],min(a['term'],primary['max_term']))
    offer=make_offer(primary,term)
    if offer:result['offers'].append(offer)
    if offer and term<primary['max_term'] and a.get('flexible') in ('term','both'):
        longer=make_offer(primary,primary['max_term'])
        if longer and longer['amount']>offer['amount']:result['offers'].append(longer)
    if not result['offers']:
        # Only consider an alternative compatible with the stated operating need.
        for p in products.values():
            if p['id']==primary['id'] or p['id']=='equipment' or primary['id']=='equipment':continue
            other=make_offer(p,max(p['min_term'],min(a['term'],p['max_term'])))
            if other:result['offers'].append(other)
    if not result['offers']:return outcome('declined','below_minimum','Допустимая сумма ниже минимума подходящих продуктов. Уменьшите действующие платежи или дополните финансовые данные.')
    first=result['offers'][0]
    exact=first['amount']==a['amount'] and first['term']==a['term'] and first['product_id']==a['purpose']
    if exact and risk=='low':return outcome('approved','all_clear','Запрошенные параметры проходят все демонстрационные ограничения.')
    reason=f"Предлагаем пересчитанные условия. Ограничение: {first['binding'].lower()}."
    if first['alternative']:reason='Первоначальный продукт не подходит по минимальной сумме. Доступна альтернатива для оборотной потребности — подтвердите смену продукта.'
    return outcome('counteroffer','adjusted',reason)
