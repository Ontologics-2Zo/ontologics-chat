-- Generic evidence document proof. No BOM, inventory, purchase, or live operations seed.
CREATE TABLE IF NOT EXISTS documents (
    id uuid PRIMARY KEY,
    company_id text NOT NULL,
    kind text NOT NULL CHECK (kind = 'evidence_note'),
    content jsonb NOT NULL,
    canonical_bytes bytea NOT NULL,
    payload_hash text NOT NULL,
    canonical_version text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    status text NOT NULL CHECK (status IN ('DRAFT', 'REVIEWED', 'APPROVED', 'POSTED')),
    created_by text NOT NULL,
    reviewed_by text,
    approved_by text,
    approved_version integer,
    approved_hash text,
    approval_policy_version integer,
    ledger_version bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_company ON documents(company_id, created_at);
CREATE TABLE IF NOT EXISTS document_revisions (
    document_id uuid NOT NULL REFERENCES documents(id),
    version integer NOT NULL,
    content jsonb NOT NULL,
    canonical_bytes bytea NOT NULL,
    payload_hash text NOT NULL,
    canonical_version text NOT NULL,
    actor_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(document_id, version)
);
CREATE TABLE IF NOT EXISTS operations (
    company_id text NOT NULL,
    actor_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    operation_id uuid NOT NULL UNIQUE,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(company_id, actor_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS ledger_state (
    company_id text PRIMARY KEY,
    version bigint NOT NULL DEFAULT 0 CHECK (version >= 0)
);
CREATE TABLE IF NOT EXISTS ledger_events (
    event_id uuid PRIMARY KEY,
    company_id text NOT NULL,
    ledger_version bigint NOT NULL,
    document_id uuid NOT NULL REFERENCES documents(id),
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    actor_id text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(company_id, ledger_version),
    UNIQUE(document_id, event_type)
);
CREATE TABLE IF NOT EXISTS audit_events (
    audit_id uuid PRIMARY KEY,
    company_id text NOT NULL,
    actor_id text NOT NULL,
    action text NOT NULL,
    document_id uuid NOT NULL REFERENCES documents(id),
    details jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS outbox (
    event_id uuid PRIMARY KEY REFERENCES ledger_events(event_id),
    company_id text NOT NULL,
    ledger_version bigint NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    recorded_at timestamptz NOT NULL DEFAULT now()
);
