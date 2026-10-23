"""Data contracts shared by the three v2 tracks (docs/v2/CONTRACTS.md, SPEC v2).

This module is the single source of truth for the data exchanged between the
mock sources, source adapters, feature extraction, the decision engine and the
interfaces. Every model has an example in tests/fixtures/. Changing this file
needs a separate PR approved by all three track owners.

Conventions:
- money is whole rubles (int);
- rates, shares, PD and LGD are fractions (0.209 means 20.9%);
- dates and timestamps are ISO 8601; months are "YYYY-MM";
- None means "no data", never zero (SPEC §5.1).

Thresholds (tier bounds, DSCR minimums, LGD table, rate cap) are policy and live
in config/, not here. Contracts only carry the values computed from them.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

CONTRACT_VERSION = '1.0'

Rub = int
NonNegativeRub = Annotated[int, Field(ge=0)]
Share = Annotated[float, Field(ge=0, le=1)]
Month = Annotated[str, Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')]
Inn = Annotated[str, Field(pattern=r'^\d{10}(\d{2})?$')]
PersonInn = Annotated[str, Field(pattern=r'^\d{12}$')]
Okved = Annotated[str, Field(pattern=r'^\d{2}(\.\d{1,2}){0,2}$')]
FactValue = Union[bool, int, float, str]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


# --- Shared enums ---------------------------------------------------------------

class LegalForm(StrEnum):
    IP = 'ip'
    OOO = 'ooo'


class SourceId(StrEnum):
    ACCOUNTS = 'accounts'
    CLIENT_CARD = 'client_card'
    EGRUL = 'egrul'  # ЕГРЮЛ for ООО and ЕГРИП for ИП: one registry adapter
    FNS = 'fns'
    BKI = 'bki'
    FSSP = 'fssp'
    ARBITRATION = 'arbitration'  # арбитраж and Федресурс
    AML = 'aml'  # 115-ФЗ / ПОД/ФТ
    STATEMENT_UPLOAD = 'statement_upload'  # client document (T23): yields facts, no SourceResponse


class TrustLevel(StrEnum):
    """Source reliability, most reliable first (SPEC §5.1)."""
    BANK = 'bank'
    REGISTRY = 'registry'  # state sources and the credit bureau
    CLIENT_DOCUMENT = 'client_document'
    DECLARED = 'declared'

    @property
    def rank(self) -> int:
        return list(TrustLevel).index(self)


class SourceCallStatus(StrEnum):
    OK = 'ok'
    TIMEOUT = 'timeout'
    ERROR = 'error'


class DataQualityTier(StrEnum):
    FULL = 'FULL'
    PARTIAL = 'PARTIAL'
    THIN = 'THIN'

    @property
    def rank(self) -> int:
        return list(DataQualityTier).index(self)


class Grade(StrEnum):
    A = 'A'
    B = 'B'
    C = 'C'
    D = 'D'
    E = 'E'


class Route(StrEnum):
    AUTO = 'auto'
    UNDERWRITER = 'underwriter'
    COMMITTEE = 'committee'


class ApplicationStatus(StrEnum):
    """Application statuses (SPEC §13)."""
    DRAFT = 'draft'
    SUBMITTED = 'submitted'
    ENRICHING = 'enriching'
    NEEDS_DATA = 'needs_data'
    SCORING = 'scoring'
    IN_REVIEW = 'in_review'
    OFFERED = 'offered'
    DECLINED = 'declined'
    ACCEPTED = 'accepted'
    DOCUMENTS = 'documents'
    SIGNED = 'signed'
    CONDITIONS = 'conditions'
    DISBURSED = 'disbursed'
    WITHDRAWN = 'withdrawn'
    EXPIRED = 'expired'


DECISION_STATUSES = frozenset({
    ApplicationStatus.OFFERED, ApplicationStatus.DECLINED,
    ApplicationStatus.IN_REVIEW, ApplicationStatus.NEEDS_DATA,
})


class ProductId(StrEnum):
    OVERDRAFT = 'overdraft'
    WORKING_CAPITAL = 'working_capital'
    INVESTMENT = 'investment'
    CREDIT_LINE = 'credit_line'


class CollateralType(StrEnum):
    OWNER_GUARANTEE = 'owner_guarantee'
    EQUIPMENT_PLEDGE = 'equipment_pledge'
    MSP_GUARANTEE = 'msp_guarantee'  # MOCK: Корпорация МСП guarantee


class Purpose(StrEnum):
    INVENTORY = 'inventory'
    PAYROLL_RENT = 'payroll_rent'
    EQUIPMENT = 'equipment'
    CASH_GAP = 'cash_gap'
    SEASONAL_GAP = 'seasonal_gap'
    CONTRACT = 'contract'
    OTHER = 'other'


class NeededBy(StrEnum):
    DAYS = 'days'
    WEEK = 'week'
    MONTH = 'month'
    LATER = 'later'


# --- Application request (client → workflow) -------------------------------------

class Consent(Contract):
    kind: Literal['bki_company', 'bki_owner', 'personal_data']  # 218-ФЗ, 152-ФЗ
    subject_inn: Inn
    given_at: datetime


class EquipmentInfo(Contract):
    description: str
    price: NonNegativeRub
    down_payment: NonNegativeRub
    supplier_inn: Inn


class ApplicationRequest(Contract):
    """What only the client knows: the need, declared values and consents."""
    application_id: str
    inn: Inn
    legal_form: LegalForm
    purpose: Purpose
    amount: Annotated[int, Field(gt=0)]
    term_months: Annotated[int, Field(ge=1, le=120)]
    needed_by: NeededBy
    preferred_product: ProductId | None
    declared_annual_revenue: NonNegativeRub | None
    equipment: EquipmentInfo | None
    consents: list[Consent]
    submitted_at: datetime | None


# --- Source payloads (mock service → adapters) -----------------------------------
# SIMPLIFICATION: payloads keep only the fields the conveyor needs (SPEC §5); real
# ЕГРЮЛ, ФНС and БКИ responses are much wider.

class Person(Contract):
    full_name: str
    inn: PersonInn
    since: date | None


class Founder(Person):
    share: Share


class EgrulPayload(Contract):
    """ЕГРЮЛ (ООО) or ЕГРИП (ИП). For ИП the director is the entrepreneur and founders is empty."""
    inn: Inn
    ogrn: Annotated[str, Field(pattern=r'^\d{13}(\d{2})?$')]
    legal_form: LegalForm
    name: str
    status: Literal['active', 'liquidating', 'reorganizing', 'liquidated']
    registered_at: date
    okved_main: Okved
    okved_extra: list[Okved]
    region: str
    address: str
    director: Person
    founders: list[Founder]


class GirBoReport(Contract):
    """Annual financial statements from ГИР БО (ООО)."""
    year: int
    revenue: NonNegativeRub
    cost_of_sales: NonNegativeRub
    net_profit: Rub
    total_assets: NonNegativeRub
    equity: Rub
    liabilities: NonNegativeRub


class TaxReturn(Contract):
    """Annual tax return income (ИП)."""
    year: int
    regime: Literal['usn_income', 'usn_income_expense', 'patent', 'osno']
    income: NonNegativeRub


class FnsPayload(Contract):
    tax_debt: NonNegativeRub
    accounts_suspended: bool
    mass_address: bool
    director_disqualified: bool
    employees: Annotated[int, Field(ge=0)] | None
    financials: GirBoReport | None
    tax_return: TaxReturn | None


class Delinquency(Contract):
    started_at: date
    days: Annotated[int, Field(ge=1)]
    amount: NonNegativeRub
    payment_due: NonNegativeRub  # scheduled payment, for the "< 1% of payment" rule


class BkiLoan(Contract):
    loan_id: str
    kind: Literal['loan', 'credit_line', 'overdraft', 'card', 'leasing', 'guarantee']
    opened_at: date
    closed_at: date | None
    amount: NonNegativeRub  # original amount, or the limit for revolving products
    balance: NonNegativeRub
    monthly_payment: NonNegativeRub
    current_overdue_days: Annotated[int, Field(ge=0)]
    current_overdue_amount: NonNegativeRub
    delinquencies: list[Delinquency]


class BkiInquiry(Contract):
    inquired_at: date
    lender_type: Literal['bank', 'mfo', 'leasing', 'other']


class BkiReport(Contract):
    subject: Literal['company', 'owner']
    subject_inn: Inn
    loans: list[BkiLoan]
    inquiries: list[BkiInquiry]


class BkiPayload(Contract):
    """ООО: company report and the main owner's report. ИП: owner report only (SPEC §3)."""
    reports: list[BkiReport]


