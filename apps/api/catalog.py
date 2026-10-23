import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES = json.loads((ROOT / 'config/rules.json').read_text(encoding='utf-8'))
PRODUCTS = {p['id']: p for p in RULES['products']}

def q(id, section, title, kind='choice', options=None, hint='', **kw):
    return dict(id=id, section=section, title=title, type=kind, options=[{'value':v,'label':l} for v,l in (options or [])], hint=hint, **kw)

YN=[('yes','Да'),('no','Нет')]
QUESTIONS = [
 q('purpose','Задача','Что поможет вашему бизнесу вырасти?',options=[('working','Пополнить оборотные средства'),('equipment','Купить оборудование'),('line','Закрыть кассовые разрывы')],hint='Начнём с вашей задачи. Подберём продукт под неё, а не наоборот.'),
 q('goal','Задача','Расскажите чуть подробнее о вашей задаче','text',hint='Например: закупить зерно и упаковку для двух кофеен.',max_length=500),
 q('urgency','Задача','Когда вам понадобятся деньги?',options=[('week','В течение недели'),('month','В течение месяца'),('planning','Пока планирую')]),
 q('amount','Задача','Сколько денег нужно вашему бизнесу?','money',hint='Укажите сумму кредита или желаемый лимит линии.',min=10000,max=100000000),
 q('term','Задача','За сколько месяцев планируете вернуть деньги?','number',min=3,max=60,suffix='мес.',hint='Для оборудования — 12–60 месяцев, для оборотного кредита и линии — 3–12.'),
 q('name','Бизнес','Как называется ваш бизнес?','text',max_length=150),
 q('form','Бизнес','В какой форме работает ваш бизнес?',options=[('ip','ИП'),('ooo','ООО')]),
 q('inn','Бизнес','Укажите ИНН вашего бизнеса','inn',hint='10 цифр для ООО, 12 — для ИП. В демо используются синтетические реквизиты.'),
 q('ogrn','Бизнес','Укажите ОГРН или ОГРНИП','ogrn',hint='13 цифр для ООО или 15 цифр для ИП. В демо используются синтетические реквизиты.'),
 q('registered_at','Бизнес','Когда был зарегистрирован бизнес?','date',hint='Срок работы рассчитаем автоматически по дате регистрации.'),
 q('director','Бизнес','Как зовут руководителя бизнеса?','text',max_length=150),
 q('industry','Бизнес','В какой сфере вы работаете?',options=[('food','Кафе и рестораны'),('retail','Торговля'),('services','Услуги'),('production','Производство'),('it','IT и технологии'),('manual','Деятельность с ручной проверкой'),('prohibited','Отрасль из демо-стоп-листа')],hint='Список для демонстрации. Это не отраслевые правила банка.'),
 q('okved','Бизнес','Какой у бизнеса основной ОКВЭД?','okved',hint='Укажите код из реквизитов, например 56.10. В MVP отраслевые проверки демонстрационные.'),
 q('region','Бизнес','В каком городе работает бизнес?','text',max_length=100),
 q('employees','Бизнес','Сколько человек в вашей команде?','number',min=0,max=10000),
 q('revenue','Финансы','Какая выручка была за последние 12 месяцев?','money',min=0,max=10000000000,hint='Все поступления от продаж до вычета расходов. Если бизнес моложе года — фактическая выручка за период.'),
 q('trend','Финансы','Как меняется выручка?',options=[('growing','Растёт'),('stable','Примерно на одном уровне'),('falling','Снижается')]),
 q('seasonal','Финансы','Есть ли выраженная сезонность?',options=YN),
 q('monthly','Финансы','Как распределяется выручка по месяцам?','months',when={'seasonal':'yes'},hint='12 значений от самого раннего месяца к последнему. Сумма должна совпадать с годовой выручкой.'),
 q('expenses','Финансы','Сколько составляют обычные расходы в месяц?','money',min=0,max=1000000000,hint='Аренда, команда, закупки и другие регулярные расходы. Без платежей по кредитам.'),
 q('alfa','Финансы','Есть действующий счёт с оборотами в Альфа-Банке?',options=YN),
 q('has_debt','Обязательства','Есть действующие кредиты у бизнеса?',options=YN),
 q('debt','Обязательства','Какой остаток долга по всем кредитам?','money',min=1,max=10000000000,when={'has_debt':'yes'}),
 q('payments','Обязательства','Сколько платите по действующим кредитам в месяц?','money',min=1,max=1000000000,when={'has_debt':'yes'}),
 q('credit_history','Обязательства','Бизнес раньше пользовался кредитами?',options=YN,when={'has_debt':'no'}),
 q('has_overdue','Обязательства','Были ли просрочки за последние три года?',options=YN),
 q('overdue_days','Обязательства','Как долго длилась самая большая просрочка?','number',min=1,max=1000,suffix='дней',when={'has_overdue':'yes'}),
 q('overdue_months','Обязательства','Сколько месяцев прошло с этой просрочки?','number',min=0,max=36,when={'has_overdue':'yes'}),
 q('overdue_amount','Обязательства','Какой была сумма просрочки?','money',min=1,max=100000000,when={'has_overdue':'yes'}),
 q('overdue_payment','Обязательства','Какой платёж был предусмотрен договором?','money',min=1,max=100000000,when={'has_overdue':'yes'}),
 q('overdue_current','Обязательства','Эта просрочка ещё не погашена?',options=YN,when={'has_overdue':'yes'}),
 q('episodes','Обязательства','Сколько значимых просрочек было за последний год?','number',min=0,max=100,when={'has_overdue':'yes'},hint='Не учитывайте технические задержки до 5 дней и незначительные суммы.'),
 q('equipment_name','Детали','Какое оборудование планируете купить?','text',when={'purpose':'equipment'},max_length=300),
 q('equipment_cost','Детали','Сколько стоит оборудование?','money',min=1,max=100000000,when={'purpose':'equipment'}),
 q('down_payment','Детали','Сколько готовы внести своих средств?','money',min=0,max=100000000,when={'purpose':'equipment'}),
 q('collateral','Детали','Готовы предоставить оборудование в залог?',options=YN,when={'purpose':'equipment'}),
 q('collateral_value','Детали','Какова ориентировочная стоимость залога?','money',min=0,max=100000000,when={'purpose':'equipment','collateral':'yes'},hint='В демо доступная сумма ограничивается 70% стоимости обеспечения.'),
 q('invoice','Детали','Есть счёт или предложение поставщика?',options=YN,when={'purpose':'equipment'}),
 q('gap','Детали','Какой максимальный кассовый разрыв бывает?','money',min=1,max=100000000,when={'purpose':'line'}),
 q('receivable_days','Детали','Через сколько дней приходят задержанные поступления?','number',min=1,max=365,when={'purpose':'line'}),
 q('utilization','Детали','Какую часть лимита планируете использовать?','number',min=1,max=100,suffix='%',when={'purpose':'line'},hint='Основной расчёт всё равно проверит полную выборку лимита.'),
 q('flexible','Детали','Какие изменения условий вам подойдут?',options=[('amount','Можно уменьшить сумму'),('term','Можно увеличить срок'),('both','Готов рассмотреть оба варианта'),('none','Нужны указанные условия')]),
 q('documents_ready','Детали','Какие документы уже готовы?',options=[('all','Выписки и отчётность'),('statement','Только выписки'),('none','Подготовлю по запросу')]),
]

