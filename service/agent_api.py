"""Authenticated execution API. Progress events never represent ledger commits."""

import json

from fastapi import Request
from fastapi.responses import Response

from service.agent_contracts import AgentRequest, AgentResume, AgentCancel
from service.errors import ServiceError


def mount_agent_api(app, principal, workflow, model_summary, on_answer=None):
    def enabled():
        if workflow is None:
            raise ServiceError("AGENT_DISABLED", 503)
        return workflow

    def result(state):
        return {**state, "model": model_summary(state["execution_id"])}

    def answered(actor, state):
        # Same notice the deterministic query sends; the hook never alters the result.
        if on_answer is not None and state.get("status") == "COMPLETED" and state.get("result"):
            on_answer(actor, state["result"])
        return result(state)

    @app.post("/api/mks2/agent/executions")
    def create(request: Request, body: AgentRequest):
        return result(enabled().create(principal(request, write=True), body))

    @app.post("/api/mks2/agent/executions/{execution_id}/run")
    def run(request: Request, execution_id: str):
        actor = principal(request, write=True)
        return answered(actor, enabled().run(actor, execution_id))

    @app.post("/api/mks2/agent/executions/{execution_id}/summary")
    def summary(request: Request, execution_id: str):
        # Spends model budget, so it is a write-scoped request like run.
        return {"execution_id": execution_id,
                "ai_summary": enabled().summarize(principal(request, write=True), execution_id),
                "model": model_summary(execution_id)}

    @app.get("/api/mks2/agent/executions/{execution_id}")
    def status(request: Request, execution_id: str):
        return result(enabled().status(principal(request), execution_id))

    @app.post("/api/mks2/agent/executions/{execution_id}/resume")
    def resume(request: Request, execution_id: str, body: AgentResume):
        actor = principal(request, write=True)
        return answered(actor, enabled().resume(actor, execution_id, body))

    @app.post("/api/mks2/agent/executions/{execution_id}/cancel")
    def cancel(request: Request, execution_id: str, body: AgentCancel):
        return result(enabled().cancel(principal(request, write=True), execution_id, body.expected_revision))

    @app.get("/api/mks2/agent/executions/{execution_id}/events")
    def events(request: Request, execution_id: str, after: int = 0):
        if after < 0:
            raise ServiceError("INVALID_CURSOR", 422)
        # A bounded SSE page releases the DB connection and reauthorizes each
        # reconnect. The browser advances the server sequence cursor.
        rows = enabled().events(principal(request), execution_id, after)
        content = ": agent progress; no ledger mutations\n\n"
        for row in rows:
            content += f"id: {row['sequence']}\nevent: progress\ndata: " + json.dumps(row, ensure_ascii=False) + "\n\n"
        return Response(content, media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
