-- A READY projection is replaced only when the schema (ontology) version changes.
-- The replaced projection is kept here unchanged, so earlier answers that cite it
-- can still be traced to the exact schema they were checked with.
CREATE TABLE service_projection_history (
    company_id text NOT NULL,
    run_id text NOT NULL,
    ledger_version bigint NOT NULL,
    ontology_version text NOT NULL,
    projection jsonb NOT NULL,
    rdf_turtle text,
    replaced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, run_id, ledger_version, ontology_version),
    FOREIGN KEY (company_id, run_id, ledger_version) REFERENCES service_snapshots (company_id, run_id, ledger_version)
);
