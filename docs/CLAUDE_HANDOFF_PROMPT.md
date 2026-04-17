# Claude Handoff Prompt

Use this starter repo as the base implementation scaffold.
Your job is to continue it toward a production-grade Secure AI Gateway.

Priority order:
1. implement provider adapters and normalized response mapping
2. implement request orchestration flow end-to-end
3. implement policy engine v1 with allow/deny/mask decisions
4. implement DLP/PII scanner v1 and masked logging
5. wire PostgreSQL repositories and admin APIs
6. implement admin UI pages against the backend
7. add tests for routing, policy, DLP, and audit logging

Rules:
- do not break public API shapes already present
- keep internal normalized schemas provider-agnostic
- keep code modular and typed
- add tests with every behavior change
- prefer placeholders over fake completeness if something is not implemented yet
