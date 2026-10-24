"""Default outcomes and the train/holdout split in the synthetic world (T05)."""
import bisect
import math
from datetime import date

import pytest
from pydantic import ValidationError

from apps.world import generate_world, train_holdout
from apps.world.archetypes import DATA_GAPS
from apps.world.outcomes import (CONFIG_PATH, OutcomeConfig, default_intercept, expected_default_rate,
                                 load_outcome_config)

AS_OF = date(2026, 10, 1)
CONFIG = load_outcome_config()


@pytest.fixture(scope='module')
def world():
    return generate_world(seed=7, n=3000, as_of=AS_OF)


def _rate(companies):
    return sum(x.default_12m for x in companies) / len(companies)


def _real(world):
    return [x for x in world.companies if x.archetype != 'shell']


def _gini(companies):
    """Gini of the true PD: the ceiling for any model trained on the world."""
    bad = [x.pd_12m for x in companies if x.default_12m]
    good = sorted(x.pd_12m for x in companies if not x.default_12m)
    wins = sum(bisect.bisect_left(good, p) + (bisect.bisect_right(good, p) - bisect.bisect_left(good, p)) / 2
               for p in bad)
    return 2 * wins / (len(bad) * len(good)) - 1


def test_config_file_is_valid():
    assert CONFIG == OutcomeConfig.model_validate_json(CONFIG_PATH.read_text(encoding='utf-8'))
    with pytest.raises(ValidationError):
        CONFIG.model_validate({**CONFIG.model_dump(), 'quality_slope': 0.5})
    with pytest.raises(ValidationError):
        CONFIG.model_validate({**CONFIG.model_dump(), 'typo': 1})
    offsets = CONFIG.industry_default_offsets
    for broken in ({k: v for k, v in offsets.items() if k != 'it'}, {**offsets, 'mining': 0.3}):
        with pytest.raises(ValidationError, match='industry_default_offsets'):
            CONFIG.model_validate({**CONFIG.model_dump(), 'industry_default_offsets': broken})


def test_world_records_its_outcome_config(world):
    assert world.meta.outcomes == CONFIG


@pytest.mark.parametrize('target', [0.02, 0.065, 0.15])
def test_intercept_hits_the_target_rate(target):
    config = CONFIG.model_copy(update={'target_default_rate': target})
    a = default_intercept(config)
    assert expected_default_rate(a, config.quality_slope, config.industry_default_offsets) == \
        pytest.approx(target, abs=1e-9)


def test_pd_follows_the_formula(world):
    a = default_intercept(CONFIG)
    for x in world.companies:
        if x.archetype == 'shell':
            assert x.pd_12m == CONFIG.shell_default_probability
        else:
            logit = a + CONFIG.quality_slope * x.q + CONFIG.industry_default_offsets[x.industry]
            assert x.pd_12m == round(1 / (1 + math.exp(-logit)), 6)


def test_default_rate_is_near_the_target(world):
    real = _real(world)
    se = math.sqrt(CONFIG.target_default_rate * (1 - CONFIG.target_default_rate) / len(real))
    assert abs(_rate(real) - CONFIG.target_default_rate) < 4 * se
    assert _rate(real) == pytest.approx(sum(x.pd_12m for x in real) / len(real), abs=4 * se)


def test_weak_companies_default_more(world):
    real = _real(world)
    weak = [x for x in real if x.q < -1]
    strong = [x for x in real if x.q > 1]
    assert _rate(weak) > 5 * _rate(strong)
    assert 0.5 < _gini(real) < 0.75


def test_every_grade_has_companies(world):
    # Grade bounds of SPEC §8. A trained scorecard spreads less than the true PD, but it needs all grades to exist.
    bounds = (0, 0.015, 0.03, 0.06, 0.12, 1)
    real = _real(world)
    for low, high in zip(bounds, bounds[1:]):
        share = sum(low <= x.pd_12m < high for x in real) / len(real)
        assert share > 0.08, f'grade {low}-{high}'


def test_shells_default(world):
    # An inflater or a data-gap client is a real business: test_pd_follows_the_formula covers it.
    shells = [x for x in world.companies if x.archetype == 'shell']
    assert _rate(shells) > 0.5 and _rate(shells) > 5 * _rate(_real(world))
    assert {x.archetype for x in world.companies} >= {'inflater', *DATA_GAPS}


def test_observable_signals_carry_the_outcome(world):
    # The engine never sees q, so T10 can only learn if the data reflects the outcome.
    real = _real(world)
    tax_debt = [x for x in real if x.fns.tax_debt > 0]
    assert _rate(tax_debt) > 1.5 * _rate([x for x in real if x.fns.tax_debt == 0])


def test_split_is_independent_of_the_outcome(world):
    train, holdout = train_holdout(world)
    assert len(train) + len(holdout) == len(world.companies)
    assert {x.sample for x in train} == {'train'} and {x.sample for x in holdout} == {'holdout'}
    share = CONFIG.holdout_share
    for group in ([x for x in world.companies if x.default_12m], [x for x in world.companies if not x.default_12m]):
        se = math.sqrt(share * (1 - share) / len(group))
        assert abs(sum(x.sample == 'holdout' for x in group) / len(group) - share) < 3 * se
    assert sum(x.default_12m for x in holdout) > 50, 'enough defaults for Gini on holdout (T11)'


def test_raising_the_target_only_adds_defaults():
    base = generate_world(seed=4, n=300, as_of=AS_OF)
    worse = generate_world(seed=4, n=300, as_of=AS_OF, outcomes=CONFIG.model_copy(update={'target_default_rate': 0.12}))
    assert sum(x.default_12m for x in worse.companies) > sum(x.default_12m for x in base.companies)
    for a, b in zip(base.companies, worse.companies):
        assert b.default_12m >= a.default_12m and b.sample == a.sample
        assert a.model_dump(exclude={'pd_12m', 'default_12m'}) == b.model_dump(exclude={'pd_12m', 'default_12m'})


def test_industry_offsets_shift_the_industry_but_keep_the_overall_rate():
    risky = {**CONFIG.industry_default_offsets, 'it': 1.5}
    config = CONFIG.model_copy(update={'industry_default_offsets': risky})
    base = generate_world(seed=4, n=300, as_of=AS_OF)
    shifted = generate_world(seed=4, n=300, as_of=AS_OF, outcomes=config)
    assert default_intercept(config) < default_intercept(CONFIG), 'the other industries get safer'
    for a, b in zip(base.companies, shifted.companies):
        if a.archetype != 'shell':
            assert (b.pd_12m > a.pd_12m) == (a.industry == 'it')


def test_changing_the_holdout_share_keeps_the_outcomes():
    base = generate_world(seed=4, n=300, as_of=AS_OF)
    larger = generate_world(seed=4, n=300, as_of=AS_OF, outcomes=CONFIG.model_copy(update={'holdout_share': 0.5}))
    for a, b in zip(base.companies, larger.companies):
        assert b.default_12m == a.default_12m
        assert a.sample == 'train' or b.sample == 'holdout'
