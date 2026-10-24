"""Russian labels for the scorecard file: what the staff dossier shows next to each feature and bin."""
from __future__ import annotations

from apps.api.contracts import LegalForm

FEATURE_LABELS = {
    'inflow_trend_12m': 'Тренд поступлений за 12 мес.',
    'inflow_cv_12m': 'Волатильность поступлений',
    'months_without_inflows_12m': 'Месяцев без поступлений',
    'customers_count_12m': 'Число покупателей',
    'top_customer_share': 'Доля крупнейшего покупателя',
    'cash_withdrawal_share': 'Доля снятия наличных',
    'balance_days_of_expenses': 'Средний остаток, дней расходов',
    'tax_payment_months_12m': 'Месяцев с налоговыми платежами',
    'payroll_payment_months_12m': 'Месяцев с выплатой зарплаты',
    'worst_delinquency_days_24m_company': 'Худшая просрочка компании, дней',
    'worst_delinquency_days_24m_owner': 'Худшая просрочка собственника, дней',
    'bki_inquiries_3m': 'Запросы КИ за 3 мес.',
    'debt_to_revenue': 'Долг к выручке',
    'limit_utilization': 'Использование лимитов',
    'business_age_months': 'Возраст бизнеса, мес.',
    'okved_risk_group': 'Группа риска ОКВЭД',
    'legal_form': 'Правовая форма',
    'owner_or_director_changed_6m': 'Смена собственника или директора за 6 мес.',
    'margin': 'Маржинальность',
    'negative_equity': 'Отрицательный капитал',
}

# The full domain of every categorical feature: each value gets a bin, seen in training or not.
CATEGORIES: dict[str, list] = {
    'okved_risk_group': ['low', 'medium', 'high'],
    'legal_form': [f.value for f in LegalForm],
    'owner_or_director_changed_6m': [False, True],
    'negative_equity': [False, True],
}

INTEGER_FEATURES = frozenset({
    'months_without_inflows_12m', 'customers_count_12m', 'tax_payment_months_12m', 'payroll_payment_months_12m',
    'worst_delinquency_days_24m_company', 'worst_delinquency_days_24m_owner', 'bki_inquiries_3m',
    'business_age_months',
})

_CATEGORY_LABELS = {'low': 'низкий', 'medium': 'средний', 'high': 'высокий', 'ip': 'ИП', 'ooo': 'ООО',
                    False: 'нет', True: 'да'}

MISSING_LABEL = 'нет данных'


def _number(x: float) -> str:
    if float(x).is_integer():
        return f'{int(x):,}'.replace(',', ' ')
    return f'{x:g}'.replace('.', ',')


def numeric_bin_label(name: str, lower: float | None, upper: float | None) -> str:
    """[lower, upper): integer features show the last included value, the others the bound."""
    if lower is None and upper is None:
        return 'любое значение'
    if name in INTEGER_FEATURES:
        last = None if upper is None else upper - 1
        if lower is None:
            return f'{_number(last)} и меньше'
        if last is None:
            return f'{_number(lower)} и больше'
        return _number(lower) if last == lower else f'{_number(lower)}–{_number(last)}'
    if lower is None:
        return f'меньше {_number(upper)}'
    if upper is None:
        return f'{_number(lower)} и больше'
    return f'{_number(lower)}–{_number(upper)}'


def category_label(categories: list) -> str:
    return ', '.join(_CATEGORY_LABELS[c] for c in categories)