class FsspProceeding(Contract):
    number: str
    subject: Literal['company', 'owner']
    opened_at: date
    kind: str
    amount: NonNegativeRub
    remaining: NonNegativeRub


class FsspPayload(Contract):
    proceedings: list[FsspProceeding]


class ArbitrationCase(Contract):
    number: str
    role: Literal['defendant', 'plaintiff', 'third_party']
    filed_at: date
    claim_amount: NonNegativeRub
    status: Literal['pending', 'decided', 'dismissed']


class BankruptcyNotice(Contract):
    published_at: date
    kind: Literal['creditor_intent', 'filing', 'proceedings_opened', 'other']


class ArbitrationPayload(Contract):
    cases: list[ArbitrationCase]
    bankruptcy_notices: list[BankruptcyNotice]  # Федресурс


class AmlListMatch(Contract):
    list_name: str
    subject: Literal['company', 'director', 'founder']
    matched_name: str


class AmlPayload(Contract):
    zsk_zone: Literal['green', 'yellow', 'red']  # MOCK: ЦБ «Знай своего клиента» risk zone
    list_matches: list[AmlListMatch]
    transit_operations: bool


class Inflows(Contract):
    customers: NonNegativeRub
    own_transfers: NonNegativeRub
    loans_received: NonNegativeRub
    founder_contributions: NonNegativeRub
    other: NonNegativeRub

    @property
    def total(self) -> int:
        return self.customers + self.own_transfers + self.loans_received + self.founder_contributions + self.other


