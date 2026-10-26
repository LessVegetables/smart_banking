FROM python:3.12-slim AS build

WORKDIR /src
RUN pip install --no-cache-dir mkdocs-material==9.7.7

COPY docs-site/mkdocs.yml docs-site/hooks.py ./

# MkDocs refuses a docs_dir that contains mkdocs.yml, so the pages go into a child folder (docs_dir: content).
WORKDIR /src/content
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
# Glob, not the full name: macOS stores "й" decomposed (NFD), and an exact COPY misses it there.
COPY misc/Альфа_Хак_*.pdf ./misc/

WORKDIR /src
RUN mkdocs build --clean --site-dir /site

FROM nginx:stable-alpine
COPY --from=build /site/ /usr/share/nginx/html/
EXPOSE 80
