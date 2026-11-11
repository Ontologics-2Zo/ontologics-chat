-- What-if schedule scenario (T12): assumed inspection, capacity, shipping and
-- transit durations for one delivery round, kept as a separately approved
-- document and projected as ScenarioRun/ScenarioProfile/ScenarioAssumption.
ALTER TABLE procurement_documents DROP CONSTRAINT IF EXISTS procurement_documents_kind_check;
ALTER TABLE procurement_documents ADD CONSTRAINT procurement_documents_kind_check CHECK (kind IN
    ('QUOTE','PR','PO','SUPPLY_CONFIRMATION','RECEIPT','IQC','CANCEL','PRODUCTION','FG_RECEIPT','SHIPMENT','ARRIVAL','SCENARIO'));
