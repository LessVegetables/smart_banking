# Сторонние компоненты

Файл создаёт `python scripts/third_party_notices.py` из `requirements-lock.txt` и `package-lock.json`; руками не правится. Тексты лицензий — на страницах проектов и в установленных пакетах.

Все компоненты используются без изменений. `psycopg` (LGPL-3.0) подключается как обычная библиотека и может быть заменён другой версией без пересборки приложения; `psycopg-binary` содержит собранные `libpq` (PostgreSQL License) и OpenSSL (Apache-2.0).

## Python (образ приложения)

| Пакет | Версия | Лицензия |
|---|---|---|
| [alembic](https://github.com/sqlalchemy/alembic) | 1.20.1 | MIT |
| [annotated-doc](https://github.com/fastapi/annotated-doc) | 0.0.5 | MIT |
| [annotated-types](https://github.com/annotated-types/annotated-types) | 0.8.0 | MIT |
| [anyio](https://github.com/agronholm/anyio) | 4.15.1 | MIT |
| [boto3](https://github.com/boto/boto3) | 1.43.117 | Apache-2.0 |
| [botocore](https://github.com/boto/botocore) | 1.43.117 | Apache-2.0 |
| [certifi](https://github.com/certifi/python-certifi) | 2026.10.19 | MPL-2.0 |
| [click](https://github.com/pallets/click) | 8.5.0 | BSD-3-Clause |
| [colorama](https://github.com/tartley/colorama) | 0.4.6 | BSD-3-Clause |
| [fastapi](https://github.com/fastapi/fastapi) | 0.159.1 | MIT |
| [h11](https://github.com/python-hyper/h11) | 0.16.0 | MIT |
| [httpcore](https://github.com/encode/httpcore) | 1.0.9 | BSD-3-Clause |
| [httpx](https://github.com/encode/httpx) | 0.28.1 | BSD-3-Clause |
| [idna](https://github.com/kjd/idna) | 3.20 | BSD-3-Clause |
| [iniconfig](https://github.com/pytest-dev/iniconfig) | 2.3.1 | MIT |
| [jmespath](https://github.com/jmespath/jmespath.py) | 1.1.0 | MIT |
| [Mako](https://github.com/sqlalchemy/mako) | 1.4.3 | MIT |
| [MarkupSafe](https://github.com/pallets/markupsafe) | 3.0.3 | BSD-3-Clause |
| [opentelemetry-api](https://github.com/open-telemetry/opentelemetry-python) | 1.45.1 | Apache-2.0 |
| [packaging](https://github.com/pypa/packaging) | 26.3 | Apache-2.0 OR BSD-2-Clause |
| [pluggy](https://github.com/pytest-dev/pluggy) | 1.7.0 | MIT |
| [psycopg](https://github.com/psycopg/psycopg) | 3.3.6 | LGPL-3.0-only |
| [psycopg-binary](https://github.com/psycopg/psycopg) | 3.3.6 | LGPL-3.0-only |
| [pydantic](https://github.com/pydantic/pydantic) | 2.14.1 | MIT |
| [pydantic_core](https://github.com/pydantic/pydantic-core) | 2.50.1 | MIT |
| [Pygments](https://github.com/pygments/pygments) | 2.21.0 | BSD-2-Clause |
| [pytest](https://github.com/pytest-dev/pytest) | 9.1.1 | MIT |
| [python-dateutil](https://github.com/dateutil/dateutil) | 2.9.0.post0 | Apache-2.0 AND BSD-3-Clause |
| [python-multipart](https://github.com/Kludex/python-multipart) | 0.0.32 | Apache-2.0 |
| [s3transfer](https://github.com/boto/s3transfer) | 0.19.3 | Apache-2.0 |
| [six](https://github.com/benjaminp/six) | 1.17.0 | MIT |
| [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) | 2.1.6 | MIT |
| [starlette](https://github.com/Kludex/starlette) | 1.9.0 | BSD-3-Clause |
| [typing-inspection](https://github.com/pydantic/typing-inspection) | 0.4.4 | MIT |
| [typing_extensions](https://github.com/python/typing_extensions) | 4.16.0 | PSF-2.0 |
| [tzdata](https://github.com/python/tzdata) | 2026.5 | Apache-2.0 |
| [urllib3](https://github.com/urllib3/urllib3) | 2.8.0 | MIT |
| [uvicorn](https://github.com/Kludex/uvicorn) | 0.54.0 | BSD-3-Clause |

## JavaScript в собранном клиенте (`dist/`)

| Пакет | Версия | Лицензия |
|---|---|---|
| [lucide-react](https://www.npmjs.com/package/lucide-react) | 0.468.0 | ISC |
| [react](https://www.npmjs.com/package/react) | 19.3.1 | MIT |
| [react-dom](https://www.npmjs.com/package/react-dom) | 19.3.1 | MIT |
| [scheduler](https://www.npmjs.com/package/scheduler) | 0.28.0 | MIT |

## JavaScript только для сборки клиента

Нужны `npm run build` (TypeScript, Vite и их зависимости); в собранный клиент и итоговый образ не попадают.

| Пакет | Версия | Лицензия |
|---|---|---|
| @babel/code-frame | 7.29.7 | MIT |
| @babel/compat-data | 7.29.7 | MIT |
| @babel/core | 7.29.7 | MIT |
| @babel/generator | 7.29.8 | MIT |
| @babel/helper-compilation-targets | 7.29.7 | MIT |
| @babel/helper-globals | 7.29.7 | MIT |
| @babel/helper-module-imports | 7.29.7 | MIT |
| @babel/helper-module-transforms | 7.29.7 | MIT |
| @babel/helper-plugin-utils | 7.29.7 | MIT |
| @babel/helper-string-parser | 7.29.7 | MIT |
| @babel/helper-validator-identifier | 7.29.7 | MIT |
| @babel/helper-validator-option | 7.29.7 | MIT |
| @babel/helpers | 7.29.10 | MIT |
| @babel/parser | 7.29.9 | MIT |
| @babel/plugin-transform-react-jsx-self | 7.29.7 | MIT |
| @babel/plugin-transform-react-jsx-source | 7.29.7 | MIT |
| @babel/template | 7.29.7 | MIT |
| @babel/traverse | 7.29.10 | MIT |
| @babel/types | 7.29.8 | MIT |
| @esbuild/aix-ppc64 | 0.25.12 | MIT |
| @esbuild/android-arm | 0.25.12 | MIT |
| @esbuild/android-arm64 | 0.25.12 | MIT |
| @esbuild/android-x64 | 0.25.12 | MIT |
| @esbuild/darwin-arm64 | 0.25.12 | MIT |
| @esbuild/darwin-x64 | 0.25.12 | MIT |
| @esbuild/freebsd-arm64 | 0.25.12 | MIT |
| @esbuild/freebsd-x64 | 0.25.12 | MIT |
| @esbuild/linux-arm | 0.25.12 | MIT |
| @esbuild/linux-arm64 | 0.25.12 | MIT |
| @esbuild/linux-ia32 | 0.25.12 | MIT |
| @esbuild/linux-loong64 | 0.25.12 | MIT |
| @esbuild/linux-mips64el | 0.25.12 | MIT |
| @esbuild/linux-ppc64 | 0.25.12 | MIT |
| @esbuild/linux-riscv64 | 0.25.12 | MIT |
| @esbuild/linux-s390x | 0.25.12 | MIT |
| @esbuild/linux-x64 | 0.25.12 | MIT |
| @esbuild/netbsd-arm64 | 0.25.12 | MIT |
| @esbuild/netbsd-x64 | 0.25.12 | MIT |
| @esbuild/openbsd-arm64 | 0.25.12 | MIT |
| @esbuild/openbsd-x64 | 0.25.12 | MIT |
| @esbuild/openharmony-arm64 | 0.25.12 | MIT |
| @esbuild/sunos-x64 | 0.25.12 | MIT |
| @esbuild/win32-arm64 | 0.25.12 | MIT |
| @esbuild/win32-ia32 | 0.25.12 | MIT |
| @esbuild/win32-x64 | 0.25.12 | MIT |
| @jridgewell/gen-mapping | 0.3.13 | MIT |
| @jridgewell/remapping | 2.3.5 | MIT |
| @jridgewell/resolve-uri | 3.1.2 | MIT |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT |
| @jridgewell/trace-mapping | 0.3.31 | MIT |
| @napi-rs/lzma-linux-x64-gnu | 1.5.1 | MIT |
| @rolldown/pluginutils | 1.0.0-beta.27 | MIT |
| @rollup/rollup-android-arm-eabi | 4.65.0 | MIT |
| @rollup/rollup-android-arm64 | 4.65.0 | MIT |
| @rollup/rollup-darwin-arm64 | 4.65.0 | MIT |
| @rollup/rollup-darwin-x64 | 4.65.0 | MIT |
| @rollup/rollup-freebsd-arm64 | 4.65.0 | MIT |
| @rollup/rollup-freebsd-x64 | 4.65.0 | MIT |
| @rollup/rollup-linux-arm-gnueabihf | 4.65.0 | MIT |
| @rollup/rollup-linux-arm-musleabihf | 4.65.0 | MIT |
| @rollup/rollup-linux-arm64-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-arm64-musl | 4.65.0 | MIT |
| @rollup/rollup-linux-loong64-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-loong64-musl | 4.65.0 | MIT |
| @rollup/rollup-linux-ppc64-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-ppc64-musl | 4.65.0 | MIT |
| @rollup/rollup-linux-riscv64-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-riscv64-musl | 4.65.0 | MIT |
| @rollup/rollup-linux-s390x-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-x64-gnu | 4.65.0 | MIT |
| @rollup/rollup-linux-x64-musl | 4.65.0 | MIT |
| @rollup/rollup-openbsd-x64 | 4.65.0 | MIT |
| @rollup/rollup-openharmony-arm64 | 4.65.0 | MIT |
| @rollup/rollup-win32-arm64-msvc | 4.65.0 | MIT |
| @rollup/rollup-win32-ia32-msvc | 4.65.0 | MIT |
| @rollup/rollup-win32-x64-gnu | 4.65.0 | MIT |
| @rollup/rollup-win32-x64-msvc | 4.65.0 | MIT |
| @types/babel__core | 7.20.5 | MIT |
| @types/babel__generator | 7.27.0 | MIT |
| @types/babel__template | 7.4.4 | MIT |
| @types/babel__traverse | 7.28.0 | MIT |
| @types/estree | 1.0.9 | MIT |
| @types/react | 19.3.0 | MIT |
| @types/react-dom | 19.3.0 | MIT |
| @vitejs/plugin-react | 4.7.0 | MIT |
| baseline-browser-mapping | 2.11.30 | Apache-2.0 |
| browserslist | 4.29.4 | MIT |
| caniuse-lite | 1.0.30001819 | CC-BY-4.0 |
| convert-source-map | 2.0.0 | MIT |
| csstype | 3.2.3 | MIT |
| debug | 4.4.3 | MIT |
| electron-to-chromium | 1.5.463 | ISC |
| esbuild | 0.25.12 | MIT |
| escalade | 3.2.0 | MIT |
| fdir | 6.5.0 | MIT |
| fsevents | 2.3.3 | MIT |
| gensync | 1.0.0-beta.2 | MIT |
| js-tokens | 4.0.0 | MIT |
| jsesc | 3.1.0 | MIT |
| json5 | 2.2.3 | MIT |
| lru-cache | 5.1.1 | ISC |
| ms | 2.1.3 | MIT |
| nanoid | 3.3.20 | MIT |
| node-releases | 2.0.60 | MIT |
| picocolors | 1.1.1 | ISC |
| picomatch | 4.0.7 | MIT |
| postcss | 8.5.31 | MIT |
| react-refresh | 0.17.0 | MIT |
| rollup | 4.65.0 | MIT |
| semver | 6.3.1 | ISC |
| source-map-js | 1.2.2 | BSD-3-Clause |
| tinyglobby | 0.2.17 | MIT |
| typescript | 5.7.3 | Apache-2.0 |
| update-browserslist-db | 1.3.4 | MIT |
| vite | 6.4.5 | MIT |
| yallist | 3.1.1 | ISC |

## Образы Docker

| Образ | Где | Лицензия |
|---|---|---|
| `python:3.12-slim` | образ приложения (`Dockerfile`) | Python — PSF-2.0; пакеты Debian — их собственные лицензии |
| `node:22-alpine` | только сборка клиента (`Dockerfile`, в итоговый образ не попадает) | Node.js — MIT; пакеты Alpine — их собственные лицензии |
| `postgres:16-alpine` | база данных (`compose.yaml`) | PostgreSQL License |
| `caddy:2-alpine` | обратный прокси VPS (`compose.vps.yaml`) | Apache-2.0 |

## Модели

В репозиторий и образы модели не входят. Языковая модель подключается по желанию через `LLM_BASE_URL` / `LLM_MODEL` (`README.md`, «Языковая модель»); пример из README — [Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct), Apache-2.0. Скоринговые карты `apps/ml/scorecard_v1.json` и `apps/api/scorecards/scorecard_v0.json` — собственные, обучены или заданы на синтетических данных проекта.
