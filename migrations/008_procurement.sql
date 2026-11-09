-- Independent internal development documents; never supplier transmission.
CREATE TABLE procurement_documents (
    id uuid PRIMARY KEY,
    company_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('QUOTE','PR','PO','SUPPLY_CONFIRMATION','RECEIPT','IQC','CANCEL')),
    parent_id uuid REFERENCES procurement_documents(id),
    business_key text,
    lot_code text,
    quantity bigint NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    passed_qty bigint NOT NULL DEFAULT 0 CHECK (passed_qty >= 0),
    failed_qty bigint NOT NULL DEFAULT 0 CHECK (failed_qty >= 0),
    cancelled_by uuid REFERENCES procurement_documents(id),
    content jsonb NOT NULL,
    canonical_bytes bytea NOT NULL,
    payload_hash text NOT NULL,
    canonical_version text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    status text NOT NULL CHECK (status IN ('DRAFT','REVIEWED','APPROVED','POSTED')),
    created_by text NOT NULL,
    reviewed_by text,
    approved_by text,
    approved_version integer,
    approved_hash text,
    approval_policy_version integer,
    rejection_reason text,
    ledger_version bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX procurement_company ON procurement_documents(company_id, created_at);
CREATE INDEX procurement_parent ON procurement_documents(parent_id, kind, status);
CREATE UNIQUE INDEX procurement_business_reference ON procurement_documents(company_id, kind, business_key)
    WHERE business_key IS NOT NULL;
CREATE UNIQUE INDEX procurement_receipt_lot ON procurement_documents(company_id, lot_code)
    WHERE lot_code IS NOT NULL;
CREATE TABLE procurement_revisions (
    document_id uuid NOT NULL REFERENCES procurement_documents(id),
    version integer NOT NULL,
    content jsonb NOT NULL,
    canonical_bytes bytea NOT NULL,
    payload_hash text NOT NULL,
    actor_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(document_id, version)
);
CREATE TABLE procurement_audit (
    audit_id uuid PRIMARY KEY,
    company_id text NOT NULL,
    actor_id text NOT NULL,
    action text NOT NULL,
    document_id uuid NOT NULL REFERENCES procurement_documents(id),
    details jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE ledger_events ADD COLUMN procurement_document_id uuid REFERENCES procurement_documents(id);
ALTER TABLE ledger_events DROP CONSTRAINT ledger_event_document_kind;
ALTER TABLE ledger_events ADD CONSTRAINT ledger_event_document_kind CHECK (
    (event_type = 'OPENING_BALANCE_IMPORTED' AND document_id IS NULL AND bom_document_id IS NULL
        AND procurement_document_id IS NULL AND COALESCE(payload->>'record_type' = 'opening_balance', false))
    OR (event_type = 'EvidenceDocumentPosted' AND document_id IS NOT NULL AND bom_document_id IS NULL
        AND procurement_document_id IS NULL)
    OR (event_type = 'BomRegistered' AND document_id IS NULL AND bom_document_id IS NOT NULL
        AND procurement_document_id IS NULL AND COALESCE(payload->>'event_type' = 'BomRegistered', false))
    OR (event_type = 'ProcurementPosted' AND document_id IS NULL AND bom_document_id IS NULL
        AND procurement_document_id IS NOT NULL AND COALESCE(payload->>'event_type' = 'ProcurementPosted', false))
);
CREATE UNIQUE INDEX ledger_procurement_once ON ledger_events(procurement_document_id)
    WHERE procurement_document_id IS NOT NULL;
