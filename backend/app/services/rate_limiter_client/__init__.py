"""gRPC client for the Phase-3b Rust ``rate-limiter`` sidecar.

The ``.proto`` source lives at ``rate-limiter/proto/ratelimit.proto``;
both the Rust server and these Python stubs are regenerated with
``scripts/proto_codegen.sh`` (commit the generated files so live
clusters don't need a build-time protoc).
"""

from .client import (  # noqa: F401
    RateLimiterClient,
    RateLimiterUnavailable,
    proto_to_dict,
)