class Outflows(Contract):
    suppliers: NonNegativeRub
    payroll: NonNegativeRub
    taxes: NonNegativeRub
    rent: NonNegativeRub
    loan_payments: NonNegativeRub
    cash_withdrawals: NonNegativeRub
    own_transfers: NonNegativeRub
    other: NonNegativeRub

    @property
    def total(self) -> int:
        return (self.suppliers + self.payroll + self.taxes + self.rent + self.loan_payments
                + self.cash_withdrawals + self.own_transfers + self.other)


class MonthlyTurnover(Contract):
    month: Month
    inflows: Inflows
    outflows: Outflows
    average_balance: Rub


class CounterpartySummary(Contract):
    """Last 12 months."""
    customers_count: Annotated[int, Field(ge=0)]
    suppliers_count: Annotated[int, Field(ge=0)]
    top_customer_share: Share | None  # None when there are no customer inflows


class AccountsPayload(Contract):
    """Internal account turnovers, up to 24 full months, oldest first."""
    account_opened_at: date
    months: Annotated[list[MonthlyTurnover], Field(max_length=24)]
    counterparties_12m: CounterpartySummary

    @model_validator(mode='after')
    def _months_ordered(self):
        names = [m.month for m in self.months]
        if names != sorted(set(names)):
            raise ValueError('months must be unique and ordered oldest first')
        return self


class BankProduct(Contract):
    kind: Literal['account', 'deposit', 'card', 'loan', 'overdraft', 'credit_line', 'guarantee']
    opened_at: date
    amount: NonNegativeRub | None
    balance: NonNegativeRub | None
    monthly_payment: NonNegativeRub | None


class RelatedCompany(Contract):
    inn: Inn
    name: str
    relation: Literal['same_owner', 'same_director', 'group']


class ClientCardPayload(Contract):
    client_since: date
    products: list[BankProduct]
    internal_stop_list: bool
    related_companies: list[RelatedCompany]


class _SourceResponse(Contract):
    """Adapter envelope around one source call, shown on the dossier sources panel."""
    inn: Inn
    status: SourceCallStatus
    requested_at: datetime
    received_at: datetime | None
    latency_ms: Annotated[int, Field(ge=0)]
    attempts: Annotated[int, Field(ge=1)]
    error: str | None

    @model_validator(mode='after')
    def _payload_matches_status(self):
        ok = self.status == SourceCallStatus.OK
        if ok != (self.payload is not None):
            raise ValueError('payload is required when status is ok and must be null otherwise')
        if not ok and not self.error:
            raise ValueError('error is required when status is not ok')
        return self


