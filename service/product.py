"""Product source/candidate review over one already-parsed ledger snapshot.

No parser, provider, filesystem, database, approval or write port is available
here. Similarity and old conditional mappings only suggest candidates. Source
cells are data, including any instructions embedded in a supplier document.
"""

from copy import deepcopy
import re
import unicodedata

from service.errors import ServiceError
from service.gateway import ToolContext
from service.product_contracts import ProductReviewInput


PRODUCT_VERSION = "product-review/1"
_DECISION_FIELDS = ("application_status", "quantity_per_unit", "unit", "item_id",
                    "review_basis", "evidence_note")
_TOKEN = re.compile(r"[a-z0-9]+(?:[._/+-][a-z0-9]+)*|[가-힣]+")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,99}\Z")
_SEARCH_STATES = {"HIT", "NO_HIT", "INDEX_INCOMPLETE", "INDEX_ERROR", "PROFILE_NOT_IMPLEMENTED"}


def _tokens(value):
    return set(_TOKEN.findall(unicodedata.normalize("NFKC", str(value or "")).casefold()))


def _parts(value):
    return {token for token in _tokens(value) if len(token) >= 4
            and re.search(r"[a-z]", token) and re.search(r"[0-9]", token)}


def _item_name(entity):
    return entity.get("attributes", {}).get("display_name") or entity.get("label") or entity["display_id"]


def _source_evidence(bom, row):
    return {"evidence_id": row["row_id"], "kind": "BOM_ROW", "source_sha256": bom["source"]["sha256"],
            "locator": row["locator"], "row_id": row["row_id"], "bom_id": bom["bom_id"],
            "bom_version": bom["bom_version"], "origin": bom["source"].get("source_kind", "unknown")}


def _item_evidence(entity):
    evidence = entity.get("evidence", {})
    return {"evidence_id": entity["id"], "kind": "ITEM_RECORD", "origin": entity.get("origin", "unknown"),
            "source_sha256": evidence.get("source_sha256"), "locator": evidence.get("source_locator")}


def _verify(payload, request, context):
    meta = payload.get("metadata", {})
    projection = payload.get("projection") or {}
    if (meta.get("company_id") != context.company_id
            or meta.get("snapshot_id") != context.read_snapshot_id
            or meta.get("server_ledger_version") != request.ledger_version
            or meta.get("current_ledger_version") != request.ledger_version
            or context.ledger_version != request.ledger_version):
        raise ServiceError("PRODUCT_SNAPSHOT_CHANGED", 409)
    if (projection.get("state") != "READY" or projection.get("projection_version") != request.ledger_version
            or context.projection_version != request.ledger_version):
        raise ServiceError("PRODUCT_PROJECTION_NOT_READY", 409)
    bom = payload.get("bom")
    if not bom:
        raise ServiceError("PRODUCT_BOM_UNAVAILABLE", 409)
    if (bom.get("bom_id") != request.bom_id or bom.get("bom_version") != request.bom_version
            or bom.get("payload_hash") != request.payload_hash):
        raise ServiceError("PRODUCT_BOM_VERSION_CHANGED", 409)
    return bom


def _retrieve(search, row, context, limit):
    if search is None:
        return {"status": "NOT_CONFIGURED", "hits": []}
    # Only a row's bounded text is searched locally. It never becomes an
    # instruction, provider prompt, URL, filename or tool name.
    query = str(row.get("source_description") or " ".join(row.get("designators", []))).strip()[:1000]
    if not query:
        return {"status": "NO_QUERY", "hits": []}
    try:
        response = search(query, min(20, max(8, limit)))
    except ServiceError as error:
        # Scope and freshness errors are never disguised as an empty search.
        if error.status_code in {401, 403, 409}:
            raise
        return {"status": "INDEX_ERROR", "hits": []}
    except Exception:
        return {"status": "INDEX_ERROR", "hits": []}
    if not isinstance(response, dict) or response.get("status") not in _SEARCH_STATES:
        return {"status": "INDEX_ERROR", "hits": []}
    meta = response.get("meta", {})
    if (meta.get("snapshot_id", meta.get("read_snapshot_id")) != context.read_snapshot_id
            or meta.get("ledger_version") != context.ledger_version
            or meta.get("projection_version") != context.projection_version
            or meta.get("company_id") != context.company_id):
        raise ServiceError("PRODUCT_RETRIEVAL_SCOPE_MISMATCH", 409)
    return {"status": response["status"], "hits": response.get("hits", []),
            "corpus_version": meta.get("corpus_version"), "index_version": meta.get("index_version"),
            "embedding": deepcopy(meta.get("embedding"))}


