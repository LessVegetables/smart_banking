"""The client card, ФССП, арбитраж / Федресурс and 115-ФЗ answers in the synthetic world (T06)."""
from collections import Counter
from datetime import date

import pytest

from apps.api import contracts as c
from apps.world import generate_world
from apps.world.registries import COURTS

AS_OF = date(2026, 10, 1)


@pytest.fixture(scope='module')
def world():
    return generate_world(seed=7, n=3000, as_of=AS_OF)


def _share(pred, group):
    return sum(map(pred, group)) / len(group)


def _weak(world):
    return [x for x in world.companies if x.q < -1 and x.archetype != 'shell']


def _strong(world):
    return [x for x in world.companies if x.q > 1 and x.archetype != 'shell']


def _sued_over_10pct(x):
    claims = sum(k.claim_amount for k in x.arbitration.cases if k.role == 'defendant' and k.status != 'dismissed')
    return claims > 0.1 * x.annual_revenue


def test_payloads_match_contracts(world):
    for x in world.companies:
        for model, value in ((c.ClientCardPayload, x.client_card), (c.FsspPayload, x.fssp),
                             (c.ArbitrationPayload, x.arbitration), (c.AmlPayload, x.aml)):
            assert model.model_validate(value.model_dump(mode='json')) == value


def test_sources_are_reproducible():
    # The earlier data staying the same is checked by the hash tests of T02-T03.
    sources = lambda w: [(x.client_card, x.fssp, x.arbitration, x.aml) for x in w.companies]
    assert sources(generate_world(11, 200, AS_OF)) == sources(generate_world(11, 200, AS_OF))
    assert sources(generate_world(11, 200, AS_OF)) != sources(generate_world(12, 200, AS_OF))


def test_client_card_follows_the_account_and_loans(world):
    for x in world.companies:
        card = x.client_card
        assert card.client_since == x.accounts.account_opened_at
        account = card.products[0]
        assert account.kind == 'account' and account.opened_at == card.client_since
        assert [p.opened_at for p in card.products] == sorted(p.opened_at for p in card.products)
        assert all(card.client_since <= p.opened_at < AS_OF for p in card.products)
        open_loans = {(b.opened_at, b.amount, b.balance) for b in x.bki.reports[0].loans
                      if b.loan_id.startswith(f'{x.company_id}-L') and b.kind == 'loan' and b.closed_at is None}
        assert {(p.opened_at, p.amount, p.balance) for p in card.products if p.kind == 'loan'} == open_loans
        if x.archetype == 'shell':
            assert not any(p.kind in ('card', 'loan') for p in card.products)
        if x.legal_form == c.LegalForm.IP:
            assert not any(p.kind == 'deposit' for p in card.products)
        assert card.related_companies == []


def test_client_card_product_mix(world):
    cards = [x.client_card for x in world.companies]
    kinds = Counter(p.kind for card in cards for p in card.products)
    assert kinds['account'] == len(cards)
    assert kinds['card'] > 0.3 * len(cards) and kinds['loan'] > 0.1 * len(cards) and kinds['deposit'] > 0.1 * len(cards)
    assert 0 < _share(lambda card: card.internal_stop_list, cards) < 0.01


def test_fssp_proceedings(world):
    for x in world.companies:
        for p in x.fssp.proceedings:
            assert x.egrul.registered_at < p.opened_at < AS_OF
            assert 0 < p.remaining <= p.amount
            assert p.number.endswith('-ИП') and p.number.split('/')[2].startswith(x.inn[:2])
            if x.legal_form == c.LegalForm.IP:
                assert p.subject == 'owner'
    kinds = Counter(p.kind for x in world.companies for p in x.fssp.proceedings)
    assert set(kinds) == {'Налоги и сборы', 'Задолженность по кредитным платежам', 'Задолженность по договору',
                          'Коммунальные платежи', 'Штраф'}


def test_fssp_follows_quality_and_tax_debt(world):
    has = lambda x: bool(x.fssp.proceedings)
    assert _share(has, _weak(world)) > 5 * _share(has, _strong(world))
    assert 0.04 < _share(has, world.companies) < 0.15  # the SPEC §7 flag must stay an exception
    taxed = lambda x: any(p.kind == 'Налоги и сборы' for p in x.fssp.proceedings)
    assert all(x.fns.tax_debt for x in world.companies if taxed(x))
    debtors = [x for x in world.companies if x.fns.tax_debt]
    assert 0.25 < _share(taxed, debtors) < 0.6


def test_arbitration_cases(world):
    for x in world.companies:
        court = COURTS[x.egrul.region]
        for k in x.arbitration.cases:
            assert k.number.startswith(f'А{court}-') and k.number.endswith(f'/{k.filed_at.year}')
            assert x.egrul.registered_at < k.filed_at < AS_OF and k.claim_amount >= 1000
        notices = x.arbitration.bankruptcy_notices
        assert [n.published_at for n in notices] == sorted(n.published_at for n in notices)
        assert all(n.published_at < AS_OF for n in notices)
    roles = Counter(k.role for x in world.companies for k in x.arbitration.cases)
    assert roles['plaintiff'] > roles['defendant'] > roles['third_party'] > 0


def test_arbitration_follows_quality(world):
    sued = lambda x: any(k.role == 'defendant' for k in x.arbitration.cases)
    weak, strong = _weak(world), _strong(world)
    assert _share(sued, weak) > 5 * _share(sued, strong)
    assert 0.005 < _share(_sued_over_10pct, world.companies) < 0.04
    bankrupt = lambda x: bool(x.arbitration.bankruptcy_notices)
    assert 0 < _share(bankrupt, world.companies) < 0.015
    assert _share(bankrupt, weak) > 5 * _share(bankrupt, strong)


def test_aml_finds_most_shells(world):
    shells = [x for x in world.companies if x.archetype == 'shell']
    others = [x for x in world.companies if x.archetype != 'shell']
    assert _share(lambda x: x.aml.transit_operations, shells) > 0.6
    assert _share(lambda x: x.aml.transit_operations, others) < 0.02
    assert _share(lambda x: x.aml.zsk_zone != 'green', shells) > 0.8
    assert all(x.aml.zsk_zone != 'red' for x in others)
    assert _share(lambda x: x.aml.zsk_zone == 'yellow', others) < 0.03


def test_aml_list_matches_name_a_real_party(world):
    matched = [x for x in world.companies if x.aml.list_matches]
    assert 0 < len(matched) < 0.01 * len(world.companies)
    for x in matched:
        names = {'company': {x.egrul.name}, 'director': {x.egrul.director.full_name},
                 'founder': {f.full_name for f in x.egrul.founders}}
        for m in x.aml.list_matches:
            assert m.matched_name in names[m.subject]


def test_world_has_cases_for_every_source_stop_factor_and_flag(world):
    """SPEC §7 rules that read the T06 sources, each in both forms where both apply."""
    rules = {
        'bankruptcy notice': lambda x: bool(x.arbitration.bankruptcy_notices),
        'list match': lambda x: bool(x.aml.list_matches),
        'internal stop list': lambda x: x.client_card.internal_stop_list,
        'transit operations': lambda x: x.aml.transit_operations,
        'claims over 10% of revenue': _sued_over_10pct,
        'enforcement proceedings': lambda x: bool(x.fssp.proceedings),
    }
    for name, rule in rules.items():
        forms = {x.legal_form for x in world.companies if rule(x)}
        assert forms, name
        if name not in ('transit operations', 'internal stop list', 'list match', 'bankruptcy notice'):
            assert forms == {c.LegalForm.IP, c.LegalForm.OOO}, name
