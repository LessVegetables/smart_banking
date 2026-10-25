# Развёртывание на VPS

Как показать прототип с сервера: тот же Docker-образ, что в README, за обратным прокси с HTTPS. Два варианта:

- **Caddy** — всё в Docker: стек `compose.vps.yaml`, запуск `deploy.sh`, настройки `deploy/Caddyfile`. Разделы ниже до «Вариант с nginx».
- **nginx** на самом сервере перед `compose.yaml`: `deploy/nginx/`, раздел «Вариант с nginx».

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

## Вариант с nginx

Если на сервере уже работает nginx (или другой сервис на 80/443), Caddy не нужен: стек поднимается из `compose.yaml` на `127.0.0.1:8000`, а nginx принимает HTTPS и проксирует на этот адрес. `deploy.sh` в этом варианте не запускайте — он поднимает Caddy на тех же портах. Готовый конфиг для домена `etobank.ru` — `deploy/nginx/etobank.ru.conf`; для другого домена замените имя в `server_name` и путях сертификата.

`compose.yaml` рассчитан на локальный запуск и задаёт `APP_ORIGIN=http://127.0.0.1:8000`. За nginx браузер присылает `Origin: https://etobank.ru`, и API отвечает 403 на любой изменяющий запрос. Поэтому стек запускается вместе с `deploy/nginx/compose.nginx.yaml`, который берёт `APP_ORIGIN` и `COOKIE_SECURE` из `.env`.

1. DNS: записи A (и AAAA, если есть IPv6) для `etobank.ru` и `умныйкредитныйкабинет.рф` указывают на сервер.
2. `.env` в папке с кодом: `python3 scripts/init_env.py` создаёт его со случайными `STAFF_PASSWORD` и `POSTGRES_PASSWORD` (или `cp .env.example .env` и задайте их сами). Затем в `.env`:

   ```dotenv
   APP_ORIGIN=https://etobank.ru
   COOKIE_SECURE=true
   ```

3. Стек:

   ```bash
   docker compose -f compose.yaml -f deploy/nginx/compose.nginx.yaml up -d --build --wait
   ```

4. Сертификат Let's Encrypt (Ubuntu / Debian), до включения конфига — блок 443 без сертификата не пройдёт `nginx -t`:

   ```bash
   sudo apt install nginx certbot python3-certbot-nginx
   sudo certbot certonly --nginx -d etobank.ru --deploy-hook "systemctl reload nginx"
   sudo certbot certonly --nginx -d xn--80achddpccdgernff2bzbib6ni.xn--p1ai --deploy-hook "systemctl reload nginx"
   ```

   Второй сертификат — для `умныйкредитныйкабинет.рф`: certbot и nginx принимают кириллический домен только в punycode.

   `certonly` только получает сертификат и не меняет конфиги nginx. Продление выполняет таймер certbot; после продления nginx перезагружается.

5. Конфиг nginx:

   ```bash
   sudo cp deploy/nginx/etobank.ru.conf /etc/nginx/sites-available/etobank.ru
   sudo ln -s /etc/nginx/sites-available/etobank.ru /etc/nginx/sites-enabled/etobank.ru
   sudo nginx -t
   sudo systemctl reload nginx
   ```

После этого кабинет — https://etobank.ru/, рабочее место — https://etobank.ru/staff, mini app — https://etobank.ru/mini/. Конфиг проверен `nginx -t` на версиях 1.18, 1.24 и 1.27; на 1.25 и новее nginx предупреждает, что `listen … http2` устарел, — это не ошибка (можно заменить на `listen 443 ssl;` и `http2 on;`).

Что делает конфиг: `умныйкредитныйкабинет.рф` (по HTTP и HTTPS) перенаправляет на `https://etobank.ru` с тем же путём и строкой запроса; если в ссылке нет `utm_source`, добавляется `utm_source=rf_domain`, чтобы в аналитике был виден этот домен (`/staff?utm_source=tg` → `https://etobank.ru/staff?utm_source=tg`, `/staff?utm_campaign=x` → `https://etobank.ru/staff?utm_campaign=x&utm_source=rf_domain`, `/` → `https://etobank.ru/?utm_source=rf_domain`). Метку меняет `map` в начале блока `.рф`; HTTP → HTTPS, TLS 1.2–1.3, HSTS, gzip, тело запроса до 12 МБ (загрузка выписок — до 5 файлов по 2 МБ), заголовки `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`. Заголовка `X-Frame-Options` нет намеренно: веб-версия Telegram открывает mini app во фрейме.

Управление — те же команды Compose с двумя файлами:

```bash
docker compose -f compose.yaml -f deploy/nginx/compose.nginx.yaml ps
docker compose -f compose.yaml -f deploy/nginx/compose.nginx.yaml logs -f --tail=100 api worker
docker compose -f compose.yaml -f deploy/nginx/compose.nginx.yaml up -d --build --wait
```

В этом варианте сервисы читают `.env` целиком, как при локальном запуске: сценарий отказа БКИ для персоны `source_down` и остальные `MOCK_SOURCES_*`, `SCORECARD_*`, `LOG_LEVEL`, `SOURCE_*` работают.

## Отличия от локального запуска (вариант с Caddy)

- `compose.vps.yaml` передаёт сервисам только явный список переменных, без `MOCK_SOURCES_*`, `SCORECARD_*`, `LOG_LEVEL` и `SOURCE_*`. Поэтому на VPS персона `source_down` получает обычный ответ БКИ и решается автоматически, а уровень журнала — по умолчанию. Это известное ограничение (`docs/v2/NOTES.md`, Unassigned).
- Синтетический мир, как и в README, собирается в образ при сборке; `WORLD_*` не действуют.

Прототип работает на синтетических данных, все внешние системы — моки ([docs/SIMPLIFICATIONS.md](docs/SIMPLIFICATIONS.md)).
