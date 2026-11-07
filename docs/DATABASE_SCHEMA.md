# Database Schema (Conceptual)

## Core tables
- tenants
- users
- projects
- api_keys
- providers
- provider_credentials
- models
- policy_profiles
- routing_rules
- request_logs
- usage_records
- audit_events
- alerts

## Notes
Use Alembic migrations. Consider table partitioning later for high-volume request logs.
