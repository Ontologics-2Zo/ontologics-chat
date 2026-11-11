-- Baseline boards without serials (T10): finished-goods receipt, shipment and
-- arrival of the opening-balance cohort by quantity, never beyond what the
-- opening balance recorded at each stage.
ALTER TABLE procurement_documents DROP CONSTRAINT IF EXISTS procurement_documents_kind_check;
ALTER TABLE procurement_documents ADD CONSTRAINT procurement_documents_kind_check CHECK (kind IN
    ('QUOTE','PR','PO','SUPPLY_CONFIRMATION','RECEIPT','IQC','CANCEL','PRODUCTION','FG_RECEIPT','SHIPMENT','ARRIVAL','SCENARIO','COHORT'));
