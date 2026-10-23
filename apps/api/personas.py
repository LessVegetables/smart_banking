from copy import deepcopy
import calendar
from datetime import date
from .catalog import today

BASE={'purpose':'working','goal':'Закупить сырьё и упаковку для двух кофеен','urgency':'month','amount':1000000,'term':12,'name':'Кофейня «Зёрна»','form':'ooo','inn':'7700000000','age':36,'industry':'food','region':'Новосибирск','employees':12,'revenue':12000000,'trend':'stable','seasonal':'no','expenses':600000,'alfa':'yes','has_debt':'no','credit_history':'yes','has_overdue':'no','flexible':'both','documents_ready':'all'}
PERSONAS=[
 {'id':'healthy','title':'Здоровый бизнес','subtitle':'Одобрение · кофейня «Зёрна»','answers':{}},
 {'id':'medium','title':'Рост с нагрузкой','subtitle':'Контрпредложение · меньше сумма','answers':{'amount':2500000,'age':10,'alfa':'no','has_debt':'yes','debt':3000000,'payments':90000}},
 {'id':'equipment','title':'Новое оборудование','subtitle':'Предложение с залогом','answers':{'purpose':'equipment','amount':3000000,'term':36,'age':10,'alfa':'no','has_debt':'yes','debt':3000000,'payments':90000,'equipment_name':'Профессиональная кофемашина и печь','equipment_cost':5000000,'down_payment':500000,'collateral':'yes','collateral_value':5000000,'invoice':'yes'}},
 {'id':'documents','title':'Уточнение финансов','subtitle':'Документы → новое решение','answers':{'amount':4000000,'age':8,'alfa':'no','trend':'falling','has_debt':'yes','debt':5000000,'payments':100000}},
 {'id':'manual','title':'Сложный профиль','subtitle':'Документы → андеррайтер','answers':{'amount':4000000,'age':8,'alfa':'no','trend':'falling','has_debt':'yes','debt':5000000,'payments':100000}},
 {'id':'alternative','title':'Подходящий формат','subtitle':'Линия → оборотный кредит','answers':{'purpose':'line','amount':300000,'revenue':2400000,'has_debt':'yes','debt':300000,'payments':40000,'gap':300000,'receivable_days':30,'utilization':70}},
 {'id':'stop','title':'Стоп-фактор','subtitle':'Отказ с объяснением','answers':{'has_overdue':'yes','overdue_days':95,'overdue_months':3,'overdue_amount':50000,'overdue_payment':80000,'overdue_current':'no','episodes':1}},
]

def persona_answers(id):
    p=next((p for p in PERSONAS if p['id']==id),None)
    if not p:raise ValueError('Неизвестный сценарий')
    a=deepcopy(BASE);a.update(p['answers'])
    current=today();month=current.year*12+current.month-1-a['age'];year,month=divmod(month,12);month+=1
    a.update(registered_at=date(year,month,min(current.day,calendar.monthrange(year,month)[1])).isoformat(),ogrn='1000000000000',director='Иванов Алексей',okved='56.10')
    return a
