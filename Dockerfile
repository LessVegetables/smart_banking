FROM node:22-alpine AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY apps/web ./apps/web
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt
COPY apps/api ./apps/api
COPY apps/ml ./apps/ml
COPY apps/sources ./apps/sources
COPY apps/world ./apps/world
COPY config ./config
COPY alembic.ini ./alembic.ini
COPY run.py ./
COPY --from=frontend /build/dist ./dist
# The default world (seed 42, 5000 companies), written once at build time, so the API and
# the worker read it instead of regenerating it (~5 s) on their first v2 request. Demo
# personas (apps/world/demo) are picked from this world. SIMPLIFICATION: while apps/world/out exists,
# WORLD_SEED / WORLD_N / WORLD_AS_OF have no effect (apps/sources/service.py, load_world).
RUN python -m apps.world
COPY THIRD_PARTY_NOTICES.md ./
RUN useradd --create-home appuser && mkdir -p /app/data && chown appuser:appuser /app/data
USER appuser
EXPOSE 8000
CMD ["python", "run.py"]
