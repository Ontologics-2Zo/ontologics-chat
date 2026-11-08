-- Read-only workflow control state; separate from business approval/ledger data.
CREATE TABLE agent_executions (
    execution_id uuid PRIMARY KEY,
    thread_id uuid NOT NULL UNIQUE,
    company_id text NOT NULL,
    owner_actor_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    request jsonb NOT NULL,
    pinned_context jsonb NOT NULL,
    policy_version integer NOT NULL,
    workflow_version text NOT NULL,
    checkpoint_version text NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    status text NOT NULL DEFAULT 'CREATED' CHECK (status IN ('CREATED','RUNNING','WAITING','COMPLETED','FAILED','CANCELLED')),
    capability text,
    segment_count integer NOT NULL DEFAULT 0,
    tool_calls integer NOT NULL DEFAULT 0,
    max_tool_calls integer NOT NULL CHECK (max_tool_calls > 0),
    deadline_at timestamptz NOT NULL,
    cancel_requested boolean NOT NULL DEFAULT false,
    lease_token uuid,
    lease_until timestamptz,
    result_ref uuid,
    pending_resume jsonb,
    interrupt jsonb,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(company_id, owner_actor_id, idempotency_key)
);
CREATE TABLE agent_events (
    sequence bigserial PRIMARY KEY,
    execution_id uuid NOT NULL REFERENCES agent_executions(execution_id),
    segment_id uuid,
    stage text NOT NULL,
    status text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_events_execution ON agent_events(execution_id, sequence);
CREATE TABLE agent_artifacts (
    artifact_id uuid PRIMARY KEY,
    execution_id uuid NOT NULL REFERENCES agent_executions(execution_id),
    artifact_key text NOT NULL,
    content jsonb NOT NULL,
    content_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(execution_id, artifact_key)
);
CREATE TABLE agent_resume_requests (
    execution_id uuid NOT NULL REFERENCES agent_executions(execution_id),
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(execution_id, idempotency_key)
);