class AccountsResponse(_SourceResponse):
    source: Literal[SourceId.ACCOUNTS]
    payload: AccountsPayload | None


class ClientCardResponse(_SourceResponse):
    source: Literal[SourceId.CLIENT_CARD]
    payload: ClientCardPayload | None


class EgrulResponse(_SourceResponse):
    source: Literal[SourceId.EGRUL]
    payload: EgrulPayload | None


class FnsResponse(_SourceResponse):
    source: Literal[SourceId.FNS]
    payload: FnsPayload | None


class BkiResponse(_SourceResponse):
    source: Literal[SourceId.BKI]
    payload: BkiPayload | None


class FsspResponse(_SourceResponse):
    source: Literal[SourceId.FSSP]
    payload: FsspPayload | None


class ArbitrationResponse(_SourceResponse):
    source: Literal[SourceId.ARBITRATION]
    payload: ArbitrationPayload | None


class AmlResponse(_SourceResponse):
    source: Literal[SourceId.AML]
    payload: AmlPayload | None


SourceResponse = Annotated[
    Union[AccountsResponse, ClientCardResponse, EgrulResponse, FnsResponse,
          BkiResponse, FsspResponse, ArbitrationResponse, AmlResponse],
    Field(discriminator='source'),
]
SOURCE_RESPONSE = TypeAdapter(SourceResponse)


# --- Facts (adapters → engine) ---------------------------------------------------

class Fact(Contract):
    """One fact with its declared and verified versions (SPEC §2, principle 2).

    One Fact per (name, source). When sources conflict the engine uses the more
    reliable one; the discrepancy itself is a flag (SPEC §7).
    """
    name: str
    declared_value: FactValue | None
    verified_value: FactValue | None
    source: SourceId | None  # where verified_value came from; None when only declared
    trust_level: TrustLevel  # of the value the engine uses
    fetched_at: datetime | None
    discrepancy: float | None = None  # |declared - verified| / |verified|, filled in when both are numeric

    @model_validator(mode='after')
    def _consistent(self):
        if self.verified_value is None:
            if self.source is not None or self.trust_level != TrustLevel.DECLARED:
                raise ValueError('a fact without a verified value has no source and is declared')
        elif self.source is None or self.fetched_at is None:
            raise ValueError('a verified value needs a source and fetched_at')
        if self.discrepancy is None and _numeric(self.declared_value) and _numeric(self.verified_value) and self.verified_value:
            self.discrepancy = abs(self.declared_value - self.verified_value) / abs(self.verified_value)
        return self

    @property
    def value(self) -> FactValue | None:
        return self.verified_value if self.verified_value is not None else self.declared_value


