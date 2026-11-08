-- Immutable raw source evidence and separate registration approval. No stock seed.
CREATE TABLE bom_sources (
    company_id text NOT NULL,
    source_hash text NOT NULL,
    source_id text NOT NULL,
    filename text NOT NULL,
    format text NOT NULL,
    raw_bytes bytea NOT NULL,
    parsed jsonb NOT NULL,
    parser_version text NOT NULL,
    byte_count integer NOT NULL CHECK (byte_count > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(company_id, source_hash)
);
CREATE TABLE bom_documents (
    id uuid PRIMARY KEY,
    company_id text NOT NULL,
    source_hash text NOT NULL,
    configuration_id text NOT NULL,
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
    review_record jsonb,
    ledger_version bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(company_id, source_hash) REFERENCES bom_sources(company_id, source_hash)
);
CREATE INDEX bom_documents_company ON bom_documents(company_id, created_at);
CREATE TABLE bom_revisions (
    document_id uuid NOT NULL REFERENCES bom_documents(id),
    version integer NOT NULL,
    content jsonb NOT NULL,
    canonical_bytes bytea NOT NULL,
    payload_hash text NOT NULL,
    actor_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(document_id, version)
);
CREATE TABLE bom_audit (
    audit_id uuid PRIMARY KEY,
    company_id text NOT NULL,
    actor_id text NOT NULL,
    action text NOT NULL,
    document_id uuid NOT NULL REFERENCES bom_documents(id),
    details jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE ledger_events ADD COLUMN bom_document_id uuid REFERENCES bom_documents(id);
ALTER TABLE ledger_events DROP CONSTRAINT ledger_event_document_kind;
ALTER TABLE ledger_events ADD CONSTRAINT ledger_event_document_kind CHECK (
    (event_type = 'OPENING_BALANCE_IMPORTED' AND document_id IS NULL AND bom_document_id IS NULL
        AND COALESCE(payload->>'record_type' = 'opening_balance', false))
    OR (event_type = 'EvidenceDocumentPosted' AND document_id IS NOT NULL AND bom_document_id IS NULL)
    OR (event_type = 'BomRegistered' AND document_id IS NULL AND bom_document_id IS NOT NULL
        AND COALESCE(payload->>'event_type' = 'BomRegistered', false))
);
CREATE UNIQUE INDEX ledger_bom_registration_once ON ledger_events(bom_document_id, event_type)
    WHERE bom_document_id IS NOT NULL;
