-- BOM reviewer outcomes (DEC-H48): return a draft for changes, or reject it.
-- Neither outcome posts anything or copies decisions; history stays in audit.
ALTER TABLE bom_documents DROP CONSTRAINT IF EXISTS bom_documents_status_check;
ALTER TABLE bom_documents ADD CONSTRAINT bom_documents_status_check
    CHECK (status IN ('DRAFT', 'REVIEWED', 'APPROVED', 'POSTED', 'REJECTED'));
ALTER TABLE bom_documents ADD COLUMN change_request jsonb;
ALTER TABLE bom_documents ADD COLUMN rejection jsonb;
