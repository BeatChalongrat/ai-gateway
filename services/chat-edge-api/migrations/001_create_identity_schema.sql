-- 001_create_identity_schema.sql
--
-- Identity-domain tables owned by chat-edge-api. Tenants and users
-- are the canonical source of truth for the web-chat product surface.
-- Existing gateway-api / admin-ui have their own user tables — this
-- service does not share them; the two surfaces sync via the
-- service-to-service JWT contract.

CREATE SCHEMA IF NOT EXISTS identity;

CREATE TABLE identity.tenants (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    slug       TEXT UNIQUE NOT NULL,
    is_personal BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE identity.users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES identity.tenants(id),
    email         TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user'
                  CHECK (role IN ('user', 'admin', 'super_admin')),
    is_active     BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_tenant ON identity.users(tenant_id);
CREATE INDEX idx_users_email  ON identity.users(email);

CREATE TABLE identity.invitation_tokens (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES identity.tenants(id),
    token      TEXT UNIQUE NOT NULL,
    created_by UUID NOT NULL REFERENCES identity.users(id),
    used_by    UUID REFERENCES identity.users(id),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Refresh-token rotation table.
--
-- ``token_hash`` stores SHA-256 of the opaque token so a database
-- dump alone cannot replay a session. ``family_id`` is the rotation
-- anchor: a single login mints family X with seed token T1; refresh
-- revokes T1 and issues T2 under the same family; "logout
-- everywhere" revokes the whole family. Stolen-token reuse detection
-- = "any token in the family is used after revocation" → revoke
-- the family.
CREATE TABLE identity.refresh_tokens (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES identity.users(id),
    token_hash TEXT UNIQUE NOT NULL,
    family_id  UUID NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_refresh_tokens_user   ON identity.refresh_tokens(user_id);
CREATE INDEX idx_refresh_tokens_family ON identity.refresh_tokens(family_id);
