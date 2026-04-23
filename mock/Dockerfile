# Containerised mock LLM provider for the perf baseline.
#
# Why this exists: on Windows the host-run uvicorn process saturates at
# ~50 RPS because asyncio on Windows picks the ProactorEventLoop and a
# single worker has no h2-style offload. This image runs uvicorn with
# uvloop + httptools + 4 workers on a Linux container, which empirically
# sustains 50 RPS with room to spare. See perf/sanity/B50/check_result.md
# for the failure mode that motivated this.
#
# Build:
#   docker compose -f docker-compose.yml -f perf/docker-compose.perf.yml \\
#       build mock-provider
# Run:
#   docker compose -f docker-compose.yml -f perf/docker-compose.perf.yml \\
#       up -d mock-provider
FROM python:3.11-slim

WORKDIR /app

# Install runtime deps. uvicorn[standard] pulls in uvloop + httptools +
# websockets + watchfiles; we only need the first two but the extra has
# no runtime cost at 4-worker scale.
RUN pip install --no-cache-dir \
        fastapi==0.115.0 \
        "uvicorn[standard]==0.30.6" \
        pyyaml==6.0.2

COPY server.py latency_profile.yaml /app/

EXPOSE 9090

# uvloop + httptools runtime; 4 workers to match the Windows-host
# saturation fix. The CMD is explicit rather than shell-expanded so
# signals propagate cleanly (no sh wrapper).
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9090", "--workers", "4", "--loop", "uvloop", "--http", "httptools"]
