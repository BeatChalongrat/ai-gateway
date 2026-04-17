# System Design

## Services
- API service
- routing service
- policy engine
- DLP/security service
- provider adapter layer
- usage/cost service
- audit/event service
- admin console

## Key dependencies
- PostgreSQL for metadata and logs
- Redis for caching, rate limit, transient state
- object storage for future prompt artifacts, reports, and export files

## Non-functional goals
- low-latency pass-through for simple requests
- policy-first execution
- deterministic logging/audit behavior
- tenant isolation
- adapter abstraction for provider portability
