FROM python:3.12-slim AS build

WORKDIR /src
RUN pip install --no-cache-dir mkdocs-material==9.7.7

COPY docs-site/mkdocs.yml ./mkdocs.yml
COPY docs-site/index.md ./index.md
COPY README.md ARCHITECTURE.md VPS.md AGENTS.md THIRD_PARTY_NOTICES.md ./
COPY docs ./docs
COPY deploy/Caddyfile ./deploy/
COPY deploy/nginx/*.conf ./deploy/nginx/
COPY apps/api/*.md ./apps/api/
COPY apps/api/features/README.md ./apps/api/features/
COPY apps/ml/README.md apps/ml/scorecard_v1.json ./apps/ml/
COPY apps/mini/README.md ./apps/mini/
COPY apps/sources/README.md ./apps/sources/
COPY apps/web/README.md ./apps/web/
COPY apps/world/*.md ./apps/world/
COPY config/*.json ./config/
COPY misc/HACKATHON.md ./misc/
COPY misc/Альфа_Хак_Умный_кредитный_кабинет.pdf ./misc/

RUN mkdocs build --clean --site-dir /site

FROM nginx:stable-alpine
COPY --from=build /site/ /usr/share/nginx/html/
EXPOSE 80
