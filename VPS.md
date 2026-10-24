# Запуск ZIP на VPS

Архив содержит исходники, готовую frontend-сборку, Dockerfile, конфигурацию PostgreSQL/API/worker/Caddy и Linux-скрипт запуска. Локальные `.env`, базы, загруженные документы и зависимости в архив не входят. Пароли создаются заново на VPS.

## Быстрый запуск по IP

На сервере нужны Linux, Docker Engine с Docker Compose v2 (с поддержкой `up --wait`) и `unzip`. Порты 80 и 443 должны быть доступны и свободны. DNS для запуска по IP не требуется.

Загрузите `alfa_hack-vps.zip` на сервер, затем выполните, заменив IP:

```bash
unzip alfa_hack-vps.zip
cd alfa_hack
bash deploy.sh http://YOUR_VPS_IP
```

Если Docker доступен только root, запустите последнюю команду через `sudo bash deploy.sh http://YOUR_VPS_IP`.

После первой сборки:

- `http://YOUR_VPS_IP/` — веб-кабинет;
- `http://YOUR_VPS_IP/mini/` — компактный интерфейс;
- `http://YOUR_VPS_IP/staff` — сотрудник;
- `http://YOUR_VPS_IP/docs` — документация API.

Демонстрационные сценарии доступны на главной странице. Пароль сотрудника находится в `STAFF_PASSWORD` файла `.env.vps`. Файл создаётся на сервере с ограниченными правами; скрипт не переписывает существующие секреты.

## Домен и HTTPS

Направьте DNS домена на VPS. Для IPv6 запись также должна указывать на этот сервер. Затем при первом запуске используйте:

```bash
bash deploy.sh https://business.example.com
```

Caddy получает и обновляет сертификат автоматически. Публичные 80/443 необходимы для этого сценария. HTTP по IP подходит для просмотра демо; запуск внутри Telegram требует HTTPS и токен бота.

Чтобы перейти с IP на домен у уже установленного приложения, измените в `.env.vps` `APP_ORIGIN=https://business.example.com` и `COOKIE_SECURE=true`, затем снова выполните `bash deploy.sh https://business.example.com`. Пароли БД и сотрудника сохраните прежними. Оба клиента и API работают на одном домене.

## Если Docker ещё не установлен

Установите Docker Engine и Compose plugin по инструкции для дистрибутива VPS:

- [Docker Engine: Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Engine: Debian](https://docs.docker.com/engine/install/debian/)

Проверка: `docker --version` и `docker compose version`. При первой сборке сервер загружает Docker-образы и зависимости из интернета. Для сборки практично выделить 2 ГБ памяти или больше.

## Управление

Все команды ниже выполняются из распакованной папки `alfa_hack`:

```bash
# Статус
docker compose --env-file .env.vps -f compose.vps.yaml ps

# Логи
docker compose --env-file .env.vps -f compose.vps.yaml logs -f --tail=100

# Остановка с сохранением данных
docker compose --env-file .env.vps -f compose.vps.yaml stop

# Повторный запуск
bash deploy.sh http://YOUR_VPS_IP
```

PostgreSQL, документы и сертификаты хранятся в Docker volumes. Обычная остановка и пересборка их сохраняют. Для переноса на другой сервер отдельно переносите резервную копию БД и файлов; ZIP исходников пользовательские данные не содержит.

При подключении языковой модели или токена Telegram в `.env.vps` перезапустите через ту же команду `bash deploy.sh <адрес>`. Модель включается, когда заданы `LLM_BASE_URL` и `LLM_MODEL` (OpenAI-совместимый сервер с моделью с открытыми весами, `LLM_API_KEY` — если сервер требует ключ; подробнее — раздел «Языковая модель» в `README.md`). Без них доступен справочный помощник. Telegram-бот настраивается на URL `/mini/`.

Если на сервере уже работает Nginx или другой сервис на 80/443, используйте имеющийся `compose.yaml` (API привязан к `127.0.0.1:8000`) и настройте ваш reverse proxy на этот адрес вместо запуска второго Caddy. В этом случае задайте переменные из `.env.example`, добавьте `POSTGRES_PASSWORD`, пропишите публичный `APP_ORIGIN` и передайте необходимые ключи в `compose.yaml`.

## Проверка архива

Рядом с ZIP находится `alfa_hack-vps.zip.sha256`:

```bash
sha256sum -c alfa_hack-vps.zip.sha256
```

Внутри папки также есть `MANIFEST.sha256` с хешами исходных файлов:

```bash
sha256sum -c MANIFEST.sha256
```

Прототип использует синтетические проверки реестров/БКИ и имитацию подписания и выдачи. Техническая конфигурация VPS подготовлена; запуск Docker-стека на вашем VPS выполняется приведёнными командами. Локальные проверки приложения описаны в README.
