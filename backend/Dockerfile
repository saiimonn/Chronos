FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Local-production image (docker-compose.prod.yml passes the frontend source
# as the "frontend" build context). FastAPI serves the built SPA itself.
FROM node:24-slim AS frontend-build

WORKDIR /frontend

COPY --from=frontend package.json package-lock.json ./
RUN npm ci

COPY --from=frontend . .
RUN npm run build

FROM base AS production

COPY --from=frontend-build /frontend/dist /app/frontend_dist
ENV FRONTEND_DIST_DIR=/app/frontend_dist

# No --host: uvicorn reads UVICORN_HOST and otherwise binds 127.0.0.1.
CMD ["uvicorn", "app.main:app", "--port", "8000", "--no-proxy-headers"]

# Default (development) image, used by docker-compose.yml.
FROM base AS development

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
