"""Deterministic comparison of explicitly selected, preserved BOM source rows.

This module has no file parser, database, model or mutation capability. Position
text is a correspondence key, never proof of part equivalence or revision lineage.
"""

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
import re

from service.contracts import content_hash


COMPARISON_VERSION = "bom-source-compare/1"
ROW_FIELDS = ("row_id", "locator", "sheet", "source_row", "ordinal", "designators",
              "source_quantity", "source_quantity_text", "source_description", "cells")


def _document_ref(document):
    content = document["content"]
    return {"document_id": str(document["id"]), "version": document["version"],
            "payload_hash": document["payload_hash"], "status": document["status"],
            "configuration_id": content["configuration_id"],
            "filename": content["source"]["filename"],
            "source_sha256": content["source"]["sha256"],
            "source_row_count": len(content["rows"])}


def _row_ref(document, row):
    return {**{key: deepcopy(row.get(key)) for key in ROW_FIELDS},
            "document_id": str(document["id"]), "document_version": document["version"],
            "payload_hash": document["payload_hash"],
            "source_sha256": document["content"]["source"]["sha256"]}


def _placeholder(token):
    # Preserve unfamiliar identifiers; only explicit missing/wildcard markers
    # are excluded. In particular, Ref** is not a unique physical position.
    return (bool(re.search(r"[*?]", token)) or not any(c.isalnum() for c in token)
            or token.casefold() in {"na", "n/a", "ns", "none", "null", "unknown", "opened"})


def _changes(left, right):
    changes = []
    if (left["source_quantity"] is not None and right["source_quantity"] is not None
            and Decimal(left["source_quantity"]) != Decimal(right["source_quantity"])):
        changes.append("QUANTITY_CHANGED")
    # Counts overlap intentionally: a quantity cell is also an original value.
    # Never infer which equal-valued raw cell is the quantity column.
    if any(left.get(key) != right.get(key) for key in
           ("source_quantity_text", "source_description", "cells")):
        changes.append("SOURCE_VALUE_CHANGED")
    return changes


