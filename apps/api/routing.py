"""Routing by the authority matrix (T18, SPEC §11): who decides an application of a given amount.

    committee    amount over 15M, or grade D or worse with an amount over 5M
    underwriter  grade D or worse, any flag, THIN data, PARTIAL data over 1M, amount over 5M up to 15M,
                 a required source unavailable after all retries, no score, or a client's statement (T23)
    auto         grade A–C, no flags, FULL data up to 5M or PARTIAL data up to 1M; and every stop factor:
                 the decline is automatic

Every condition that applies gives its own reason, so staff see all of them; the route is the strictest.
`route_for` closes over grade, flags and tier and gives the `amount -> Route` that pricing needs
(`generate_offers`, offers.py): the processing cost depends on the route. See ROUTING.md.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from apps.api.contracts import DataQuality, DataQualityTier, Grade, Reason, Route, SourceId
from apps.api.offers import RouteFor, rub

CLIENT_AUTO = 'Решение принято автоматически.'
CLIENT_UNDERWRITER = 'Заявку проверит специалист банка — обычно в течение рабочего дня.'
CLIENT_COMMITTEE = 'Заявку рассмотрит кредитный комитет банка — обычно до 2 рабочих дней.'
CLIENT_TEXT = {Route.AUTO: CLIENT_AUTO, Route.UNDERWRITER: CLIENT_UNDERWRITER, Route.COMMITTEE: CLIENT_COMMITTEE}


# SIMPLIFICATION: the authority matrix lives here until it moves to config/credit_policy.json (shared-folder
# PR, docs/v2/NOTES.md, T18 → Unassigned). The values are SPEC §11; grade E is routed like D (§11 names
# only A–C and D).
@dataclass(frozen=True)
class RoutingPolicy:
    auto_grades: frozenset[Grade] = field(default_factory=lambda: frozenset({Grade.A, Grade.B, Grade.C}))
    auto_max_full: int = 5_000_000  # also the top of the underwriter's own band for grades A–C
    auto_max_partial: int = 1_000_000
    underwriter_max: int = 15_000_000
    grade_committee_above: int = 5_000_000  # grades outside `auto_grades` above this go to the committee


DEFAULT_POLICY = RoutingPolicy()


@dataclass(frozen=True)
class Routing:
    route: Route
    reasons: tuple[Reason, ...]  # `Decision.route_reasons`; each one's route is in `routes`
    routes: tuple[Route, ...]  # the route each reason asks for, same order


def _strictest(routes: Iterable[Route]) -> Route:
    return max(routes, key=list(Route).index)


def millions(amount: int) -> str:
    return f'{amount / 1_000_000:g} млн ₽'.replace('.', ',')


def route_application(amount: int, *, grade: Grade | None, flags: Iterable[Reason] = (),
                      data_quality: DataQuality | None, stop_factors: Iterable[Reason] = (),
                      unavailable_sources: Iterable[SourceId] = (), statements: bool = False,
                      policy: RoutingPolicy = DEFAULT_POLICY) -> Routing:
    """Route an application or an offer of `amount`.

    `grade` is the champion's (`Scoring.score.grade`, never the challenger's); None when scoring refused
    the features (`FeatureSetMismatch`). `flags` and `stop_factors` come from `evaluate_stop_factors`,
    `data_quality` from the same call as the limit, `unavailable_sources` is `Enrichment.unavailable_required`.
    `statements`: the data quality and the limit count statements from other banks (T23, statements.py).
    """
    flags, unavailable = tuple(flags), tuple(unavailable_sources)
    if tuple(stop_factors):
        return Routing(Route.AUTO, (Reason(code='route_stop_factor', staff_text='Стоп-фактор: автоматический отказ',
                                           client_text=CLIENT_AUTO),), (Route.AUTO,))
    if unavailable:
        names = ', '.join(s.value for s in unavailable)
        return Routing(Route.UNDERWRITER, (Reason(
            code='route_source_unavailable', staff_text=f'Обязательный источник недоступен после всех повторов: {names}',
            client_text=CLIENT_UNDERWRITER),), (Route.UNDERWRITER,))

    found: list[tuple[Route, str, str, str | None]] = []  # route, code, staff text, client text if not the route's

    def add(route: Route, code: str, staff: str, client: str | None = None):
        found.append((route, code, staff, client))

    if amount > policy.underwriter_max:
        add(Route.COMMITTEE, 'route_amount_over_15m',
            f'Сумма {rub(amount)} больше {millions(policy.underwriter_max)}: решение комитета')
    elif amount > policy.auto_max_full:
        add(Route.UNDERWRITER, 'route_amount_over_5m',
            f'Сумма {rub(amount)} больше {millions(policy.auto_max_full)}: выше полномочий автоматического решения')

    if grade is None:
        add(Route.UNDERWRITER, 'route_score_unavailable', 'Скоринг не рассчитан: модель не подходит к признакам')
    elif grade not in policy.auto_grades:
        if amount > policy.grade_committee_above:
            add(Route.COMMITTEE, 'route_grade_over_5m',
                f'Грейд {grade} и сумма {rub(amount)} больше {millions(policy.grade_committee_above)}')
        else:
            add(Route.UNDERWRITER, 'route_grade', f'Грейд {grade}: автоматически решаются только грейды A–C')

    for flag in flags:
        # The flag's own client text stays in `Decision.flags`; the client only learns that staff will review.
        add(Route.UNDERWRITER, f'route_flag_{flag.code}', f'Флаг: {flag.staff_text}')

    tier = data_quality.tier if data_quality else None
    if data_quality is None:
        add(Route.UNDERWRITER, 'route_data_quality_missing', 'Качество данных по счёту не рассчитано')
    elif tier == DataQualityTier.THIN:
        add(Route.UNDERWRITER, 'route_data_thin', 'Данные по счёту THIN: лимит по денежному потоку не считается',
            data_quality.client_message)
    elif tier == DataQualityTier.PARTIAL and amount > policy.auto_max_partial:
        add(Route.UNDERWRITER, 'route_data_partial_over_1m',
            f'Данные по счёту PARTIAL и сумма {rub(amount)} больше {millions(policy.auto_max_partial)}',
            data_quality.client_message)

    if statements:
        # A client document is less reliable than our own data (SPEC §5.1): a person checks it.
        add(Route.UNDERWRITER, 'route_statement_upload',
            'Лимит и качество данных учитывают выписки других банков: документ клиента проверяет андеррайтер')

    if not found:
        limit = policy.auto_max_full if tier == DataQualityTier.FULL else policy.auto_max_partial
        return Routing(Route.AUTO, (Reason(
            code='route_auto', staff_text=f'Грейд {grade}, флагов нет, данные {tier}, сумма до {millions(limit)}',
            client_text=CLIENT_AUTO),), (Route.AUTO,))
    reasons = tuple(Reason(code=code, staff_text=staff, client_text=client or CLIENT_TEXT[route])
                    for route, code, staff, client in found)
    routes = tuple(route for route, *_ in found)
    return Routing(_strictest(routes), reasons, routes)


def route_for(grade: Grade | None, flags: Iterable[Reason], data_quality: DataQuality | None,
              policy: RoutingPolicy = DEFAULT_POLICY, *, statements: bool = False) -> RouteFor:
    """The route of any amount for one scored application: what `generate_offers` takes as `route_for`."""
    flags = tuple(flags)
    return lambda amount: route_application(amount, grade=grade, flags=flags, data_quality=data_quality,
                                            statements=statements, policy=policy).route
