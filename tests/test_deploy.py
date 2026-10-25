"""Delivery artifacts (T34): the committed OpenAPI schema, the request collection, third-party notices,
the variables compose reads and the app's log level."""
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='alfa-tests-'))
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.path.join(os.environ['DATA_DIR'], 'test.db').replace('\\', '/'))
os.environ.setdefault('WORKER_ENABLED', 'false')

import pytest

from apps.api.db import init_db

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import export_openapi  # noqa: E402
import third_party_notices  # noqa: E402

REQUESTS = ROOT / 'docs' / 'api' / 'requests.http'


def test_committed_openapi_matches_the_app():
    committed = export_openapi.OUT.read_text(encoding='utf-8')
    assert committed == export_openapi.schema(), 'run python scripts/export_openapi.py'


def collection():
    """(method, path) of every request in requests.http, with {{…}} values as path parameters."""
    lines = REQUESTS.read_text(encoding='utf-8').splitlines()
    found = []
    for line in lines:
        m = re.match(r'^(GET|POST|PUT|PATCH|DELETE) \{\{(?:client|staff)\}\}(\S+)$', line)
        if m:
            found.append((m[1], m[2].split('?', 1)[0]))
    return found


def test_request_collection_only_uses_documented_routes():
    paths = json.loads(export_openapi.OUT.read_text(encoding='utf-8'))['paths']
    routes = [(method.upper(), re.compile(re.sub(r'\\\{[^}]+\\\}', '[^/]+', re.escape(path)) + '$'))
              for path, ops in paths.items() for method in ops]
    requests = collection()
    assert len(requests) >= 20
    for method, path in requests:
        assert any(m == method and r.match(path) for m, r in routes), (method, path)


def test_request_collection_covers_the_v2_flow():
    used = {path.split('}}')[-1] for _, path in collection()}
    for step in ('/accept', '/sign/code', '/sign', '/review', '/api/auth/staff', '/api/auth/demo'):
        assert step in used, step


def test_third_party_notices_are_up_to_date():
    assert third_party_notices.OUT.read_text(encoding='utf-8') == third_party_notices.render(), \
        'run python scripts/third_party_notices.py'


def test_every_locked_package_has_a_license():
    text = third_party_notices.render()
    for name, version in third_party_notices.python_packages():
        assert f'| {version} |' in text and name in text, name
    for name, _, license in third_party_notices.npm_packages(dev=False):
        assert license != 'не указана', name


def test_env_example_has_every_variable_compose_reads():
    example = {line.split('=', 1)[0] for line in (ROOT / '.env.example').read_text(encoding='utf-8').splitlines()
               if '=' in line and not line.startswith('#')}
    used = set(re.findall(r'\$\{([A-Z_]+)', (ROOT / 'compose.yaml').read_text(encoding='utf-8')))
    assert used <= example, used - example


def test_env_example_scenario_is_valid_json():
    from apps.sources.service import parse_scenarios
    line = next(l for l in (ROOT / '.env.example').read_text(encoding='utf-8').splitlines()
                if l.startswith('MOCK_SOURCES_SCENARIOS='))
    assert parse_scenarios(json.loads(line.split('=', 1)[1]))


@pytest.fixture
def root_level():
    root = logging.getLogger()
    level = root.level
    yield
    root.setLevel(level)


def test_app_logs_at_info_after_migrations(root_level, monkeypatch):
    # Alembic's logging config sets the root logger to WARN while init_db runs.
    monkeypatch.delenv('LOG_LEVEL', raising=False)
    init_db()
    assert logging.getLogger('apps.api.scoring').isEnabledFor(logging.INFO)


def test_log_level_comes_from_env(root_level, monkeypatch):
    monkeypatch.setenv('LOG_LEVEL', 'warning')
    init_db()
    assert not logging.getLogger('apps.api.scoring').isEnabledFor(logging.INFO)

