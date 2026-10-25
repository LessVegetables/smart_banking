"""Write THIRD_PARTY_NOTICES.md from requirements-lock.txt and package-lock.json.

    python scripts/third_party_notices.py          # rewrite the file
    python scripts/third_party_notices.py --check  # exit 1 if the file is out of date

npm licenses come from package-lock.json. Python lock files carry no licenses, so they are listed
in PYTHON below: a package added to requirements-lock.txt has to be added there too.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'THIRD_PARTY_NOTICES.md'

# name (lower case, as in the lock) -> (SPDX license, project page)
PYTHON = {
    'alembic': ('MIT', 'https://github.com/sqlalchemy/alembic'),
    'annotated-doc': ('MIT', 'https://github.com/fastapi/annotated-doc'),
    'annotated-types': ('MIT', 'https://github.com/annotated-types/annotated-types'),
    'anyio': ('MIT', 'https://github.com/agronholm/anyio'),
    'boto3': ('Apache-2.0', 'https://github.com/boto/boto3'),
    'botocore': ('Apache-2.0', 'https://github.com/boto/botocore'),
    'certifi': ('MPL-2.0', 'https://github.com/certifi/python-certifi'),
    'click': ('BSD-3-Clause', 'https://github.com/pallets/click'),
    'colorama': ('BSD-3-Clause', 'https://github.com/tartley/colorama'),
    'fastapi': ('MIT', 'https://github.com/fastapi/fastapi'),
    'h11': ('MIT', 'https://github.com/python-hyper/h11'),
    'httpcore': ('BSD-3-Clause', 'https://github.com/encode/httpcore'),
    'httpx': ('BSD-3-Clause', 'https://github.com/encode/httpx'),
    'idna': ('BSD-3-Clause', 'https://github.com/kjd/idna'),
    'iniconfig': ('MIT', 'https://github.com/pytest-dev/iniconfig'),
    'jmespath': ('MIT', 'https://github.com/jmespath/jmespath.py'),
    'mako': ('MIT', 'https://github.com/sqlalchemy/mako'),
    'markupsafe': ('BSD-3-Clause', 'https://github.com/pallets/markupsafe'),
    'opentelemetry-api': ('Apache-2.0', 'https://github.com/open-telemetry/opentelemetry-python'),
    'packaging': ('Apache-2.0 OR BSD-2-Clause', 'https://github.com/pypa/packaging'),
    'pluggy': ('MIT', 'https://github.com/pytest-dev/pluggy'),
    'psycopg': ('LGPL-3.0-only', 'https://github.com/psycopg/psycopg'),
    'psycopg-binary': ('LGPL-3.0-only', 'https://github.com/psycopg/psycopg'),
    'pydantic': ('MIT', 'https://github.com/pydantic/pydantic'),
    'pydantic-core': ('MIT', 'https://github.com/pydantic/pydantic-core'),
    'pygments': ('BSD-2-Clause', 'https://github.com/pygments/pygments'),
    'pytest': ('MIT', 'https://github.com/pytest-dev/pytest'),
    'python-dateutil': ('Apache-2.0 AND BSD-3-Clause', 'https://github.com/dateutil/dateutil'),
    'python-multipart': ('Apache-2.0', 'https://github.com/Kludex/python-multipart'),
    's3transfer': ('Apache-2.0', 'https://github.com/boto/s3transfer'),
    'six': ('MIT', 'https://github.com/benjaminp/six'),
    'sqlalchemy': ('MIT', 'https://github.com/sqlalchemy/sqlalchemy'),
    'starlette': ('BSD-3-Clause', 'https://github.com/Kludex/starlette'),
    'typing-extensions': ('PSF-2.0', 'https://github.com/python/typing_extensions'),
    'typing-inspection': ('MIT', 'https://github.com/pydantic/typing-inspection'),
    'tzdata': ('Apache-2.0', 'https://github.com/python/tzdata'),
    'urllib3': ('MIT', 'https://github.com/urllib3/urllib3'),
    'uvicorn': ('BSD-3-Clause', 'https://github.com/Kludex/uvicorn'),
}

HEADER = """# Сторонние компоненты

