# Scale, reliability and compliance boundaries

Source review: 2026-11-12. Deployment capacity and regulatory compliance are
not established by the presence of abstractions or manifests.

## Implemented foundations

- Redis-backed cache and coordination are implemented in the backend; the old
  in-memory-only cache description is obsolete.
- Optional Rust/Go sidecars accelerate DLP, rate limiting and log writing.
  Configuration selects their runtime mode and fallback behavior.
- Request logs are partitioned. Retention has scheduled strip/archive/expiry
  ordering and separate chat/account/file cleanup mechanisms.
- Agent Market has a durable PostgreSQL queue and a separate runner. Ordinary
  web streaming turns and desktop turns have different lifecycles.
- Monitoring, health probes, alerts, backups, scaling overlays, Kubernetes and
  Terraform assets exist in the repository. Their presence is not proof that a
  particular environment runs them.

## Operator references

| Concern | Current reference |
|---|---|
| Enable scaling | [Activation runbook](SCALING_ACTIVATION_RUNBOOK.md), [scaling](ops/scaling.md) |
| Host capacity | [Host memory](HOST_MEMORY.md) |
| Retention/deletion | [Retention](DATA_RETENTION_PLAN.md), [user data](ops/user-data.md) |
| Health/alerts | [Monitoring](ops/monitoring.md), [observability](OBSERVABILITY.md) |
| Sandbox runtime | [Sandbox deployment](DEPLOY_SANDBOX.md) |
| Identity/policy | [Security model](SECURITY_MODEL.md), [SSO](SSO-GO-LIVE.md) |

Audit-chain helpers are technical integrity primitives, not a legal guarantee
of tamper-proof storage. Regional routing rules do not themselves establish
data residency. Google SSO does not imply arbitrary organizational IdP/SCIM
support. Test those requirements against the intended deployment and contracts.
