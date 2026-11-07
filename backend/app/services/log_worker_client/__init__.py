"""Generated gRPC stubs + thin Python client for the Go log-worker sidecar.

The ``.proto`` source lives at ``log-worker/proto/logworker.proto``;
both the Go server and these Python stubs are regenerated with
``protoc`` from it (commit the generated files so live
clusters don't need a build-time protoc).
"""

from .client import (  # noqa: F401
    LogWorkerClient,
    LogWorkerUnavailable,
    record_to_proto,
)
