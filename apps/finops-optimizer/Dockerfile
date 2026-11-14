# syntax=docker/dockerfile:1
# Build context: repo root (see scripts/build-push.sh)

FROM python:3.12-slim AS build
WORKDIR /build
COPY apps/finops-optimizer/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /install /usr/local
WORKDIR /app
COPY apps/finops-optimizer/src/finops_optimizer ./finops_optimizer
USER 10001
CMD ["python", "-m", "finops_optimizer.main"]