def _review_row(payload, bom, row, masters, request, retrieval):
    decision = row.get("decision") or {}
    description = row.get("source_description", "")
    source_tokens, part_tokens = _tokens(description), _parts(description)
    candidates = {}

    def suggest(entity, method, rank, evidence=None):
        entry = candidates.setdefault(entity["id"], {
            "item_entity_id": entity["id"], "item_id": entity["display_id"], "display_name": _item_name(entity),
            "origin": entity.get("origin", "unknown"), "review_status": "UNVERIFIED_CANDIDATE",
            "match_methods": [], "evidence_refs": [_item_evidence(entity)], "_rank": rank,
            "automatic_application": False, "technical_equivalence_verified": False})
        if method not in entry["match_methods"]:
            entry["match_methods"].append(method)
        entry["_rank"] = max(entry["_rank"], rank)
        if evidence and evidence not in entry["evidence_refs"]:
            entry["evidence_refs"].append(evidence)

    by_display = {}
    for entity in masters.values():
        by_display.setdefault(entity["display_id"], []).append(entity)
        if decision.get("item_id") == entity["display_id"]:
            suggest(entity, "CURRENT_CONDITIONAL_DECISION", 100, _source_evidence(bom, row))
        label = _item_name(entity)
        if part_tokens.intersection(_parts(label)):
            suggest(entity, "EXACT_PART_TOKEN", 90, _source_evidence(bom, row))
        overlap = {token for token in source_tokens.intersection(_tokens(label)) if len(token) > 1}
        if overlap:
            suggest(entity, "LEXICAL_CANDIDATE", 30 + min(len(overlap), 10), _source_evidence(bom, row))
    # Historic candidates are scoped to this snapshot and remain assumptions.
    # RefDes alone cannot identify a part; require label overlap as well.
    designators = set(row.get("designators", []))
    for old in payload.get("opening_balance", {}).get("mapping_candidates", []):
        old_refs = set(re.split(r"[,;\s]+", str(old.get("refdes", "")))) - {""}
        if old_refs and old_refs == designators and source_tokens.intersection(_tokens(old.get("display_name"))):
            for entity in by_display.get(old.get("item_id"), []):
                suggest(entity, "HISTORICAL_UNREVIEWED_CANDIDATE", 60, _item_evidence(entity))
    search_evidence = []
    for hit in retrieval.get("hits", []):
        if not isinstance(hit, dict):
            continue
        source = hit.get("source") or {}
        ref = {"evidence_id": hit.get("evidence_id"), "content_hash": hit.get("content_hash"),
               "source_sha256": source.get("sha256"), "locator": source.get("locator"),
               "origin": hit.get("origin", "unknown"), "kind": hit.get("kind", "unknown")}
        for identifier in hit.get("node_ids", []):
            if identifier in masters:
                suggest(masters[identifier], "LOCAL_RETRIEVAL_CANDIDATE", 20, ref)
        if (source.get("sha256") == bom["source"]["sha256"] and source.get("row_id") == row["row_id"]
                and source.get("bom_id") == bom["bom_id"] and source.get("bom_version") == bom["bom_version"]):
            search_evidence.append(ref)
    ordered = sorted(candidates.values(), key=lambda item: (-item["_rank"], item["item_id"], item["item_entity_id"]))
    total = len(ordered)
    for item in ordered:
        del item["_rank"]
    issues = ["TECHNICAL_EQUIVALENCE_UNVERIFIED", "SOURCE_UNIT_UNCONFIRMED", "APPLICATION_REVIEW_REQUIRED"]
    if row.get("source_quantity") is None:
        issues.append("SOURCE_QUANTITY_UNCONFIRMED")
    if not row.get("designators"):
        issues.append("SOURCE_DESIGNATOR_UNCONFIRMED")
    if total == 0:
        issues.append("NO_ITEM_CANDIDATE")
    elif total > 1:
        issues.append("AMBIGUOUS_ITEM_CANDIDATES")
    if any(len(by_display.get(item["item_id"], [])) > 1 for item in ordered):
        issues.append("ITEM_CODE_SCOPE_AMBIGUOUS")
    if bom.get("source_conflicts"):
        issues.append("SOURCE_CONFLICT_REQUIRES_REVIEW")
    if bom.get("configuration_binding") != "VERIFIED":
        issues.append("CONFIGURATION_BINDING_UNVERIFIED")
    return {"row_id": row["row_id"], "ordinal": row.get("ordinal"), "status": "NEEDS_REVIEW",
            "source": {key: deepcopy(row.get(key)) for key in
                       ("locator", "source_row", "sheet", "source_description", "source_quantity", "source_quantity_text", "designators")},
            "source_unit": None, "source_unit_status": "UNKNOWN",
            "current_decision": {key: deepcopy(decision.get(key)) for key in _DECISION_FIELDS},
            "candidate_count": total, "candidates": ordered[:request.candidate_limit],
            "candidates_truncated": total > request.candidate_limit,
            "ambiguity": "MULTIPLE_CANDIDATES" if total > 1 else "SINGLE_UNVERIFIED_CANDIDATE" if total else "NO_CANDIDATE",
            "issues": issues, "evidence_refs": [_source_evidence(bom, row), *search_evidence],
            "retrieval": {key: deepcopy(value) for key, value in retrieval.items() if key != "hits"},
            "automatic_application": False, "decision_changed": False}


