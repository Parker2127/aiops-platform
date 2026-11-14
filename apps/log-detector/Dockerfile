# syntax=docker/dockerfile:1
# Build context: repo root, so the shared ml/features.py can be copied in.

FROM python:3.12-slim AS build
WORKDIR /build
COPY apps/log-detector/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /install /usr/local
WORKDIR /app
# Same feature code the model was trained with - train/serve parity.
COPY ml/features.py ./features.py
COPY apps/log-detector/src/log_detector ./log_detector
USER 10001
EXPOSE 8080
CMD ["python", "-m", "log_detector.main"]
