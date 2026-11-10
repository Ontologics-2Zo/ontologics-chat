-- Production, finished-goods receipt, shipment and arrival records (DEC-H49).
-- Same exact-version review/approval/post as internal purchasing documents.
ALTER TABLE procurement_documents DROP CONSTRAINT IF EXISTS procurement_documents_kind_check;
ALTER TABLE procurement_documents ADD CONSTRAINT procurement_documents_kind_check CHECK (kind IN
    ('QUOTE','PR','PO','SUPPLY_CONFIRMATION','RECEIPT','IQC','CANCEL','PRODUCTION','FG_RECEIPT','SHIPMENT','ARRIVAL'));
