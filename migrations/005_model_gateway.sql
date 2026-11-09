-- Model accounting is separate from business facts and workflow checkpoints.
CREATE TABLE model_budget_accounts (
    budget_id text PRIMARY KEY,
    limit_usd numeric NOT NULL CHECK (limit_usd > 0 AND limit_usd <= 10),
    prior_spend_usd numeric CHECK (prior_spend_usd >= 0),
    authorization_ref text,
    blocked boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (prior_spend_usd IS NULL OR authorization_ref IS NOT NULL)
);

CREATE TABLE model_executions (
    execution_id text PRIMARY KEY,
    budget_id text NOT NULL REFERENCES model_budget_accounts(budget_id),
    company_id text NOT NULL,
    actor_id text NOT NULL,
    scope_hash text NOT NULL,
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count BETWEEN 0 AND 1),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE model_attempts (
    attempt_id text PRIMARY KEY,
    execution_id text NOT NULL REFERENCES model_executions(execution_id),
    call_id text NOT NULL,
    retry_of text REFERENCES model_attempts(attempt_id),
    request_hash text NOT NULL,
    execution_mode text NOT NULL CHECK (execution_mode IN ('fake', 'live')),
    status text NOT NULL CHECK (status IN ('reserved', 'dispatched', 'completed', 'failed', 'outcome_unknown')),
    requested_provider text NOT NULL,
    requested_model text NOT NULL,
    actual_provider text,
    actual_model text,
    provider_request_id text,
    reserved_usd numeric NOT NULL CHECK (reserved_usd >= 0),
    held_usd numeric NOT NULL CHECK (held_usd >= 0),
    cost_usd numeric CHECK (cost_usd >= 0),
    usage jsonb NOT NULL DEFAULT '{}',
    result jsonb,
    error_code text,
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    dispatched_at timestamptz,
    finished_at timestamptz
);
CREATE UNIQUE INDEX model_initial_call ON model_attempts(execution_id, call_id) WHERE retry_of IS NULL;
CREATE UNIQUE INDEX model_retry_call ON model_attempts(retry_of) WHERE retry_of IS NOT NULL;
CREATE INDEX model_attempt_execution ON model_attempts(execution_id);
