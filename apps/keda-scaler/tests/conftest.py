"""Generate the gRPC stubs from the .proto (same as the Dockerfile does) before tests import them."""

from pathlib import Path

from grpc_tools import protoc

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src"

if not (SRC / "externalscaler_pb2_grpc.py").exists():
    rc = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{APP / 'proto'}",
            f"--python_out={SRC}",
            f"--grpc_python_out={SRC}",
            str(APP / "proto" / "externalscaler.proto"),
        ]
    )
    assert rc == 0, "protoc failed"