Файл создаёт `python scripts/third_party_notices.py` из `requirements-lock.txt` и `package-lock.json`; руками не правится. Тексты лицензий — на страницах проектов и в установленных пакетах.

Все компоненты используются без изменений. `psycopg` (LGPL-3.0) подключается как обычная библиотека и может быть заменён другой версией без пересборки приложения; `psycopg-binary` содержит собранные `libpq` (PostgreSQL License) и OpenSSL (Apache-2.0).
"""

FOOTER = """
## Образы Docker

| Образ | Где | Лицензия |
|---|---|---|
| `python:3.12-slim` | образ приложения (`Dockerfile`) | Python — PSF-2.0; пакеты Debian — их собственные лицензии |
| `node:22-alpine` | только сборка клиента (`Dockerfile`, в итоговый образ не попадает) | Node.js — MIT; пакеты Alpine — их собственные лицензии |
| `postgres:16-alpine` | база данных (`compose.yaml`) | PostgreSQL License |
| `caddy:2-alpine` | обратный прокси VPS (`compose.vps.yaml`) | Apache-2.0 |

## Модели

В репозиторий и образы модели не входят. Языковая модель подключается по желанию через `LLM_BASE_URL` / `LLM_MODEL` (`README.md`, «Языковая модель»); пример из README — [Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct), Apache-2.0. Скоринговые карты `apps/ml/scorecard_v1.json` и `apps/api/scorecards/scorecard_v0.json` — собственные, обучены или заданы на синтетических данных проекта.
"""


def python_packages() -> list[tuple[str, str]]:
    pins = []
    for line in (ROOT / 'requirements-lock.txt').read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            name, version = line.split('==')
            pins.append((name.strip(), version.strip()))
    return sorted(pins, key=lambda p: p[0].lower())


def npm_packages(dev: bool) -> list[tuple[str, str, str]]:
    lock = json.loads((ROOT / 'package-lock.json').read_text(encoding='utf-8'))
    found = []
    for path, info in lock['packages'].items():
        if path and bool(info.get('dev')) == dev:
            found.append((path.rsplit('node_modules/', 1)[-1], info['version'], info.get('license') or 'не указана'))
    return sorted(set(found))


def render() -> str:
    lines = [HEADER, '## Python (образ приложения)', '', '| Пакет | Версия | Лицензия |', '|---|---|---|']
    missing = []
    for name, version in python_packages():
        key = re.sub(r'[-_.]+', '-', name).lower()
        if key not in PYTHON:
            missing.append(name)
            continue
        spdx, url = PYTHON[key]
        lines.append(f'| [{name}]({url}) | {version} | {spdx} |')
    if missing:
        raise SystemExit(f'Add the license of {", ".join(missing)} to PYTHON in scripts/third_party_notices.py')
    lines += ['', '## JavaScript в собранном клиенте (`dist/`)', '', '| Пакет | Версия | Лицензия |', '|---|---|---|']
    lines += [f'| [{n}](https://www.npmjs.com/package/{n}) | {v} | {l} |' for n, v, l in npm_packages(dev=False)]
    lines += ['', '## JavaScript только для сборки клиента', '',
              'Нужны `npm run build` (TypeScript, Vite и их зависимости); в собранный клиент и итоговый образ не попадают.',
              '', '| Пакет | Версия | Лицензия |', '|---|---|---|']
    lines += [f'| {n} | {v} | {l} |' for n, v, l in npm_packages(dev=True)]
    return '\n'.join(lines) + '\n' + FOOTER


if __name__ == '__main__':
    text = render()
    if '--check' in sys.argv[1:]:
        if not OUT.exists() or OUT.read_text(encoding='utf-8') != text:
            raise SystemExit('THIRD_PARTY_NOTICES.md is out of date: run python scripts/third_party_notices.py')
    else:
        OUT.write_text(text, encoding='utf-8', newline='\n')
        print(f'{OUT.name} written')
