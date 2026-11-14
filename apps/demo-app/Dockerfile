# syntax=docker/dockerfile:1
# Build context: repo root (see scripts/build-push.sh)

FROM python:3.12-slim AS build
WORKDIR /build
COPY apps/demo-app/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /install /usr/local
WORKDIR /app
COPY apps/demo-app/src/demo_app ./demo_app
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
# One worker so chaos state is shared; threads because /work is sleep-bound.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "16", "--worker-class", "gthread", "demo_app.wsgi:app"]
