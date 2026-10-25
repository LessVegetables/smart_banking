# Развёртывание на VPS

Как показать прототип с сервера: тот же Docker-образ, что в README, плюс Caddy для HTTP/HTTPS на портах 80 и 443. Стек описан в `compose.vps.yaml`, запуск — `deploy.sh`, настройки Caddy — `deploy/Caddyfile`.

## Что нужно

- Linux с Docker Engine и Docker Compose v2 (с поддержкой `up --wait`): [Ubuntu](https://docs.docker.com/engine/install/ubuntu/), [Debian](https://docs.docker.com/engine/install/debian/). Проверка: `docker --version` и `docker compose version`.
- Свободные порты 80 и 443.
- Не меньше 2 ГБ памяти для сборки: образ собирает клиент и синтетический мир. При первой сборке сервер загружает образы и зависимости из интернета.

## Код на сервере

Любой из двух способов:

- `git clone` репозитория на сервер;
- архив исходников: на своей машине `python scripts/package_vps.py` создаёт `alfa_hack-vps.zip` рядом с папкой репозитория (без `.env`, баз и зависимостей, с `MANIFEST.sha256` — хэшами файлов). Загрузите его на сервер и распакуйте: `unzip alfa_hack-vps.zip && cd alfa_hack`. Проверка целостности: `sha256sum -c MANIFEST.sha256`.

## Запуск по IP

Из папки с кодом:

```bash
bash deploy.sh http://YOUR_VPS_IP
```

Если Docker доступен только root — `sudo bash deploy.sh http://YOUR_VPS_IP`.

При первом запуске скрипт создаёт `.env.vps` с правами только для владельца: новые случайные `POSTGRES_PASSWORD` и `STAFF_PASSWORD`, `APP_ORIGIN` и пустые `TELEGRAM_BOT_TOKEN`, `LLM_*`. Существующий `.env.vps` не перезаписывается. Затем скрипт собирает образ и ждёт, пока сервисы станут здоровыми (до 240 с).

После запуска:

- `http://YOUR_VPS_IP/` — кабинет клиента;
- `http://YOUR_VPS_IP/mini/` — mini app;
- `http://YOUR_VPS_IP/staff` — рабочее место сотрудника, пароль — `STAFF_PASSWORD` из `.env.vps`;
- `http://YOUR_VPS_IP/docs` — OpenAPI.

Демо-учётки и сценарий проверки — те же, что в README («Демо-учётки», «Сценарий проверки»).

## Домен и HTTPS

Направьте DNS домена на сервер (для IPv6 — тоже). При первом запуске:

```bash
bash deploy.sh https://business.example.com
```

Caddy получает и обновляет сертификат сам. Чтобы перейти с IP на домен у уже установленного приложения, измените в `.env.vps` `APP_ORIGIN=https://business.example.com` и `COOKIE_SECURE=true`, затем снова выполните `bash deploy.sh https://business.example.com`. Пароли БД и сотрудника оставьте прежними. Запуск внутри Telegram требует HTTPS и `TELEGRAM_BOT_TOKEN`; меню бота настраивается на `<адрес>/mini/`.

## Языковая модель

Задайте в `.env.vps` `LLM_BASE_URL` и `LLM_MODEL` (OpenAI-совместимый сервер с моделью с открытыми весами; `LLM_API_KEY` — если сервер требует ключ) и перезапустите `bash deploy.sh <адрес>`. Без них работает справочный помощник по шаблонам. Подробно — README, раздел «Языковая модель».

## Управление

Из папки с кодом:

```bash
docker compose --env-file .env.vps -f compose.vps.yaml ps
docker compose --env-file .env.vps -f compose.vps.yaml logs -f --tail=100
docker compose --env-file .env.vps -f compose.vps.yaml stop
```

Повторный запуск и обновление после `git pull` — снова `bash deploy.sh <адрес>`. PostgreSQL и сертификаты хранятся в томах Docker: остановка и пересборка их сохраняют. Для переноса на другой сервер отдельно переносите резервную копию БД.

Если на сервере уже работает Nginx или другой сервис на 80/443, используйте `compose.yaml` из README (API привязан к `127.0.0.1:8000`) и направьте свой reverse proxy на этот адрес; в `.env` задайте публичный `APP_ORIGIN`.

## Отличия от локального запуска

- `compose.vps.yaml` передаёт сервисам только явный список переменных, без `MOCK_SOURCES_*`, `SCORECARD_*`, `LOG_LEVEL` и `SOURCE_*`. Поэтому на VPS персона `source_down` получает обычный ответ БКИ и решается автоматически, а уровень журнала — по умолчанию. Это известное ограничение (`docs/v2/NOTES.md`, Unassigned).
- Синтетический мир, как и в README, собирается в образ при сборке; `WORLD_*` не действуют.

Прототип работает на синтетических данных, все внешние системы — моки ([docs/SIMPLIFICATIONS.md](docs/SIMPLIFICATIONS.md)).
