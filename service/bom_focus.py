"""Display-only row focus from exact stored documents; never copy decisions."""

from service.bom_compare import ROW_FIELDS


def _ref(document):
    return {"document_id": str(document["id"]), "version": document["version"],
            "payload_hash": document["payload_hash"], "status": document["status"],
            "source_sha256": document["content"]["source"]["sha256"],
            "configuration_id": document["content"]["configuration_id"]}


def mapped_row_ids(document):
    return [row["row_id"] for row in document["content"]["rows"]
            if row.get("decision", {}).get("application_status") == "APPLICABLE"
            and row["decision"].get("item_id")]


def review_focus(document, reference=None):
    """Reference is the latest same-source/config POSTED document with mappings.

    The caller authorizes and checks canonical bytes and exact target version.
    Returned IDs control presentation only, with no quantities, item choices,
    application decisions, approval identities or copied source content.
    """
    rows = document["content"]["rows"]
    current_ids = mapped_row_ids(document)
    result = {"focus_version": "bom-review-focus/1", "document": _ref(document),
              "reference": None, "basis": "NONE", "status": "NO_REFERENCE", "row_ids": [],
              "reference_selection": None, "current_mapped_count": len(current_ids),
              "read_only": True, "model_calls": 0, "ledger_mutations": 0,
              "selection_only": True, "decisions_copied": False, "approvals_copied": False,
              "technical_equivalence": "UNKNOWN", "issues": [],
              "row_count": len(rows), "focus_count": 0, "remaining_count": len(rows)}
    by_id = {row["row_id"]: row for row in rows}
    if len(by_id) != len(rows):
        result.update(status="NEEDS_REVIEW", issues=["DUPLICATE_SOURCE_ROW_ID"])
        return result
    if current_ids:
        result.update(status="FOCUSED", basis="CURRENT_DOCUMENT", row_ids=current_ids,
                      focus_count=len(current_ids), remaining_count=len(rows) - len(current_ids))
        return result
    if reference is None:
        return result
    if (reference["company_id"] != document["company_id"] or reference["status"] != "POSTED"
            or reference["content"]["source"]["sha256"] != document["content"]["source"]["sha256"]
            or reference["content"]["configuration_id"] != document["content"]["configuration_id"]):
        result.update(status="NEEDS_REVIEW", issues=["REFERENCE_SCOPE_MISMATCH"])
        return result
    if str(reference["id"]) == str(document["id"]):
        result.update(status="NO_FOCUS", basis="CURRENT_DOCUMENT")
        return result
    result.update(reference=_ref(reference), basis="POSTED_SAME_SOURCE", status="NO_FOCUS",
                  reference_selection="LATEST_POSTED_WITH_MAPPINGS")
    previous_rows = reference["content"]["rows"]
    previous_ids = {row["row_id"] for row in previous_rows}
    if (len(previous_ids) != len(previous_rows) or previous_ids != set(by_id)
            or any(any(row.get(key) != by_id[row["row_id"]].get(key) for key in ROW_FIELDS)
                   for row in previous_rows)):
        result.update(status="NEEDS_REVIEW", issues=["SOURCE_ROW_EVIDENCE_MISMATCH"])
        return result
    selected = set(mapped_row_ids(reference))
    focus_ids = [row["row_id"] for row in rows if row["row_id"] in selected]
    # The historical selection controls display only; newer unmapped documents
    # retain their own unresolved decisions. Never manufacture arbitrary rows.
    result.update(status="FOCUSED" if focus_ids else "NO_FOCUS", row_ids=focus_ids,
                  focus_count=len(focus_ids), remaining_count=len(rows) - len(focus_ids))
    return result
