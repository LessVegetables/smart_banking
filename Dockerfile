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
RUN useradd --create-home appuser && mkdir -p /app/data && chown appuser:appuser /app/data
USER appuser
EXPOSE 8000
CMD ["python", "run.py"]
