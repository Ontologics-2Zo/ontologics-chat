CREATE TABLE identity_accounts (
    actor_id text PRIMARY KEY,
    dev_profile_id text NOT NULL UNIQUE,
    tenant_id text NOT NULL,
    label text NOT NULL,
    site_scope text[] NOT NULL,
    permissions text[] NOT NULL,
    policy_version integer NOT NULL DEFAULT 1 CHECK (policy_version > 0),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE identity_sessions (
    session_ref uuid PRIMARY KEY,
    token_hash text NOT NULL UNIQUE,
    csrf_hash text NOT NULL,
    actor_id text NOT NULL REFERENCES identity_accounts(actor_id),
    identity_revision uuid NOT NULL UNIQUE,
    policy_version integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    write_in_progress boolean NOT NULL DEFAULT false
);
CREATE INDEX identity_sessions_actor ON identity_sessions(actor_id);

CREATE TABLE identity_audit (
    audit_id uuid PRIMARY KEY,
    event_type text NOT NULL,
    actor_id text NOT NULL,
    previous_actor_id text,
    tenant_id text NOT NULL,
    session_ref uuid NOT NULL,
    identity_revision uuid NOT NULL,
    policy_version integer NOT NULL,
    auth_source text NOT NULL CHECK (auth_source = 'dev_switch'),
    recorded_at timestamptz NOT NULL DEFAULT now()
);
