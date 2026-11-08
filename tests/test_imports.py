"""Independent synthetic import cases; no private source values or fixtures."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from service.imports import (
    BASELINE_PATH, InitialInputError, canonical_id, convert_baseline,
    load_initial, partition_reference_bundle, validate_initial,
)


def synthetic_baseline():
    """A small imaginary company, deliberately unlike the private scenario."""
    return {
        "schema_version": "cq123-design-data/1.1", "profile_id": "example-profile",
        "reference_run_id": "example-run", "snapshot_id": "example-snapshot",
        "origin": "synthetic_operation", "service_implementation_claim": False,
        "immutable": True, "ledger_version": 7, "projection_version": 7,
        "as_of": "2025-02-01T10:00:00+00:00", "known_as_of": "2025-02-01T10:00:00+00:00",
        "scope": {"round_id": "delivery-a", "job_id": "job-a", "configuration_id": "config-a", "contract_line_id": "contract-line-a"},
        "configuration": {"configuration_id": "config-a", "registration_status": "NOT_REGISTERED", "technical_review_status": "UNREVIEWED", "bom_snapshot_id": "unapplied-bom"},
        "contract_line": {"contract_line_id": "contract-line-a", "customer_id": "customer-a", "promised_qty": 20, "unit": "piece"},
        "delivery_round": {"round_id": "delivery-a", "contract_line_id": "contract-line-a", "job_id": "job-a", "configuration_id": "config-a", "promised_qty": 20,
                           "good_qty": 9, "fg_received_qty": 8, "shipped_qty": 5, "arrived_qty": 3, "unit": "piece", "arrival_due_date": "2025-03-01"},
        "production_reports": [{"report_id": "aggregate-a", "job_id": "job-a", "good_qty": 9, "defect_qty": 1, "pending_judgement_qty": 2, "total_reported_qty": 12,
                                "record_type": "immutable_baseline_aggregate", "effective_at": None, "recorded_at": None, "event_time_coverage": "UNKNOWN", "board_ids": None, "board_membership_coverage": "UNKNOWN"}],
        "fg_receipts": [{"fg_receipt_id": "receipt-aggregate", "source_report_id": "aggregate-a", "job_id": "job-a", "qty": 8, "unit": "piece", "board_ids": None, "membership_coverage": "UNKNOWN"}],
        "shipments": [{"shipment_id": "shipment-aggregate", "round_id": "delivery-a", "qty": 5, "unit": "piece", "board_ids": None, "membership_coverage": "UNKNOWN",
                       "arrivals": [{"arrival_id": "arrival-aggregate", "qty": 3, "board_ids": None}]}],
        "inventory": {"lots": [{"lot_id": "lot-a", "item_id": "item-a", "on_hand_qty": 17, "reserved_other_qty": 4, "reserved_self_qty": 2, "held_qty": 1, "unit": "piece", "location": "bin-a"}],
                      "reservations": [{"reservation_id": "reservation-other", "lot_id": "lot-a", "item_id": "item-a", "qty": 4, "unit": "piece", "demand_id": "other-demand", "status": "ACTIVE", "reservation_relation_to_selected_demand": "OTHER"},
                                       {"reservation_id": "reservation-self", "lot_id": "lot-a", "item_id": "item-a", "qty": 2, "unit": "piece", "demand_id": "job-a", "status": "ACTIVE", "reservation_relation_to_selected_demand": "SELF"}],
                      "other_item_snapshots": []},
        "material_mapping_assumptions": [],
        "quotations": [{"quote_id": "offer-a", "supplier_id": "supplier-a", "item_id": "item-a", "unit_price": 12, "moq": 2, "order_multiple": 1, "freight": None,
                        "lead_business_days": 4, "currency": "USD", "unit": "piece", "supplier_confirmation": False, "valid_until": None}],
        "substitutes": [{"substitution_id": "candidate-a", "original_item_id": "item-a", "candidate_item_id": "alternative-a", "candidate_on_hand_qty": 11,
                         "unit": "piece", "status": "NOT_REVIEWED", "approval_ref": None, "counted_qty_for_selected_demand": 0}],
        "purchase_requests": [], "purchase_orders": [], "new_receipts": [], "board_instances": [], "installation_events": [], "coverage_assertions": [],
        "query_results": {"answer": "EXPECTED_SENTINEL_NEVER_INITIAL"},
    }


class InitialConversionTests(unittest.TestCase):
    def convert(self, source=None, **kwargs):
        source = synthetic_baseline() if source is None else source
        return convert_baseline(source, company_id=kwargs.get("company_id", "test-company"),
                                run_id=kwargs.get("run_id", "test-run"), profile_id="test-profile",
                                imported_at="2025-02-02T12:00:00+00:00", source_sha256="a"*64)

    def test_initial_reconciles_counts_and_distinguishes_source_server_versions(self):
        result = self.convert()
        balance = result["opening_balance"]["delivery_round"]
        self.assertEqual([balance[k] for k in ("remaining_promise_qty", "remaining_good_target_qty", "good_not_received_qty", "warehouse_qty", "in_transit_qty")], [17, 11, 1, 3, 2])
        self.assertEqual(result["metadata"]["source_ledger_version"], 7)
        self.assertIsNone(result["metadata"]["server_ledger_version"])
        self.assertEqual(result["metadata"]["projection_state"], "NOT_PROJECTED")
        self.assertEqual(result["metadata"]["full_bom_inventory_coverage"], "NOT_IMPORTED")

    def test_self_reservation_not_subtracted_twice_and_hold_not_available(self):
        row = self.convert()["opening_balance"]["inventory"][0]
        self.assertEqual(row["available_qty"], 12)
        self.assertEqual(row["unreserved_qty"], 10)
        self.assertEqual(row["reserved_self_qty"], 2)

    def test_no_expected_answers_future_events_or_invented_history(self):
        result = self.convert()
        self.assertNotIn("EXPECTED_SENTINEL", json.dumps(result))
        self.assertIsNone(result["opening_balance"]["actual_exposure_count"])
        types = {row["type"] for row in result["entities"]}
        self.assertFalse(types & {"BoardUnit", "InstallationEvent", "ProductionReport", "Shipment", "ArrivalRecord", "FinishedGoodsReceipt"})
        self.assertEqual(result["opening_balance"]["known_installation_count"], 0)
        self.assertEqual(result["opening_balance"]["production_reports"][0]["event_time_coverage"], "UNKNOWN")

    def test_rejects_every_future_collection_even_if_no_events_list(self):
        for key in ("purchase_requests", "purchase_orders", "new_receipts", "board_instances", "installation_events", "coverage_assertions"):
            source = synthetic_baseline()
            source[key] = [{"id": "future"}]
            with self.subTest(key=key), self.assertRaisesRegex(InitialInputError, "INITIAL_FUTURE_STATE_REJECTED"):
                self.convert(source)

    def test_unknown_fields_cannot_become_current_facts(self):
        source = synthetic_baseline()
        source["entities"] = [{"type": "BoardUnit", "id": "future-board"}]
        source["delivery_round"]["future_answer"] = "LEAK_SENTINEL"
        source["inventory"]["lots"][0]["approval_id"] = "LEAK_SENTINEL"
        text = json.dumps(self.convert(source))
        self.assertNotIn("LEAK_SENTINEL", text)
        self.assertNotIn("future-board", text)

    def test_identity_separates_company_run_kind_and_source(self):
        a = self.convert()
        b = self.convert(company_id="different-company")
        c = self.convert(run_id="different-run")
        self.assertFalse({x["id"] for x in a["entities"]} & {x["id"] for x in b["entities"]})
        self.assertFalse({x["id"] for x in a["entities"]} & {x["id"] for x in c["entities"]})
        args = ("company", "profile", "run", "PartLot", "same-display")
        self.assertNotEqual(canonical_id(*args), canonical_id(*args, source_namespace="different-source"))
        self.assertNotEqual(canonical_id(*args), canonical_id("company", "profile", "run", "InternalItem", "same-display"))

    def test_canonical_predicates_match_distinct_contract_line_and_order(self):
        result = self.convert()
        indexed = {e["id"]: e["type"] for e in result["entities"]}
        for edge in result["relationships"]:
            self.assertIn(edge["source"], indexed)
            self.assertIn(edge["target"], indexed)
            if edge["predicate"] == "roundOfContractLine":
                self.assertEqual(indexed[edge["source"]], "DeliveryRound")
                self.assertEqual(indexed[edge["target"]], "ContractLine")
        self.assertNotIn("roundOfOrder", {e["predicate"] for e in result["relationships"]})

    def test_rejects_quantity_reconciliation_and_reservation_errors(self):
        cases = [
            lambda x: x["delivery_round"].update(arrived_qty=6),
            lambda x: x["production_reports"][0].update(good_qty=8),
            lambda x: x["inventory"]["lots"][0].update(on_hand_qty=3),
            lambda x: x["inventory"]["reservations"][0].update(qty=3),
            lambda x: x["inventory"]["lots"][0].update(held_qty=True),
            lambda x: x["inventory"]["reservations"][0].update(reservation_relation_to_selected_demand="SELF"),
        ]
        for mutate in cases:
            source = synthetic_baseline()
            mutate(source)
            with self.subTest(mutate=mutate), self.assertRaises(InitialInputError):
                self.convert(source)

    def test_rejects_applied_bom_and_source_approvals(self):
        cases = [lambda x: x["configuration"].update(registration_status="POSTED"),
                 lambda x: x["configuration"].update(technical_review_status="APPROVED"),
                 lambda x: x["substitutes"][0].update(approval_ref="approval-from-fixture"),
                 lambda x: x["quotations"][0].update(supplier_confirmation=True)]
        for mutate in cases:
            source = synthetic_baseline()
            mutate(source)
            with self.subTest(mutate=mutate), self.assertRaises(InitialInputError):
                self.convert(source)

    def test_time_and_provenance_are_preserved_without_fabricating_event_times(self):
        result = self.convert()
        for row in result["entities"]:
            self.assertEqual(row["evidence"]["record_type"], "opening_balance")
            self.assertEqual(row["evidence"]["source_sha256"], "a"*64)
            self.assertEqual(row["evidence"]["effective_at"], "2025-02-01T10:00:00+00:00")
        for field, value in [("known_as_of", "2025-03-01T00:00:00+00:00"), ("as_of", "2025-02-01T10:00:00")]:
            source = synthetic_baseline()
            source[field] = value
            with self.subTest(field=field), self.assertRaises(InitialInputError):
                self.convert(source)

    def test_conversion_does_not_mutate_source(self):
        source = synthetic_baseline()
        before = deepcopy(source)
        result = self.convert(source)
        result["opening_balance"]["shipments"][0]["arrivals"][0]["qty"] = 0
        result["entities"][0]["attributes"]["registration_status"] = "POSTED"
        self.assertEqual(source, before)

    def test_private_loader_verifies_digest_and_never_reads_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = root / BASELINE_PATH
            baseline.parent.mkdir(parents=True)
            raw = json.dumps(synthetic_baseline()).encode()
            baseline.write_bytes(raw)
            manifest = {"files": [{"path": BASELINE_PATH, "bytes": len(raw), "sha256": sha256(raw).hexdigest()}]}
            (root / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest))
            # No scenario bundle exists: INITIAL must not require it.
            result = load_initial(root, company_id="test-company", run_id="test-run", profile_id="test-profile", imported_at="2025-02-02T12:00:00+00:00")
            self.assertEqual(result["metadata"]["mode"], "INITIAL")
            baseline.write_bytes(raw + b" ")
            with self.assertRaisesRegex(InitialInputError, "INITIAL_SOURCE_INTEGRITY_MISMATCH"):
                load_initial(root, company_id="test-company", run_id="test-run", profile_id="test-profile", imported_at="2025-02-02T12:00:00+00:00")

    def test_loader_rejects_symlink_escape_without_leaking_private_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "inputs"
            root.mkdir()
            outside = Path(temp) / "secret.json"
            outside.write_text("DO_NOT_DISCLOSE")
            (root / "SOURCE_MANIFEST.json").symlink_to(outside)
            with self.assertRaises(InitialInputError) as caught:
                load_initial(root, company_id="c", profile_id="p", run_id="r", imported_at="2025-02-02T12:00:00+00:00")
            self.assertNotIn(str(outside), str(caught.exception))
            self.assertNotIn("DO_NOT_DISCLOSE", str(caught.exception))

    def test_scenario_partition_has_no_initial_or_authoritative_approvals(self):
        source = {"meta": {"real_service_execution": False},
                  "entities": [{"id": "completed-board"}], "relationships": [{"id": "future-edge"}],
                  "events": [{"id": "event-a", "run_id": "scenario-a", "type": "ExampleEvent", "real_service_execution": False,
                              "approval_id": "old-approval", "status": "POSTED_DESIGN", "payload_hash": "old-hash", "payload": {"item_id": "example-item"}}],
                  "snapshots": [{"answer": "EXPECTED_SENTINEL"}], "cq_results": [], "findings": []}
        before = deepcopy(source)
        parts = partition_reference_bundle(source)
        self.assertEqual(set(parts), {"commands", "expected"})
        self.assertFalse(parts["commands"]["executable"])
        candidate = parts["commands"]["candidates"][0]
        self.assertTrue(candidate["requires_new_server_approval"])
        self.assertNotIn("old-approval", json.dumps(parts))
        self.assertNotIn("old-hash", json.dumps(parts))
        self.assertNotIn("POSTED_DESIGN", json.dumps(parts))
        self.assertNotIn("EXPECTED_SENTINEL", json.dumps(parts["commands"]))
        self.assertFalse(parts["expected"]["runtime_retrieval_allowed"])
        self.assertEqual(source, before)

    def test_output_validator_requires_pinned_metadata_and_original_versions(self):
        result = self.convert()
        with self.assertRaises(InitialInputError):
            validate_initial(result)
        result["metadata"]["source_manifest_sha256"] = "b"*64
        validate_initial(result)
        cases = [lambda x: x.update(events=[]),
                 lambda x: x["metadata"].update(mode="EXPECTED"),
                 lambda x: x["metadata"].update(server_ledger_version=7),
                 lambda x: x["metadata"].update(projection_state="READY"),
                 lambda x: x["metadata"].update(source_sha256="unverified"),
                 lambda x: x["metadata"].update(initial_scope="FULL_SCENARIO"),
                 lambda x: x["metadata"].update(synthetic=False)]
        for mutate in cases:
            candidate = deepcopy(result)
            mutate(candidate)
            with self.subTest(mutate=mutate), self.assertRaises(InitialInputError):
                validate_initial(candidate)

    def test_output_validator_rejects_graph_truncation_and_evidence_forgery(self):
        result = self.convert()
        result["metadata"]["source_manifest_sha256"] = "b"*64
        cases = [lambda x: x["entities"].pop(),
                 lambda x: x["relationships"].pop(),
                 lambda x: x["entities"][0].update(type="BoardUnit"),
                 lambda x: x["entities"][0].update(id="forged"),
                 lambda x: x["entities"][0]["attributes"].update(expected_answer="NO"),
                 lambda x: x["entities"][0]["evidence"].update(source_sha256="c"*64),
                 lambda x: x["relationships"][0].update(predicate="installationUsesLot"),
                 lambda x: x["relationships"][0].update(target="missing")]
        for mutate in cases:
            candidate = deepcopy(result)
            mutate(candidate)
            with self.subTest(mutate=mutate), self.assertRaises(InitialInputError):
                validate_initial(candidate)

    def test_output_validator_rejects_nested_future_data_and_balance_changes(self):
        result = self.convert()
        result["metadata"]["source_manifest_sha256"] = "b"*64
        cases = [lambda x: x["opening_balance"]["inventory"][0].update(available_qty=999),
                 lambda x: x["opening_balance"]["inventory"].append(deepcopy(x["opening_balance"]["inventory"][0])),
                 lambda x: x["opening_balance"]["reservations"][0].update(qty=0),
                 lambda x: x["opening_balance"]["quotations"][0].update(expected_result="NO"),
                 lambda x: x["opening_balance"]["substitutes"][0].update(approval_ref="saved-approval"),
                 lambda x: x["opening_balance"]["production_reports"][0].update(board_ids=["invented"]),
                 lambda x: x["opening_balance"]["shipments"][0]["arrivals"][0].update(qty=7),
                 lambda x: x["opening_balance"]["configuration"].update(registration_status="APPROVED")]
        for mutate in cases:
            candidate = deepcopy(result)
            mutate(candidate)
            with self.subTest(mutate=mutate), self.assertRaises(InitialInputError):
                validate_initial(candidate)


if __name__ == "__main__":
    unittest.main()
