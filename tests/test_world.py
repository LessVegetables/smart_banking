import statistics
from datetime import date

import pytest

from apps.api import contracts as c
from apps.world import company_rng, generate_world
from apps.world.__main__ import main, read_world
from apps.world.generator import SIZE_LIMITS, company_inn, ogrn, ogrnip, person_inn, valid_inn, valid_ogrn
from apps.world.industries import BY_KEY, INDUSTRIES

AS_OF = date(2026, 10, 1)


@pytest.fixture(scope='module')
def world():
    return generate_world(seed=7, n=3000, as_of=AS_OF)


def test_same_seed_same_world():
    a = generate_world(seed=1, n=200, as_of=AS_OF)
    b = generate_world(seed=1, n=200, as_of=AS_OF)
    assert a.model_dump_json() == b.model_dump_json()


def test_other_seed_other_world():
    a = generate_world(seed=1, n=200, as_of=AS_OF)
    b = generate_world(seed=2, n=200, as_of=AS_OF)
    assert [x.q for x in a.companies] != [x.q for x in b.companies]


def test_growing_the_world_keeps_existing_companies():
    small = generate_world(seed=3, n=50, as_of=AS_OF)
    large = generate_world(seed=3, n=80, as_of=AS_OF)
    assert large.companies[:50] == small.companies


def test_company_rng_streams_are_independent_and_stable():
    assert company_rng(1, 'C00001', 'turnovers').random() == company_rng(1, 'C00001', 'turnovers').random()
    assert company_rng(1, 'C00001', 'turnovers').random() != company_rng(1, 'C00001', 'bki').random()


@pytest.mark.parametrize('n', [0, 100_001])
def test_world_size_is_bounded(n):
    with pytest.raises(ValueError):
        generate_world(n=n)


def test_payloads_match_contracts(world):
    for company in world.companies:
        egrul = c.EgrulPayload.model_validate(company.egrul.model_dump(mode='json'))
        fns = c.FnsPayload.model_validate(company.fns.model_dump(mode='json'))
        assert egrul == company.egrul and fns == company.fns


def test_identifiers_are_unique_and_valid(world):
    companies = world.companies
    assert len({x.inn for x in companies}) == len(companies)
    assert len({x.egrul.ogrn for x in companies}) == len(companies)
    persons = {}
    for x in companies:
        assert valid_inn(x.inn) and valid_ogrn(x.egrul.ogrn)
        for p in [x.egrul.director, *x.egrul.founders]:
            assert valid_inn(p.inn)
            persons.setdefault(p.inn, p.full_name)
            assert persons[p.inn] == p.full_name, 'one INN, one person'
    company_inns = {x.inn for x in companies if x.legal_form == c.LegalForm.OOO}
    assert not company_inns & persons.keys()


def _bump_last(value: str) -> str:
    return value[:-1] + str((int(value[-1]) + 1) % 10)


def test_identifier_layout_and_checksums():
    inn10, inn12 = company_inn('5406', 1), person_inn('5407', 2)
    assert inn10.startswith('540600001') and len(inn10) == 10 and valid_inn(inn10)
    assert inn12.startswith('5407000002') and len(inn12) == 12 and valid_inn(inn12)
    assert not valid_inn(_bump_last(inn10)) and not valid_inn(_bump_last(inn12))
    for value in (ogrn(date(2023, 10, 12), '54', 17), ogrnip(date(2026, 4, 2), '54', 12)):
        assert len(value) in (13, 15) and valid_ogrn(value) and not valid_ogrn(_bump_last(value))


def test_legal_forms(world):
    forms = {x.legal_form for x in world.companies}
    assert forms == {c.LegalForm.IP, c.LegalForm.OOO}
    for x in world.companies:
        e = x.egrul
        assert e.legal_form == x.legal_form
        if x.legal_form == c.LegalForm.IP:
            assert len(e.inn) == 12 and len(e.ogrn) == 15
            assert e.founders == [] and e.director.inn == e.inn and e.name == f'ИП {e.director.full_name}'
            assert x.fns.financials is None and not x.fns.director_disqualified
        else:
            assert len(e.inn) == 10 and len(e.ogrn) == 13
            assert e.founders and sum(f.share for f in e.founders) == pytest.approx(1.0)
            assert x.fns.tax_return is None


def test_sizes_follow_209_fz(world):
    assert {x.size for x in world.companies} == {'micro', 'small'}
    for x in world.companies:
        max_employees, max_revenue = SIZE_LIMITS[x.size]
        assert 0 <= x.employees <= max_employees
        assert 0 < x.annual_revenue <= max_revenue
        assert x.fns.employees == x.employees


def test_dates_are_consistent(world):
    for x in world.companies:
        e = x.egrul
        assert e.registered_at < AS_OF
        assert e.registered_at <= e.director.since <= AS_OF
        assert all(f.since == e.registered_at for f in e.founders)
        filing = x.fns.financials or x.fns.tax_return
        if filing is None:
            assert e.registered_at.year == AS_OF.year
        else:
            assert filing.year == AS_OF.year - 1 and e.registered_at.year < AS_OF.year


def test_gir_bo_balances(world):
    for x in world.companies:
        f = x.fns.financials
        if f:
            assert f.total_assets == f.equity + f.liabilities


def test_hidden_quality_is_standard_normal(world):
    qs = [x.q for x in world.companies]
    assert abs(statistics.mean(qs)) < 0.08
    assert 0.92 < statistics.stdev(qs) < 1.08


def test_quality_drives_registry_signals(world):
    weak = [x for x in world.companies if x.q < -1]
    strong = [x for x in world.companies if x.q > 1]
    debt_share = lambda group: sum(x.fns.tax_debt > 0 for x in group) / len(group)
    assert debt_share(weak) > 2 * debt_share(strong)
    age = lambda group: statistics.median((AS_OF - x.egrul.registered_at).days for x in group)
    assert age(strong) > age(weak)


def test_world_covers_data_quality_cases(world):
    ages = [(AS_OF - x.egrul.registered_at).days for x in world.companies]
    young = sum(a < 182 for a in ages) / len(ages)
    assert 0.01 < young < 0.15, 'some companies are too young for 6 months of history (THIN)'
    assert {x.industry for x in world.companies} == set(BY_KEY)


def test_industry_parameters():
    assert sum(i.weight for i in INDUSTRIES) > 0
    for i in INDUSTRIES:
        assert len(i.seasonality) == 12
        assert statistics.mean(i.seasonality) == pytest.approx(1.0, abs=1e-3)
        assert 0 <= i.ip_share <= 1


def test_cli_writes_reproducible_files(tmp_path):
    for out in ('a', 'b'):
        main(['--seed', '5', '--n', '120', '--as-of', '2026-10-01', '--out', str(tmp_path / out)])
    for name in ('meta.json', 'companies.jsonl'):
        assert (tmp_path / 'a' / name).read_bytes() == (tmp_path / 'b' / name).read_bytes()
    world = read_world(tmp_path / 'a')
    assert world == generate_world(seed=5, n=120, as_of=AS_OF)
    assert world.meta.n == 120 and world.meta.as_of == AS_OF
