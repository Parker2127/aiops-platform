# syntax=docker/dockerfile:1
# Build context: repo root (see scripts/build-push.sh)

FROM python:3.12-slim AS build
WORKDIR /build
COPY apps/keda-scaler/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
# Generate stubs with the same grpc/protobuf versions the runtime uses
# (a stub/runtime mismatch is the classic "it worked locally" gRPC failure).
RUN pip install --no-cache-dir grpcio-tools==1.85.1 protobuf==7.36.2
COPY apps/keda-scaler/proto ./proto
RUN mkdir gen && python -m grpc_tools.protoc -Iproto --python_out=gen --grpc_python_out=gen proto/externalscaler.proto

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /install /usr/local
WORKDIR /app
COPY --from=build /build/gen/ ./
COPY apps/keda-scaler/src/keda_scaler ./keda_scaler
USER 10001
EXPOSE 4350 8080
CMD ["python", "-m", "keda_scaler.server"]
