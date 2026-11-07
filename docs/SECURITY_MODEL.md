# Security Model

## Security goals
- minimize sensitive data leaving the organization
- maintain auditability of all routing and policy actions
- prevent secret leakage in both directions
- isolate tenants and projects strongly

## Controls
- API key auth for MVP; JWT/SSO later
- prompt scanning before provider call
- response scanning before return
- masked logging by default
- encrypted credentials
- provider allow/deny policy
- region restrictions

## Sensitive data classes
- PII
- secrets
- financial/account identifiers
- source code and proprietary documents
- regulated domain data (health, legal, HR, payroll)