def compare_bom_documents(left, right):
    """Compare authorized, exact-version document records without editing them."""
    result = {"comparison_version": COMPARISON_VERSION, "status": "NO_BASELINE",
              "left": _document_ref(left) if left else None, "right": _document_ref(right),
              "comparison_scope": "SOURCE_ROWS_ONLY", "read_only": True,
              "model_calls": 0, "ledger_mutations": 0, "automatic_application": False,
              "technical_equivalence": "UNKNOWN", "lineage_inferred": False,
              "quantity_aggregation": False, "same_source": False,
              "source_warnings": {"left": deepcopy(left["content"].get("source_conflicts", [])) if left else [],
                                  "right": deepcopy(right["content"].get("source_conflicts", []))},
              "warnings": [], "sheet_policy": None, "rows": [],
              "summary": {"left_rows": len(left["content"]["rows"]) if left else 0,
                          "right_rows": len(right["content"]["rows"]), "added": 0, "removed": 0,
                          "changed": 0, "quantity_changed": 0, "source_value_changed": 0,
                          "unchanged": 0, "needs_review": 0}}
    if left is None:
        return result
    if left["content"]["configuration_id"] != right["content"]["configuration_id"]:
        result["warnings"].append("DIFFERENT_CONFIGURATION_EXPLICIT_BASELINE")

    def append(kind, left_row=None, right_row=None, *, basis=None, reasons=(), group=None):
        changes = _changes(left_row, right_row) if left_row and right_row else []
        result["rows"].append({"change_type": kind, "changes": changes,
            "match_basis": basis, "left": _row_ref(left, left_row) if left_row else None,
            "right": _row_ref(right, right_row) if right_row else None,
            "review_reasons": sorted(reasons), "review_group_id": group})
        result["summary"][kind.lower()] += 1
        for change in changes:
            result["summary"][change.lower()] += 1

    lrows, rrows = left["content"]["rows"], right["content"]["rows"]
    same_source = left["content"]["source"]["sha256"] == right["content"]["source"]["sha256"]
    result["same_source"] = same_source
    if same_source:
        # Source-row identity is available even for a duplicated/missing RefDes.
        # Application decisions are deliberately outside this comparison.
        by_id = {row["row_id"]: row for row in rrows}
        source_rows_identical = (len(by_id) == len(rrows) == len(lrows)
            and len({row["row_id"] for row in lrows}) == len(lrows)
            and all(row["row_id"] in by_id and all(row.get(key) == by_id[row["row_id"]].get(key)
                        for key in ROW_FIELDS) for row in lrows))
        if source_rows_identical:
            result.update(status="IDENTICAL_SOURCE", sheet_policy="SAME_SOURCE_ROW")
            for row in lrows:
                append("UNCHANGED", row, by_id[row["row_id"]], basis="SAME_SOURCE_ROW")
            return result
        # A source hash alone must not hide divergent extraction evidence.
        result["warnings"].append("SAME_SOURCE_EXTRACTION_DIFFERS")

    sheets = [{row["sheet"] for row in rows} for rows in (lrows, rrows)]
    single = len(sheets[0]) == len(sheets[1]) == 1
    result["sheet_policy"] = "SINGLE_SHEET_CONTEXT" if single else "EXACT_SHEET_NAME"
    if single and sheets[0] != sheets[1]:
        result["warnings"].append("SINGLE_SHEET_NAMES_DIFFER")
    nodes = [(side, row) for side, rows in enumerate((lrows, rrows)) for row in rows]
    parents = list(range(len(nodes)))
    reasons = [set() for _ in nodes]
    keys, indexes = [], defaultdict(list)

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, (side, row) in enumerate(nodes):
        tokens = row["designators"]
        scope = "single" if single else row["sheet"]
        keys.append(frozenset(tokens))
        if not tokens:
            reasons[index].add("MISSING_DESIGNATOR")
        if any(_placeholder(token) for token in tokens):
            reasons[index].add("UNRESOLVED_DESIGNATOR")
        if row["source_quantity"] is None:
            reasons[index].add("QUANTITY_UNCONFIRMED")
        if not single and row["sheet"] not in sheets[1 - side]:
            reasons[index].add("SHEET_CORRESPONDENCE_UNKNOWN")
        if same_source:
            reasons[index].add("SAME_SOURCE_EXTRACTION_DIFFERS")
        for token in tokens:
            indexes[(scope, token.casefold())].append(index)

    for matches in indexes.values():
        for side in (0, 1):
            same_side = [index for index in matches if nodes[index][0] == side]
            if len(same_side) > 1:
                for index in same_side:
                    reasons[index].add("DUPLICATE_DESIGNATOR")
        for index in matches[1:]:
            parents[find(index)] = find(matches[0])
    components = defaultdict(list)
    for index in range(len(nodes)):
        components[find(index)].append(index)
    for component in sorted(components.values(), key=min):
        sides = [[index for index in component if nodes[index][0] == side] for side in (0, 1)]
        problems = set().union(*(reasons[index] for index in component))
        if len(sides[0]) > 1 or len(sides[1]) > 1:
            problems.add("AMBIGUOUS_ROW_CORRESPONDENCE")
        if sides[0] and sides[1] and keys[sides[0][0]] != keys[sides[1][0]]:
            problems.add("DESIGNATOR_SET_CHANGED")
        if problems:
            group = content_hash([[side, row["row_id"]] for side, row in (nodes[index] for index in component)])
            for index in component:
                side, row = nodes[index]
                append("NEEDS_REVIEW", row if side == 0 else None, row if side == 1 else None,
                       reasons=problems, group=group)
        elif sides[0] and sides[1]:
            left_row, right_row = nodes[sides[0][0]][1], nodes[sides[1][0]][1]
            append("CHANGED" if _changes(left_row, right_row) else "UNCHANGED", left_row, right_row,
                   basis="EXACT_DESIGNATOR_SET")
        elif sides[0]:
            append("REMOVED", nodes[sides[0][0]][1], basis="UNIQUE_LEFT_POSITION")
        else:
            append("ADDED", right_row=nodes[sides[1][0]][1], basis="UNIQUE_RIGHT_POSITION")
    result["status"] = "NEEDS_REVIEW" if result["summary"]["needs_review"] else "COMPARED"
    return result
