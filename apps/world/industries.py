"""Industries of the synthetic world (SPEC §14).

Each industry has its own default-rate offset (added to the logit in T05), its own
monthly seasonality (used for turnovers in T02) and its own economics. These are
parameters of the synthetic world, not credit policy, so they live here and not in
config/.

SIMPLIFICATION: about a dozen industries with hand-set parameters stand in for the
full ОКВЭД tree and industry statistics a bank would calibrate on.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Industry:
    key: str
    name: str
    okved_main: str
    okved_extra: tuple[str, ...]
    weight: float  # share of companies in the world (normalised by the generator)
    ip_share: float  # share of ИП among companies in this industry
    revenue_median: int  # micro business, rubles per year
    revenue_per_employee: int  # rubles per year
    cogs_share: float  # cost of sales / revenue
    assets_to_revenue: float  # total assets / revenue
    default_offset: float  # added to the default logit in T05
    seasonality: tuple[float, ...]  # Jan..Dec multipliers, mean 1
    name_words: tuple[str, ...]  # for ООО names


def _season(*values: float) -> tuple[float, ...]:
    assert len(values) == 12
    mean = sum(values) / 12
    return tuple(round(v / mean, 4) for v in values)


FLAT = _season(*[1.0] * 12)

INDUSTRIES: tuple[Industry, ...] = (
    Industry('bakery', 'Производство хлеба и выпечки', '10.71', ('47.24', '56.10', '46.36'),
             6, 0.35, 14_000_000, 1_100_000, 0.47, 0.38, 0.0,
             _season(0.95, 0.9, 0.95, 1.0, 1.0, 0.95, 0.9, 0.95, 1.05, 1.05, 1.05, 1.25),
             ('Зёрна', 'Колос', 'Каравай', 'Пекарь', 'Хлебный двор')),
    Industry('retail_food', 'Розничная торговля продуктами', '47.11', ('47.25', '47.29', '46.39'),
             14, 0.6, 12_000_000, 2_400_000, 0.74, 0.3, 0.1,
             _season(0.9, 0.85, 0.95, 0.95, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.05, 1.3),
             ('Продмаг', 'Семейный', 'У дома', 'Свежесть', 'Корзина')),
    Industry('wholesale', 'Оптовая торговля', '46.90', ('46.49', '46.73', '52.10'),
             12, 0.25, 30_000_000, 6_000_000, 0.82, 0.45, 0.0,
             _season(0.8, 0.9, 1.0, 1.05, 1.05, 1.0, 0.95, 1.0, 1.05, 1.05, 1.05, 1.1),
             ('Опт-Снаб', 'Торговый дом', 'Профресурс', 'Логистик', 'Поставка')),
    Industry('construction', 'Строительство зданий', '41.20', ('43.21', '43.31', '43.99'),
             9, 0.3, 22_000_000, 2_200_000, 0.72, 0.6, 0.4,
             _season(0.55, 0.6, 0.8, 1.0, 1.15, 1.25, 1.3, 1.3, 1.2, 1.05, 0.95, 0.85),
             ('Стройград', 'Монолит', 'Фундамент', 'Кровля', 'Новый дом')),
    Industry('transport', 'Грузовые перевозки', '49.41', ('52.29', '49.42'),
             9, 0.65, 9_000_000, 2_000_000, 0.65, 0.7, 0.3,
             _season(0.85, 0.85, 0.95, 1.0, 1.05, 1.05, 1.05, 1.05, 1.05, 1.05, 1.0, 1.05),
             ('Трансавто', 'Грузовик', 'Дальний путь', 'Перевозчик', 'Магистраль')),
    Industry('restaurants', 'Рестораны и кафе', '56.10', ('56.30', '56.21'),
             8, 0.45, 10_000_000, 1_000_000, 0.4, 0.35, 0.5,
             _season(0.85, 0.85, 0.95, 0.95, 1.05, 1.1, 1.15, 1.15, 1.0, 0.95, 0.9, 1.1),
             ('Вкусно', 'Самовар', 'Трапеза', 'Обед', 'Уют')),
    Industry('beauty', 'Парикмахерские и салоны красоты', '96.02', ('96.04', '47.75'),
             7, 0.85, 3_000_000, 700_000, 0.25, 0.25, 0.2,
             _season(0.85, 0.95, 1.05, 1.0, 1.05, 1.0, 0.95, 0.95, 1.0, 1.0, 1.0, 1.2),
             ('Локон', 'Стиль', 'Шарм', 'Образ', 'Мастер')),
    Industry('it', 'Разработка программного обеспечения', '62.01', ('62.02', '63.11'),
             8, 0.55, 8_000_000, 2_500_000, 0.2, 0.3, -0.2,
             _season(0.8, 0.95, 1.0, 1.0, 1.0, 1.0, 0.9, 0.9, 1.0, 1.05, 1.05, 1.35),
             ('Код', 'Цифра', 'Софтлаб', 'Алгоритм', 'Интеграция')),
    Industry('agriculture', 'Выращивание зерновых культур', '01.11', ('01.13', '01.61'),
             5, 0.45, 15_000_000, 1_500_000, 0.6, 1.1, 0.2,
             _season(0.35, 0.35, 0.5, 0.7, 0.8, 0.9, 1.3, 1.9, 2.0, 1.6, 0.9, 0.7),
             ('Нива', 'Агро', 'Поле', 'Урожай', 'Колосья')),
    Industry('auto_repair', 'Ремонт автотранспорта', '45.20', ('45.32', '45.31'),
             8, 0.7, 6_000_000, 1_200_000, 0.5, 0.4, 0.1,
             _season(0.85, 0.85, 0.95, 1.15, 1.1, 1.0, 1.0, 1.0, 0.95, 1.1, 1.15, 0.9),
             ('Автомастер', 'Мотор', 'Гараж', 'Сервис', 'Колесо')),
    Industry('manufacturing', 'Обработка металлических изделий', '25.62', ('25.11', '33.12'),
             6, 0.2, 25_000_000, 1_800_000, 0.65, 0.8, -0.1,
             FLAT,
             ('Металлист', 'Станкопром', 'Деталь', 'Литейщик', 'Профиль')),
    Industry('tourism', 'Туристические агентства', '79.11', ('79.12', '55.10'),
             4, 0.5, 7_000_000, 1_500_000, 0.75, 0.25, 0.4,
             _season(0.7, 0.75, 0.85, 0.95, 1.2, 1.4, 1.45, 1.35, 1.0, 0.8, 0.7, 0.85),
             ('Путешествие', 'Горизонт', 'Маршрут', 'Глобус', 'Отпуск')),
)

BY_KEY = {i.key: i for i in INDUSTRIES}
