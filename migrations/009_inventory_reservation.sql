-- Synthetic other-demand reservations of current stock (DEC-H47).
-- A reservation never moves, consumes or receives stock. It only increases the
-- other-demand reserved quantity of one existing LOT in one committed event.
CREATE TABLE inventory_reservations (
    id uuid PRIMARY KEY,
    company_id text NOT NULL,
    run_id text NOT NULL,
    item_id text NOT NULL,
    lot_id text NOT NULL,
    unit text NOT NULL,
    quantity bigint NOT NULL CHECK (quantity > 0),
    demand_ref text NOT NULL,
    basis_ledger_version bigint NOT NULL CHECK (basis_ledger_version >= 1),
    ledger_version bigint NOT NULL UNIQUE,
    payload_hash text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX inventory_reservations_company ON inventory_reservations(company_id, run_id, ledger_version);
CREATE TABLE inventory_reservation_audit (
    audit_id uuid PRIMARY KEY,
    company_id text NOT NULL,
    actor_id text NOT NULL,
    action text NOT NULL,
    reservation_id uuid NOT NULL REFERENCES inventory_reservations(id),
    details jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE ledger_events ADD COLUMN reservation_id uuid REFERENCES inventory_reservations(id);
ALTER TABLE ledger_events DROP CONSTRAINT ledger_event_document_kind;
ALTER TABLE ledger_events ADD CONSTRAINT ledger_event_document_kind CHECK (
    (event_type = 'OPENING_BALANCE_IMPORTED' AND document_id IS NULL AND bom_document_id IS NULL
        AND procurement_document_id IS NULL AND reservation_id IS NULL
        AND COALESCE(payload->>'record_type' = 'opening_balance', false))
    OR (event_type = 'EvidenceDocumentPosted' AND document_id IS NOT NULL AND bom_document_id IS NULL
        AND procurement_document_id IS NULL AND reservation_id IS NULL)
    OR (event_type = 'BomRegistered' AND document_id IS NULL AND bom_document_id IS NOT NULL
        AND procurement_document_id IS NULL AND reservation_id IS NULL
        AND COALESCE(payload->>'event_type' = 'BomRegistered', false))
    OR (event_type = 'ProcurementPosted' AND document_id IS NULL AND bom_document_id IS NULL
        AND procurement_document_id IS NOT NULL AND reservation_id IS NULL
        AND COALESCE(payload->>'event_type' = 'ProcurementPosted', false))
    OR (event_type = 'InventoryReserved' AND document_id IS NULL AND bom_document_id IS NULL
        AND procurement_document_id IS NULL AND reservation_id IS NOT NULL
        AND COALESCE(payload->>'event_type' = 'InventoryReserved', false))
);
CREATE UNIQUE INDEX ledger_reservation_once ON ledger_events(reservation_id)
    WHERE reservation_id IS NOT NULL;
