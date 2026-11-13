# Security model

Workspace UX follow-up (2026-11-13): shared Web Chat/Electron file tabs, selection rewrites, recoverable drafts with Undo/Redo, review-before-accept AI proposals, checkpointed jobs, a cross-chat file library, geometry controls and a 12-template gallery are described in [Artifact workspace](ARTIFACT_WORKSPACE.md). Model/provider tests remain mocked.

Visual editor boundary (2026-11-13): typed sandbox operations never execute model-authored code. AI corrections require re-rendered approval, immutable revision CAS and exact editable Office text preservation. Raw page-image egress requires tenant approval through `media_pii_egress`; PDF editing is not certified redaction. See [scope and boundaries](VISUAL_EDITOR.md).

Editor/media boundary update (2026-11-13): editor input and lineage are tenant/user scoped; agents pin conversation IDs; immutable saves use a row lock and fresh child check. Sandbox editing has bounded requests/archives and no network. Video job list/status/output/cancel enforce end-user ownership; agents cannot confirm paid jobs. See [implementation limits](OFFICE_EDITOR.md).

Media follow-up: reference-image inputs are bounded, decoded/verified and conversation-scoped before external transfer. Tenant `media_pii_egress=block` refuses raw images even when prompt DLP is off; text scanning does not inspect pixels. Premium video has an owner-only confirmation transition before queue dispatch. See [the detailed contracts](OFFICE_MEDIA_PARITY.md).

Source review: 2026-11-12. Deployment settings and effectiveness require
environment-specific checks; source presence is not compliance certification.

## Identity and authorization

Public model calls resolve project/API-key or supported service identity.
Portal sessions/JWTs are separate from admin cookie/session RBAC. Chat-edge
validates Portal JWTs and applies tenant/user ownership. Admin authentication
and SSO are implemented; “JWT/SSO later” is obsolete.

A file UUID is not authorization. File operations check caller ownership and,
when a conversation is named, membership. See [Admin API](ADMIN_API.md) and
[SSO deployment](SSO-GO-LIVE.md).

## Egress and storage

Standard completion text passes through detection, policy, masking, dispatch
and restoration. Text DLP does not mask image pixels. Five raw-egress channels
and trusted-caller/in-house decisions are documented in [DLP architecture](DLP_ARCHITECTURE.md).
Do not promise that no raw data can leave.

Original uploads, conversations, encrypted traces and local caches are distinct
from privacy-safe DLP audit events. Apply [retention](DATA_RETENTION_PLAN.md),
[secret operations](ops/README-secrets.md) and [user deletion/export](ops/user-data.md).

## Document execution

Server Python has no network, read-only inputs/root filesystem, non-root
execution, dropped capabilities, seccomp and resource limits. Production
gVisor selection/refusal is covered in [sandbox deployment](DEPLOY_SANDBOX.md).
The launcher's Docker socket is privileged infrastructure; model code receives
neither that socket nor service credentials.

Later transmission of returned text/images to a model is a separate boundary.
Sandbox isolation does not itself prove masking or image redaction.

The optional Office converter is a separate non-root, read-only, resource-capped
service on an internal network. Chat-edge permits only internal converter
addresses, checks resolved IPs, bypasses environment proxies and refuses
redirects. It passes document bytes, never its credentials. Workbook archive
inspection is size-bounded; automatic recalc refuses external link parts and
macros. See [Office deployment](OFFICE_TEMPLATES.md#deployment).

## Local files

Electron owns bridge validation and permissions. Grants are host state, not
model arguments. Import/save tools require granted paths, reject links/device
paths, cap transfers at 25 MiB, verify conversation membership before download
and create outputs exclusively. Upload consent says a copy goes to the server.

Existing shell tools run on the host under their own permission policy; the
Electron host is not a VM. See [Office contracts](OFFICE_WORKFLOWS.md).
Tests establish specific controls, not universal protection against malicious
documents or fidelity of every Office format.
