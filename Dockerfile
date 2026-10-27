ARG NODE_IMAGE=public.ecr.aws/docker/library/node:22-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.14-slim-bookworm@sha256:9974e24551f9e7ca172cf284d85b8ab3e031d22d945f7026ede7a8279118c9f1
FROM ${NODE_IMAGE} AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS python-deps
WORKDIR /build
COPY backend/requirements.lock.txt ./
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.lock.txt

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TMPDIR=/tmp
WORKDIR /app
RUN groupadd --gid 10001 health && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent health && mkdir /data && chown 10001:10001 /data
COPY --from=python-deps /opt/venv /opt/venv
COPY backend/app/ /app/backend/app/
COPY --from=frontend /build/dist/ /app/frontend/dist/
COPY scripts/runtime-healthcheck.py /app/scripts/runtime-healthcheck.py
USER 10001:10001
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "32", "--no-access-log", "--timeout-graceful-shutdown", "5"]