def _numeric(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# --- Data quality (features → engine, dossier) -----------------------------------

class DataQualityMetric(Contract):
    value: float | None  # None when it cannot be computed
    tier: DataQualityTier


class DataQuality(Contract):
    """Account data quality (SPEC §5.1). The overall tier is the worst of the four."""
    history_months: DataQualityMetric
    revenue_coverage: DataQualityMetric
    operating_inflow_share: DataQualityMetric
    regularity_months: DataQualityMetric
    revenue_basis: Literal['fns', 'declared'] | None  # denominator of revenue_coverage
    tier: DataQualityTier
    client_message: str  # plain-language summary for the client
    policy_version: str

    @model_validator(mode='after')
    def _worst_of_four(self):
        metrics = (self.history_months, self.revenue_coverage, self.operating_inflow_share, self.regularity_months)
        worst = max((m.tier for m in metrics), key=lambda t: t.rank)
        if self.tier != worst:
            raise ValueError(f'tier must be the worst metric tier ({worst})')
        return self


# --- Features (features → scoring) -----------------------------------------------

class Features(Contract):
    """Scorecard features (SPEC §8). Every field is required; null means "no data" and
    goes to the scorecard's missing bin. The requested amount is deliberately absent."""
    feature_set_version: str
    # account behaviour, last 12 months
    inflow_trend_12m: float | None  # operating inflows, last 6 months / previous 6 months - 1
    inflow_cv_12m: float | None  # coefficient of variation of monthly operating inflows
    months_without_inflows_12m: int | None
    customers_count_12m: int | None
    top_customer_share: float | None
    cash_withdrawal_share: float | None  # of all outflows
    balance_days_of_expenses: float | None
    tax_payment_months_12m: int | None
    payroll_payment_months_12m: int | None
    # credit history (after the overdue rules, SPEC §5)
    worst_delinquency_days_24m_company: int | None  # None for ИП
    worst_delinquency_days_24m_owner: int | None
    bki_inquiries_3m: int | None
    debt_to_revenue: float | None
    limit_utilization: float | None
    # registries
    business_age_months: int | None
    okved_risk_group: Literal['low', 'medium', 'high'] | None
    legal_form: LegalForm | None
    owner_or_director_changed_6m: bool | None
    # financials
    margin: float | None
    negative_equity: bool | None  # ООО only


FEATURE_NAMES = frozenset(Features.model_fields) - {'feature_set_version'}


# --- Scorecard file (training → scoring) -----------------------------------------

class ScorecardBin(Contract):
    """Numeric bins are [lower, upper); a None bound is open. Categorical bins list values."""
    label: str
    lower: float | None = None
    upper: float | None = None
    categories: list[Union[bool, str]] | None = None
    missing: bool = False
    woe: float | None
    points: float


class ScorecardFeature(Contract):
    name: str
    label: str
    kind: Literal['numeric', 'categorical']
    bins: list[ScorecardBin]

    @model_validator(mode='after')
    def _bins(self):
        if self.name not in FEATURE_NAMES:
            raise ValueError(f'unknown feature {self.name!r}')
        if sum(b.missing for b in self.bins) != 1:
            raise ValueError(f'{self.name}: exactly one missing bin is required')
        for b in self.bins:
            if b.missing:
                continue
            if self.kind == 'numeric' and b.categories is not None:
                raise ValueError(f'{self.name}: numeric bins have no categories')
            if self.kind == 'categorical' and (not b.categories or b.lower is not None or b.upper is not None):
                raise ValueError(f'{self.name}: categorical bins need categories and no bounds')
        return self


class ScorecardScaling(Contract):
    """points = base_points + pdo / ln 2 * ln(odds_good / base_odds) (SPEC §8)."""
    base_points: float = 600
    base_odds: float = 50
    pdo: float = 20


class GradeBound(Contract):
    grade: Grade
    pd_max: float | None  # exclusive upper bound; None for the last grade


class Scorecard(Contract):
    """Format of scorecard_vN.json. Total points = intercept_points + one bin per feature."""
    version: str
    origin: Literal['hand_set', 'trained']
    created_at: date
    description: str
    scaling: ScorecardScaling
    intercept_points: float
    features: list[ScorecardFeature]
    grades: list[GradeBound]

    @model_validator(mode='after')
    def _grades(self):
        if [g.grade for g in self.grades] != list(Grade):
            raise ValueError('grades must list A..E in order')
        bounds = [g.pd_max for g in self.grades]
        if bounds[-1] is not None or None in bounds[:-1] or bounds[:-1] != sorted(bounds[:-1]):
            raise ValueError('pd_max must increase and be null only for the last grade')
        return self

    def pd_from_points(self, points: float) -> float:
        s = self.scaling
        odds_good = s.base_odds * 2 ** ((points - s.base_points) / s.pdo)
        return 1 / (1 + odds_good)

    def grade_for_pd(self, pd: float) -> Grade:
        return next(g.grade for g in self.grades if g.pd_max is None or pd < g.pd_max)


# --- Decision (engine → interfaces) ----------------------------------------------

class Reason(Contract):
    code: str
    staff_text: str
    client_text: str  # for AML reasons a generic wording that discloses nothing (SPEC §1.11)


class ScoreContribution(Contract):
    feature: str
    label: str
    value: FactValue | None
    bin_label: str
    points: float


class ScoreResult(Contract):
    pd: Share
    points: float
    grade: Grade
    contributions: list[ScoreContribution]
    model_version: str


class ChallengerResult(Contract):
    """Shadow model: logged only, never affects the decision."""
    pd: Share
    points: float
    grade: Grade
    model_version: str


class LimitConstraint(Contract):
    """One term of the limit minimum (SPEC §9.1) with its named inputs."""
    name: Literal['cash_flow', 'leverage', 'collateral', 'turnover', 'product_max', 'requested']
    value: NonNegativeRub | None  # None when the constraint does not apply (e.g. cash_flow for THIN)
    inputs: dict[str, FactValue | None]
    note: str | None


class LimitBreakdown(Contract):
    constraints: list[LimitConstraint]
    binding: str
    limit: NonNegativeRub

    @model_validator(mode='after')
    def _minimum(self):
        applicable = {c.name: c.value for c in self.constraints if c.value is not None}
        if not applicable:
            raise ValueError('at least one constraint must apply')
        if self.limit != min(applicable.values()):
            raise ValueError('limit must be the minimum of the applicable constraints')
        if applicable.get(self.binding) != self.limit:
            raise ValueError('binding must name a constraint equal to the limit')
        return self


class PriceBreakdown(Contract):
    """Risk-based rate (SPEC §9.2); every component is an annual fraction."""
    funding: float
    expected_loss: float
    processing: float
    capital: float
    margin: float
    rate: float
    pd: Share
    lgd: Share
    processing_cost: NonNegativeRub
    average_balance: NonNegativeRub

    @model_validator(mode='after')
    def _sum(self):
        if not math.isclose(self.rate, self.funding + self.expected_loss + self.processing + self.capital + self.margin, abs_tol=1e-4):
            raise ValueError('rate must equal the sum of its components')
        return self


class Offer(Contract):
    offer_id: str
    kind: Literal['as_requested', 'longer_term', 'max_amount', 'with_collateral', 'other_product']
    product: ProductId
    amount: Annotated[int, Field(gt=0)]
    term_months: Annotated[int, Field(ge=1)]
    rate: float
    monthly_payment: NonNegativeRub
    collateral: list[CollateralType]
    guarantee_fee: float  # annual fraction, MSP guarantee only; 0 otherwise
    disbursement: Literal['client_account', 'supplier']
    matches_request: bool
    tradeoff: str  # client-facing
    limit: LimitBreakdown
    price: PriceBreakdown
    expected_profit: Rub | None  # staff only
    valid_until: date

    @model_validator(mode='after')
    def _consistent(self):
        if self.amount > self.limit.limit:
            raise ValueError('offer amount exceeds its limit')
        if not math.isclose(self.rate, self.price.rate, abs_tol=1e-9):
            raise ValueError('rate must equal price.rate')
        return self


class Counterfactual(Contract):
    """What-if hint computed by re-running the engine (SPEC §10)."""
    lever: str
    current_value: FactValue
    required_value: FactValue
    resulting_amount: NonNegativeRub | None
    resulting_rate: float | None
    client_text: str


class Decision(Contract):
    contract_version: str = CONTRACT_VERSION
    application_id: str
    status: ApplicationStatus
    matches_request: bool | None  # set when there are offers
    route: Route
    route_reasons: list[Reason]
    reasons: list[Reason]
    stop_factors: list[Reason]
    flags: list[Reason]
    score: ScoreResult | None  # None when a stop factor fired
    challenger: ChallengerResult | None
    data_quality: DataQuality | None
    offers: Annotated[list[Offer], Field(max_length=4)]
    counterfactuals: list[Counterfactual]
    requested_documents: list[str]  # needs_data only
    reapply_after: date | None  # declined only
    model_version: str | None
    policy_version: str
    decided_at: datetime

    @model_validator(mode='after')
    def _consistent(self):
        if self.status not in DECISION_STATUSES:
            raise ValueError(f'decision status must be one of {sorted(DECISION_STATUSES)}')
        if self.stop_factors and (self.status != ApplicationStatus.DECLINED or self.score is not None):
            raise ValueError('a stop factor means declined without scoring')
        if self.status == ApplicationStatus.OFFERED and not self.offers:
            raise ValueError('offered needs at least one offer')
        if self.status == ApplicationStatus.DECLINED and self.offers:
            raise ValueError('declined has no offers')
        if self.status == ApplicationStatus.NEEDS_DATA and not self.requested_documents:
            raise ValueError('needs_data must list requested documents')
        expected = any(o.matches_request for o in self.offers) if self.offers else None
        if self.matches_request != expected:
            raise ValueError('matches_request must be true when any offer matches the request, null without offers')
        if self.score and self.score.model_version != self.model_version:
            raise ValueError('model_version must match score.model_version')
        return self