def active_questions(answers):
    return [x for x in QUESTIONS if all(answers.get(k)==v for k,v in x.get('when',{}).items())]

def clean_answers(answers):
    return {q['id']:answers[q['id']] for q in active_questions(answers) if q['id'] in answers}

def today():return datetime.now(ZoneInfo('Asia/Novosibirsk')).date()

def business_age_months(registered_at):
    registered=date.fromisoformat(registered_at);current=today()
    return max(0,(current.year-registered.year)*12+current.month-registered.month-(current.day<registered.day))

def validate_answer(question, value, answers):
    kind=question['type']
    if kind=='choice':
        if value not in [o['value'] for o in question['options']]: raise ValueError('Выберите один из вариантов')
    elif kind in ('money','number'):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not question['min']<=value<=question['max']: raise ValueError('Значение вне допустимого диапазона')
        if kind=='number' and int(value)!=value: raise ValueError('Введите целое число')
    elif kind=='months':
        if not isinstance(value,list) or len(value)!=12 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or v<0 or v>1e10 for v in value): raise ValueError('Нужны 12 неотрицательных значений')
        if abs(sum(value)-answers.get('revenue',0))>1: raise ValueError('Сумма по месяцам должна совпадать с годовой выручкой')
    else:
        if not isinstance(value,str) or not value.strip() or len(value)>question.get('max_length',500): raise ValueError('Заполните поле корректно')
        if kind=='inn' and (not value.isascii() or not value.isdigit() or len(value)!=(12 if answers.get('form')=='ip' else 10)): raise ValueError('ИНН должен содержать 12 цифр для ИП или 10 для ООО')
        if kind=='ogrn' and (not value.isascii() or not value.isdigit() or len(value)!=(15 if answers.get('form')=='ip' else 13)):raise ValueError('ОГРН должен содержать 13 цифр для ООО или 15 для ИП')
        if kind=='okved' and not re.fullmatch(r'\d{2}(?:\.\d{1,2}){0,2}',value):raise ValueError('Введите код ОКВЭД в формате 56.10')
        if kind=='date':
            try:registered=date.fromisoformat(value)
            except ValueError:raise ValueError('Введите дату регистрации')
            if registered>today() or registered<date(1900,1,1):raise ValueError('Дата регистрации должна быть в прошлом, не ранее 1900 года')
    return value.strip() if isinstance(value,str) else value
