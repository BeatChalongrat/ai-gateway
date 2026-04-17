# API Design

## Public endpoints
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/embeddings`
- `POST /v1/moderations`
- `GET /v1/models`
- `GET /v1/usage`
- `GET /health`

## Admin endpoints
- `GET /admin/tenants`
- `GET /admin/projects`
- `GET /admin/providers`
- `GET /admin/models`
- `GET /admin/policies`
- `GET /admin/requests`
- `GET /admin/audit-events`

## Request meta headers
- `Authorization: Bearer <project-api-key>`
- `X-Request-Id`
- `X-End-User-Id` optional
- `X-Policy-Profile` optional override if allowed
