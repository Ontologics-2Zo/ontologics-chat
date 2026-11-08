-- Opening balances are explicit migration events, never invented history.
ALTER TABLE ledger_events ALTER COLUMN document_id DROP NOT NULL;
ALTER TABLE ledger_events ADD CONSTRAINT ledger_event_document_kind CHECK (
    (event_type = 'OPENING_BALANCE_IMPORTED' AND document_id IS NULL
        AND COALESCE(payload->>'record_type' = 'opening_balance', false))
    OR (event_type = 'EvidenceDocumentPosted' AND document_id IS NOT NULL)
);
CREATE TABLE service_snapshots (
    company_id text NOT NULL,
    run_id text NOT NULL,
    ledger_version bigint NOT NULL,
    source_hash text NOT NULL,
    content_hash text NOT NULL,
    payload jsonb NOT NULL,
    projection jsonb,
    rdf_turtle text,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, run_id, ledger_version)
);
CREATE TABLE opening_imports (
    company_id text NOT NULL,
    run_id text NOT NULL,
    source_hash text NOT NULL,
    content_hash text NOT NULL,
    ledger_version bigint NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, run_id)
);
