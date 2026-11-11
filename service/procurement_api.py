"""Authenticated UI and Agent entry points to internal purchasing services."""

from fastapi import Request
from pydantic import Field

from service.contracts import Contract, DocumentAction
from service.errors import ServiceError
from service.gateway import EvidenceRef, ExecutionBudget, ToolOutput, ToolSpec
from service.procurement import ProcurementService
from service.procurement_contracts import ProcurementCreate, ProcurementReject, ProcurementRevise


class ProcurementOptions(Contract):
    ledger_version: int = Field(ge=1)


class RevisionCall(ProcurementRevise):
    document_id: str = Field(min_length=1, max_length=100)


class ActionCall(DocumentAction):
    document_id: str = Field(min_length=1, max_length=100)


class RejectCall(ProcurementReject):
    document_id: str = Field(min_length=1, max_length=100)


class DocumentRead(Contract):
    document_id: str = Field(min_length=1, max_length=100)


def mount_procurement_api(app, *, db, identity, ledger, gateway, principal, context, verified_payload):
    service = ProcurementService(db, identity)
    app.state.procurement = service

    def options_handler(actor, args, ctx):
        payload = verified_payload(actor, args.ledger_version)
        if (args.ledger_version != ctx.ledger_version
                or payload["metadata"]["current_ledger_version"] != args.ledger_version
                or payload["metadata"]["snapshot_id"] != ctx.read_snapshot_id):
            raise ServiceError("SNAPSHOT_CHANGED", 409)
        result = service.options(actor, actor.company_id, args.ledger_version)
        result.update(model_calls=0, ledger_mutations=0, meta={
            "company_id": actor.company_id, "identity_revision": actor.identity_revision,
            "snapshot_id": ctx.read_snapshot_id, "ledger_version": ctx.ledger_version,
            "projection_version": ctx.projection_version, "ontology_version": ctx.ontology_version})
        return ToolOutput(data=result, snapshot=ctx, evidence_refs=[EvidenceRef(
            evidence_id=ctx.read_snapshot_id, source_version=str(ctx.ledger_version),
            content_hash=payload["metadata"]["source_sha256"])])

    gateway.register(ToolSpec("procurement.options", "query.read", False, ProcurementOptions, options_handler))

    def document_handler(actor, args, ctx):
        # Read-back for write reconciliation; the same document view as the UI.
        return ToolOutput(data=service.get(actor, actor.company_id, args.document_id), snapshot=ctx)

    gateway.register(ToolSpec("procurement.document", "document.read", False, DocumentRead, document_handler))

    def handler(action, model):
        def call(actor, args, ctx):
            values = args.model_dump()
            document_id = values.pop("document_id", None)
            body = model.model_validate(values)
            method = getattr(service, action)
            result = (method(actor, actor.company_id, body) if action == "create"
                      else method(actor, actor.company_id, document_id, body))
            return ToolOutput(data=result, snapshot=ctx)
        return call

    for action, model, tool_model, permission in (
        ("create", ProcurementCreate, ProcurementCreate, "document.create"),
        ("revise", ProcurementRevise, RevisionCall, "document.update"),
        ("review", DocumentAction, ActionCall, "document.review"),
        ("reject", ProcurementReject, RejectCall, "document.review"),
        ("approve", DocumentAction, ActionCall, "document.approve"),
        ("post", DocumentAction, ActionCall, "document.post")):
        gateway.register(ToolSpec("procurement." + action, permission, True, tool_model, handler(action, model)))

    def execute(request, action, body, document_id=None):
        actor = principal(request, write=True)
        ctx = context(actor, ledger.read(actor))
        args = body.model_dump()
        if document_id is not None:
            args["document_id"] = document_id
        return gateway.execute("procurement." + action, args, principal=actor, context=ctx,
            budget=ExecutionBudget(ctx.execution_id, max_tool_calls=1)).data

    @app.get("/api/mks2/procurement/activity")
    def list_activity(request: Request):
        actor = principal(request)
        return {"activity": service.activity(actor, actor.company_id)}

    @app.get("/api/mks2/procurement/documents")
    def list_documents(request: Request):
        actor = principal(request)
        return {"documents": service.list(actor, actor.company_id)}

    @app.get("/api/mks2/procurement/documents/{document_id}")
    def get_document(request: Request, document_id: str):
        actor = principal(request)
        return service.get(actor, actor.company_id, document_id)

    @app.post("/api/mks2/procurement/options")
    def options(request: Request, body: ProcurementOptions):
        return execute(request, "options", body)

    @app.post("/api/mks2/procurement/documents")
    def create(request: Request, body: ProcurementCreate):
        result = execute(request, "create", body)
        cue = getattr(app.state, "demo_cue", None)
        if cue is not None and isinstance(result, dict) and result.get("kind") == "PR":
            cue.purchase_request_drafted(result.get("company_id") or principal(request).company_id)
        return result

    @app.post("/api/mks2/procurement/documents/{document_id}/revise")
    def revise(request: Request, document_id: str, body: ProcurementRevise):
        return execute(request, "revise", body, document_id)

    @app.post("/api/mks2/procurement/documents/{document_id}/reject")
    def reject(request: Request, document_id: str, body: ProcurementReject):
        return execute(request, "reject", body, document_id)

    @app.post("/api/mks2/procurement/documents/{document_id}/{action}")
    def action(request: Request, document_id: str, action: str, body: DocumentAction):
        if action not in {"review", "approve", "post"}:
            raise ServiceError("ACTION_NOT_ALLOWED", 404)
        return execute(request, action, body, document_id)
