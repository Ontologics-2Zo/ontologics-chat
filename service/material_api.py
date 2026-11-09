"""Host-owned read tools for retrieval and exact-version BOM candidate review."""

from copy import deepcopy

from fastapi import Request
from pydantic import Field

from service.gateway import EvidenceRef, ExecutionBudget, ToolOutput, ToolSpec
from service.errors import ServiceError
from service.product import review_product
from service.product_contracts import ProductReviewInput
from service.retrieval import RetrievalEvidence, RetrievalSearch, RetrievalService


class SearchCall(RetrievalSearch):
    ledger_version: int = Field(ge=1)


class EvidenceCall(RetrievalEvidence):
    ledger_version: int = Field(ge=1)


def mount_material_api(app, *, identity, ledger, bom, gateway, principal, context,
                       verified_payload, embedding=None):
    retrieval = RetrievalService(identity.authorize, embedding=embedding)
    app.state.retrieval = retrieval

    def output(result, payload, ctx, *, review=False):
        refs = [EvidenceRef(evidence_id=ctx.read_snapshot_id,
            source_version=str(ctx.ledger_version), content_hash=payload["metadata"]["source_sha256"])]
        for hit in result.get("hits", []):
            refs.append(EvidenceRef(evidence_id=hit["evidence_id"],
                source_version=result["meta"]["corpus_version"], content_hash=hit["content_hash"]))
        return ToolOutput(status="needs_review" if review else "completed", data=result,
            snapshot=ctx, evidence_refs=refs)

    def search_handler(actor, args, ctx):
        payload = verified_payload(actor, args.ledger_version)
        body = RetrievalSearch.model_validate(args.model_dump(exclude={"ledger_version"}))
        return output(retrieval.search(actor, payload, ctx, body), payload, ctx)

    def evidence_handler(actor, args, ctx):
        payload = verified_payload(actor, args.ledger_version)
        body = RetrievalEvidence.model_validate(args.model_dump(exclude={"ledger_version"}))
        return output(retrieval.evidence(actor, payload, ctx, body), payload, ctx)

    def product_handler(actor, args, ctx):
        payload = verified_payload(actor, args.ledger_version)
        selected = bom.get(actor, actor.company_id, args.bom_id)
        if (selected["version"] != args.bom_version or selected["payload_hash"] != args.payload_hash):
            raise ServiceError("PRODUCT_BOM_VERSION_CHANGED", 409)
        review_payload = deepcopy(payload)
        review_payload["bom"] = {**selected["content"], "bom_id": selected["id"],
            "bom_version": selected["version"], "payload_hash": selected["payload_hash"], "status": selected["status"]}
        def search(query, limit):
            # Draft rows are reviewed directly, never promoted into the posted
            # ledger corpus or sent to an external model.
            return retrieval.search(actor, payload, ctx,
                RetrievalSearch(query=query, limit=limit, profile="R1"))
        result = review_product(review_payload, args, ctx, search=search)
        current = bom.get(actor, actor.company_id, args.bom_id)
        if (current["version"], current["payload_hash"], current["status"]) != (
                selected["version"], selected["payload_hash"], selected["status"]):
            raise ServiceError("PRODUCT_BOM_VERSION_CHANGED", 409)
        result["meta"]["identity_revision"] = actor.identity_revision
        return output(result, payload, ctx, review=True)

    for name, model, handler in (("retrieval.search", SearchCall, search_handler),
                                 ("retrieval.evidence", EvidenceCall, evidence_handler),
                                 ("product.review", ProductReviewInput, product_handler)):
        gateway.register(ToolSpec(name, "query.read", False, model, handler))

    def execute(request, name, body):
        actor = principal(request, write=True)
        # Historical answers reopen their own snapshot, while authorization is
        # always revalidated against the current session. Product itself also
        # requires the current version before suggesting a new review.
        ctx = context(actor, ledger.read(actor, version=body.ledger_version))
        return gateway.execute(name, body.model_dump(), principal=actor, context=ctx,
            budget=ExecutionBudget(ctx.execution_id, max_tool_calls=1)).data

    @app.post("/api/mks2/retrieval/search")
    def search(request: Request, body: SearchCall):
        return execute(request, "retrieval.search", body)

    @app.post("/api/mks2/retrieval/evidence")
    def evidence(request: Request, body: EvidenceCall):
        return execute(request, "retrieval.evidence", body)

    @app.post("/api/mks2/product/review")
    def product(request: Request, body: ProductReviewInput):
        return execute(request, "product.review", body)