def review_product(payload, request, context, *, search=None):
    """Return all rows, or one selected row with one bounded local search.

    ``search(query, limit)`` must be a Host-bound same-snapshot RetrievalService
    call. Summary requests do no per-row searches; opening a row searches once.
    """
    request = ProductReviewInput.model_validate(request)
    if not isinstance(context, ToolContext):
        raise ServiceError("SERVER_CONTEXT_REQUIRED", 403)
    bom = _verify(payload, request, context)
    source_rows = bom["rows"]
    if request.row_id is not None:
        source_rows = [row for row in source_rows if row["row_id"] == request.row_id]
        if len(source_rows) != 1:
            raise ServiceError("PRODUCT_SOURCE_ROW_NOT_FOUND", 404)
    masters = {row["id"]: row for row in payload["entities"] if row["type"] == "InternalItem"}
    rows = []
    for row in source_rows:
        retrieval = (_retrieve(search, row, context, request.candidate_limit) if request.row_id else
                     {"status": "NOT_REQUESTED_FOR_SUMMARY", "hits": []})
        rows.append(_review_row(payload, bom, row, masters, request, retrieval))
    return {"status": "NEEDS_REVIEW", "product_version": PRODUCT_VERSION,
            "meta": {"company_id": context.company_id, "snapshot_id": context.read_snapshot_id,
                     "ledger_version": context.ledger_version, "projection_version": context.projection_version,
                     "ontology_version": context.ontology_version, "rule_version": context.rule_version,
                     "as_of": context.as_of.isoformat() if context.as_of else None,
                     "known_as_of": context.known_as_of.isoformat() if context.known_as_of else None},
            "bom_id": bom["bom_id"], "bom_version": bom["bom_version"], "payload_hash": bom["payload_hash"],
            "bom_status": bom.get("status", "UNKNOWN"),
            "source_sha256": bom["source"]["sha256"], "parser_version": bom["source"].get("parser_version"),
            "parser_result_reused": True, "all_row_count": len(bom["rows"]), "returned_row_count": len(rows),
            "current_application_counts": {state: sum(row.get("decision", {}).get("application_status") == state for row in bom["rows"])
                                           for state in ("APPLICABLE", "UNRESOLVED", "NOT_MOUNTED", "SETTING")},
            "source_conflicts": [code for code in bom.get("source_conflicts", []) if isinstance(code, str) and _CODE.fullmatch(code)],
            "rows": rows, "technical_review": "UNREVIEWED", "manufacturing_feasibility": "UNKNOWN",
            "execution_mode": "deterministic_local", "extraction_model_status": "NOT_RUN", "model_calls": 0,
            "ledger_mutations": 0, "automatic_application": False, "source_verified_promotions": 0}


def product_answer(result):
    """Presentation wrapper; preserve the complete read result as local detail."""
    rows = result["rows"]
    counts = result["current_application_counts"]
    sections = [{"id": "product-scope", "title": "원문과 검토 범위", "origin": "mixed_sources", "items": [
        {"label": "원문 전체", "value": f"{result['all_row_count']}행", "detail": "등록된 파싱 결과와 모든 행을 보존합니다."},
        {"label": "이번 조회", "value": f"{len(rows)}행", "detail": "후보 제시는 기존 적용 판단을 바꾸지 않습니다."},
        {"label": "현재 적용 미확인", "value": f"{counts['UNRESOLVED']}행", "detail": "미확인을 부족 0이나 기술 승인으로 바꾸지 않습니다."}]}]
    for row in rows[:10]:
        sections.append({"id": "product-row-" + row["row_id"], "title": row["source"]["locator"],
                         "origin": "unreviewed", "status": "UNKNOWN", "items": [
            {"label": "원문 명칭", "value": str(row["source"].get("source_description") or "미확인")[:300],
             "origin": "actual_bom" if row["evidence_refs"][0]["origin"] == "actual_bom" else "unknown"},
            {"label": "현재 연결", "value": row["current_decision"].get("item_id") or "미연결",
             "detail": "현재 등록 판단입니다. 후보와 동일성 검수는 별개입니다."},
            {"label": "품목 후보", "value": ", ".join(f"{item['item_id']} · {item['display_name']}" for item in row["candidates"]) or "없음",
             "detail": "복수 후보·단위·구성·실물 동일성은 품목 연결 검토 화면에서 확인해 주세요."} ]})
    identifiers = list(dict.fromkeys(item["item_entity_id"] for row in rows for item in row["candidates"]))
    return {"status": "PARTIAL", "intent_label": "BOM 품목 연결 검토", "conclusion": "품목 후보 · 기술 검수 필요",
            "answer": f"전체 {result['all_row_count']}행 중 {len(rows)}행의 원문·현재 판단·품목 후보를 확인했습니다. 후보를 자동 적용하거나 원장을 변경하지 않았습니다.",
            "sections": sections, "meta": deepcopy(result["meta"]), "model_calls": 0, "ledger_mutations": 0,
            "steps": ["등록된 파싱 결과와 정확 버전 확인", "같은 원장 품목·로컬 근거의 후보 비교", "현재 판단 보존·미확인 항목 표시"],
            "evidence_node_ids": identifiers, "evidence_edge_ids": [], "graph_path": identifiers,
            "evidence_refs": [ref for row in rows for ref in row["evidence_refs"]], "product_review": deepcopy(result)}
