-- Substitute application approval (T13): a reviewed and independently approved
-- quantity of an existing substitute candidate counted for the selected demand.
-- A demo application approval, not a manufacturing technical review.
ALTER TABLE procurement_documents DROP CONSTRAINT IF EXISTS procurement_documents_kind_check;
ALTER TABLE procurement_documents ADD CONSTRAINT procurement_documents_kind_check CHECK (kind IN
    ('QUOTE','PR','PO','SUPPLY_CONFIRMATION','RECEIPT','IQC','CANCEL','PRODUCTION','FG_RECEIPT','SHIPMENT','ARRIVAL','SCENARIO','COHORT','SUBSTITUTION'));
