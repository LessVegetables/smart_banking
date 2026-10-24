"""Credit-bureau processing: the MVP overdue rules (SPEC §5, §1.11) and the credit features.

The overdue rules decide which delinquencies count:

- a delay of up to `technical_days` (5) is technical and is ignored;
- an amount under `min_amount` (1 000 ₽) or under `min_payment_share` (1%) of the
  scheduled payment is ignored;
- an episode older than `half_weight_after_months` (24) counts at half weight, older
  than `ignore_after_months` (36) not at all. An episode's age runs from the payment
  that was missed (`Delinquency.started_at`).

The same cleaned episodes feed the scorecard features here and the stop factors (T14):
`SubjectHistory.worst_days_24m` and `current_overdue_days` are what "any 90+ in the last
24 months" and "current overdue over 30 days" (SPEC §7) read.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from apps.api.contracts import BkiLoan, BkiPayload, BkiReport, Delinquency

from .dates import months_before, months_between
from .policy import FeaturePolicy, OverdueRules

REVOLVING = frozenset({'overdraft', 'credit_line', 'card'})
CONTINGENT = frozenset({'guarantee'})  # a guarantee issued is not money owed


@dataclass(frozen=True)
class Episode:
    """One delinquency that passed the overdue rules."""
    loan_id: str
    started_at: date
    days: int
    amount: int
    age_months: int
    weight: float  # 1 or 0.5

    @property
    def weighted_days(self) -> int:
        return round(self.days * self.weight)


@dataclass(frozen=True)
class SubjectHistory:
    """The credit history of one bureau subject (the company, or the owner) after the overdue rules."""
    subject: str
    has_loans: bool  # any product, open or closed; without one there is no history to judge
    episodes: tuple[Episode, ...]  # oldest first
    current_overdue_days: int  # worst significant overdue on an open product today, 0 if none

    @property
    def worst_days_24m(self) -> int:
        """Worst episode at full weight, i.e. within `half_weight_after_months` (stop factors, T14)."""
        return max((e.days for e in self.episodes if e.weight == 1), default=0)

    @property
    def worst_weighted_days(self) -> int:
        """The scorecard feature: worst episode over `ignore_after_months`, older ones at half weight."""
        return max([e.weighted_days for e in self.episodes] + [self.current_overdue_days])


def significant(days: int, amount: int, payment_due: int, rules: OverdueRules) -> bool:
    """Neither a technical delay nor a small amount."""
    return (days > rules.technical_days and amount >= rules.min_amount
            and amount >= payment_due * rules.min_payment_share)


def episode_weight(age_months: int, rules: OverdueRules) -> float:
    if age_months > rules.ignore_after_months:
        return 0
    return 0.5 if age_months > rules.half_weight_after_months else 1


def _episode(loan: BkiLoan, d: Delinquency, as_of: date, rules: OverdueRules) -> Episode | None:
    if not significant(d.days, d.amount, d.payment_due, rules):
        return None
    age = months_between(d.started_at, as_of)
    weight = episode_weight(age, rules)
    if not weight:
        return None
    return Episode(loan.loan_id, d.started_at, d.days, d.amount, age, weight)


def subject_history(report: BkiReport, as_of: date, rules: OverdueRules) -> SubjectHistory:
    episodes = [e for loan in report.loans for d in loan.delinquencies
                if (e := _episode(loan, d, as_of, rules)) is not None]
    current = [loan.current_overdue_days for loan in report.loans
               if loan.closed_at is None and significant(loan.current_overdue_days, loan.current_overdue_amount,
                                                         loan.monthly_payment, rules)]
    return SubjectHistory(report.subject, bool(report.loans), tuple(sorted(episodes, key=lambda e: e.started_at)),
                          max(current, default=0))


def report(bki: BkiPayload, subject: str) -> BkiReport | None:
    return next((r for r in bki.reports if r.subject == subject), None)


def business_report(bki: BkiPayload) -> BkiReport | None:
    """The business's own debt: the company report of an ООО, the only report of an ИП."""
    return report(bki, 'company') or report(bki, 'owner')


def inquiries_since(bki: BkiPayload, start: date) -> int:
    """Credit-history inquiries on or after `start`, all reports together.

    Our own application leaves no inquiry in the report (T06), so every one counted is
    another lender's."""
    return sum(i.inquired_at >= start for r in bki.reports for i in r.inquiries)


def business_debt(bki: BkiPayload) -> int | None:
    # SIMPLIFICATION: the business's own debt only; the personal loans of an ООО's owner and
    # related companies are left out, a bank would look at the whole group.
    business = business_report(bki)
    if business is None:
        return None
    return sum(loan.balance for loan in business.loans if loan.closed_at is None and loan.kind not in CONTINGENT)


def limit_utilization(bki: BkiPayload) -> float | None:
    """Used share of the open revolving limits (overdrafts, credit lines, cards), all reports together."""
    lines = [loan for r in bki.reports for loan in r.loans
             if loan.kind in REVOLVING and loan.closed_at is None and loan.amount > 0]
    if not lines:
        return None
    return sum(loan.balance for loan in lines) / sum(loan.amount for loan in lines)


@dataclass(frozen=True)
class CreditFeatures:
    worst_delinquency_days_24m_company: int | None
    worst_delinquency_days_24m_owner: int | None
    bki_inquiries_3m: int | None
    limit_utilization: float | None
    business_debt: int | None  # rubles; divided by revenue in extract.py


NO_BKI = CreditFeatures(None, None, None, None, None)


def credit_features(bki: BkiPayload | None, as_of: date, policy: FeaturePolicy, *, is_ip: bool) -> CreditFeatures:
    if bki is None:
        return NO_BKI

    def worst(subject: str) -> int | None:
        r = report(bki, subject)
        if r is None or not r.loans:  # no credit history is not a clean one: "no data"
            return None
        return subject_history(r, as_of, policy.overdue).worst_weighted_days

    utilization = limit_utilization(bki)
    return CreditFeatures(
        worst_delinquency_days_24m_company=None if is_ip else worst('company'),
        worst_delinquency_days_24m_owner=worst('owner'),
        bki_inquiries_3m=inquiries_since(bki, months_before(as_of, policy.inquiry_window_months)),
        limit_utilization=None if utilization is None else round(utilization, 3),
        business_debt=business_debt(bki),
    )
