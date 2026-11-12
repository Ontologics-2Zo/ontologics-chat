"""Measure what the OWL RL reasoning check adds to SHACL, on stored ledger snapshots.

Read-only: reads `service_snapshots` payloads, injects faults into in-memory
copies, and prints aggregated counts and timings only (no entity ids, values,
class lists or schema text). The schema comes from PRIVATE_INPUT_ROOT.

Compared checks
  A  SHACL with RDFS inference (the check before DEC-H67)
  B  A + OWL RL contradictions (disjoint classes, owl:Nothing)
  C  A + full OWL RL check (contradictions + entailed-type mismatches)

A fault counts as caught when the check reports something the clean copy did
not: SHACL newly fails, or a contradiction or type mismatch appears that is not
in the clean report (compared by content, so a new finding on an entity that
was already flagged still counts).

Faults (each in its own copy of the latest snapshot)
  F1 one relationship reversed, once per predicate in use
  F2 one entity given another class in use (seeded sample)
  F3 one relationship pointed at an entity of another class (seeded sample)

Usage:
  PRIVATE_INPUT_ROOT=... .venv/bin/python dev/measure_reasoning.py --runtime-file <runtime.json> [--samples 40]
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import random
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402
from pyshacl import validate  # noqa: E402

from service.ontology import OntologyCatalog  # noqa: E402
from service.projections import OntologyProjector  # noqa: E402
from service.reasoning import reasoning_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-file", required=True)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    root = os.environ.get("PRIVATE_INPUT_ROOT")
    if not root:
        sys.exit("PRIVATE_INPUT_ROOT required")
    url = json.loads(Path(args.runtime_file).read_text())["DATABASE_URL"]
    with psycopg.connect(url) as conn:
        conn.read_only = True
        rows = conn.execute("SELECT ledger_version, payload FROM service_snapshots ORDER BY ledger_version").fetchall()
    projector = OntologyProjector(OntologyCatalog.from_private_root(root))

    def build(payload, version):
        meta = payload.get("metadata", {})
        graph, uris, *_ = projector._build(payload, version, meta["company_id"], meta["run_id"])
        return graph, uris

    def shacl_flags(graph):
        conforms, _, _ = validate(data_graph=graph, shacl_graph=projector.shapes, ont_graph=projector.tbox, inference="rdfs",
                                  advanced=False, js=False, do_owl_imports=False, inplace=False)
        return not conforms

    def checks(payload, version):
        graph, uris = build(payload, version)
        t0 = time.perf_counter(); a = shacl_flags(graph); t1 = time.perf_counter()
        report = reasoning_report(projector.tbox, graph, uris, projector.classes, projector.predicates); t2 = time.perf_counter()
        b = report["inconsistency_count"] > 0
        contradictions = {json.dumps(item, sort_keys=True) for item in report["inconsistencies"]}
        mismatches = {json.dumps({k: item[k] for k in ("entity_id", "declared", "entailed")}, sort_keys=True) for item in report["type_mismatches"]}
        if report["inconsistency_count"] > len(report["inconsistencies"]) or report["type_mismatch_count"] > len(report["type_mismatches"]):
            sys.exit("REPORT_LIST_TRUNCATED")  # content comparison needs the full lists
        return {"A": a, "B": a or b, "C": a or b or report["type_mismatch_count"] > 0,
                "contradiction_set": contradictions, "mismatch_set": mismatches,
                "mismatches": report["type_mismatch_count"], "contradictions": report["inconsistency_count"],
                "inferred": report["inferred_relationship_count"], "t_shacl": t1 - t0, "t_owl": t2 - t1}

    # Clean ledger: every stored version.
    clean = []
    for version, payload in rows:
        if payload.get("entities"):
            t = time.perf_counter(); build(payload, version); t_build = time.perf_counter() - t
            clean.append({"version": version, "entities": len(payload["entities"]), "relationships": len(payload["relationships"]),
                          "t_build": t_build, **checks(payload, version)})

    # Faults on the latest snapshot. Baseline findings are subtracted: a fault
    # counts as caught only when the check finds more than on the clean copy.
    version, payload = rows[-1]
    base = checks(payload, version)
    rng = random.Random(args.seed)
    types = sorted({e["type"] for e in payload["entities"]})
    by_type = {}
    for entity in payload["entities"]:
        by_type.setdefault(entity["type"], []).append(entity["id"])
    first_per_predicate = {}
    for index, rel in enumerate(payload["relationships"]):
        first_per_predicate.setdefault(rel["predicate"], index)
    cases = {"F1": [], "F2": [], "F3": []}
    for index in first_per_predicate.values():
        p = deepcopy(payload); r = p["relationships"][index]; r["source"], r["target"] = r["target"], r["source"]; cases["F1"].append(p)
    for entity in rng.sample(payload["entities"], min(args.samples, len(payload["entities"]))):
        p = deepcopy(payload); e = next(x for x in p["entities"] if x["id"] == entity["id"])
        e["type"] = rng.choice([t for t in types if t != entity["type"]]); cases["F2"].append(p)
    for index in rng.sample(range(len(payload["relationships"])), min(args.samples, len(payload["relationships"]))):
        p = deepcopy(payload); r = p["relationships"][index]
        target_type = next(e["type"] for e in p["entities"] if e["id"] == r["target"])
        r["target"] = rng.choice(by_type[rng.choice([t for t in types if t != target_type and t in by_type])]); cases["F3"].append(p)

    def caught(result, key):
        shacl = result["A"] and not base["A"]
        new_contradiction = bool(result["contradiction_set"] - base["contradiction_set"])
        new_mismatch = bool(result["mismatch_set"] - base["mismatch_set"])
        if key == "A":
            return shacl
        if key == "B":
            return shacl or new_contradiction
        return shacl or new_contradiction or new_mismatch

    faults = {}
    for name, payloads in cases.items():
        results = [checks(p, version) for p in payloads]
        count_rule = sum((r["A"] and not base["A"]) or r["mismatches"] > base["mismatches"] or r["contradictions"] > base["contradictions"] for r in results)
        faults[name] = {"cases": len(results), **{key: sum(caught(r, key) for r in results) for key in ("A", "B", "C")},
                        "C_by_count_only": count_rule}

    ms = lambda values: {"median_ms": round(statistics.median(values) * 1000), "max_ms": round(max(values) * 1000)}
    print(json.dumps({
        "snapshot_versions": len(clean),
        "latest": {"ledger_version": version, "entities": len(payload["entities"]), "relationships": len(payload["relationships"])},
        "clean_findings": {"A_versions_flagged": sum(c["A"] for c in clean),
                           "C_versions_flagged": sum(c["C"] for c in clean),
                           "C_type_mismatches_by_version": [c["mismatches"] for c in clean],
                           "contradictions_total": sum(c["contradictions"] for c in clean),
                           "inferred_relationships_total": sum(c["inferred"] for c in clean)},
        "faults": faults,
        "timing": {"graph_build": ms([c["t_build"] for c in clean]), "shacl_rdfs": ms([c["t_shacl"] for c in clean]),
                   "owl_rl_check": ms([c["t_owl"] for c in clean])},
        "settings": {"samples": args.samples, "seed": args.seed},
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
