"""Full-row graph/reducer checks with synthetic sources and real RDF engines."""

from copy import deepcopy
from hashlib import sha256
import os
import unittest
from uuid import uuid4

from rdflib import Graph

from service.contracts import content_hash
from service.errors import ServiceError
from service.imports import convert_baseline
from service.ledger import apply_bom_event
from service.materials import requirements_for_snapshot
from service.ontology import OntologyCatalog
from service.projections import OntologyProjector
from test_imports import synthetic_baseline


def initial_payload(*,company_id="DEMO-test-bom",run_id="INITIAL"):
    value=convert_baseline(synthetic_baseline(),company_id=company_id,run_id=run_id,profile_id="synthetic-bom",
                           imported_at="2025-02-02T12:00:00+00:00",source_sha256="a"*64)
    value["metadata"]["server_ledger_version"]=1
    return value


def registration_event(previous=None, *, row_count=4):
    previous=previous or initial_payload()
    source={"source_id":"b"*64,"sha256":"c"*64,"parsed_sha256":"d"*64,"filename":"imaginary.csv",
            "bytes":200,"parser_version":"synthetic-test/1","format":"csv","source_kind":"actual_bom"}
    rows=[]
    states=("APPLICABLE","UNRESOLVED","NOT_MOUNTED","SETTING")
    for index in range(1,row_count+1):
        state=states[(index-1)%len(states)]
        row_id=sha256(f"independent-row-{index}".encode()).hexdigest()
        decision={"application_status":state,"quantity_per_unit":"2" if state=="APPLICABLE" else None,
                  "unit":"piece" if state=="APPLICABLE" else None,"item_id":"item-a" if state=="APPLICABLE" else None,
                  "review_basis":"UNREVIEWED" if state=="UNRESOLVED" else "DEMO_ASSUMPTION",
                  "evidence_note":"Independently authored conditional test decision."}
        rows.append({"row_id":row_id,"ordinal":index,"source_row":index+2,"sheet":"imaginary",
                     "locator":f"sheet/imaginary/row/{index+2}","cells":["2",f"X{index}"],
                     "designators":f"X{index}","source_quantity":"2","source_description":"imaginary component",
                     "decision":decision})
    content={"kind":"bom_registration","configuration_id":"config-a","expected_ledger_version":previous["metadata"]["server_ledger_version"],
             "source":source,"rows":rows,"review_note":"Review covers source structure and conditional test choices only.",
             "registration_scope":"SOURCE_WITH_APPLICATION_REVIEW","technical_review":"UNREVIEWED",
             "configuration_binding":"DEMO_CONTEXT_ONLY"}
    bom_id=str(uuid4());digest=content_hash(content)
    event={"event_id":str(uuid4()),"event_type":"BomRegistered","document_id":None,"bom_document_id":bom_id,
           "recorded_at":"2025-02-03T12:00:00+00:00",
           "ledger_version":previous["metadata"]["server_ledger_version"]+1}
    event["payload"]={"event_type":"BomRegistered","company_id":previous["metadata"]["company_id"],
        "ledger_version":event["ledger_version"],"bom_id":bom_id,"bom_version":2,"payload_hash":digest,
        "configuration_id":"config-a","source":source,"rows":rows,"content":content,
        "approval":{"created_by":"author-a","reviewed_by":"reviewer-b","approved_by":"reviewer-b",
                    "approved_version":2,"approved_hash":digest,"approval_policy_version":"test-policy"}}
    return event


class BomReducerTests(unittest.TestCase):
    def test_preserves_baseline_and_all_rows_with_reviewed_applications_only(self):
        original=initial_payload();before=deepcopy(original);event=registration_event(original)
        result=apply_bom_event(original,event)
        self.assertEqual(original,before)
        self.assertEqual(result["opening_balance"],before["opening_balance"])
        self.assertEqual(result["entities"][:len(before["entities"])],before["entities"])
        self.assertEqual(result["relationships"][:len(before["relationships"])],before["relationships"])
        kinds=[entity["type"] for entity in result["entities"]]
        self.assertEqual(kinds.count("SourceRow"),4)
        self.assertEqual(kinds.count("BOMLine"),4)
        self.assertEqual(kinds.count("BOMApplication"),3)
        edges=[edge["predicate"] for edge in result["relationships"]]
        self.assertEqual(edges.count("applicationMapsItem"),1)
        self.assertNotIn("installedOn",edges)
        self.assertNotIn("usesLot",edges)
        self.assertEqual(result["bom"]["payload_hash"],event["payload"]["payload_hash"])
        self.assertEqual(requirements_for_snapshot(result)["unknown_shortage_count"],1)
        self.assertEqual(result["metadata"]["known_as_of"],event["recorded_at"])
        self.assertEqual(result["metadata"]["recorded_at"],event["recorded_at"])
        self.assertEqual(result["metadata"]["effective_at"],before["metadata"]["effective_at"])

    def test_invalid_approval_hash_scope_and_stale_version_fail_closed(self):
        mutations=[lambda event:event["payload"]["approval"].update(approved_by="author-a"),
                   lambda event:event["payload"]["approval"].update(approved_hash="f"*64),
                   lambda event:event["payload"].update(company_id="DEMO-other"),
                   lambda event:event["payload"]["content"].update(expected_ledger_version=99),
                   lambda event:event["payload"]["content"]["rows"][0].update(source_quantity="999")]
        for mutation in mutations:
            event=registration_event();mutation(event)
            with self.subTest(mutation=mutation),self.assertRaises(ServiceError) as caught:
                apply_bom_event(initial_payload(),event)
            self.assertEqual(caught.exception.code,"BOM_EVENT_CONTRACT_INVALID")

    def test_followup_bom_preserves_previous_registered_source_graph(self):
        first=apply_bom_event(initial_payload(),registration_event())
        second=apply_bom_event(first,registration_event(first))
        self.assertEqual(second["entities"][:len(first["entities"])],first["entities"])
        self.assertEqual(second["relationships"][:len(first["relationships"])],first["relationships"])
        self.assertEqual(second["metadata"]["server_ledger_version"],3)
        self.assertEqual(second["opening_balance"],first["opening_balance"])
        self.assertEqual(len(second["bom"]["rows"]),4)

    def test_reducer_deterministic_for_same_event(self):
        original=initial_payload();event=registration_event(original)
        self.assertEqual(apply_bom_event(original,event),apply_bom_event(original,event))

    def test_current_query_highlights_active_bom_while_full_history_remains(self):
        from service.views import answer_query, snapshot_view
        first=apply_bom_event(initial_payload(),registration_event())
        second=apply_bom_event(first,registration_event(first))
        old_ids={entity["id"] for entity in first["entities"] if entity.get("evidence",{}).get("record_type")=="bom_registration"}
        # Query analysis validates real summary/context fields as well as BOM
        # evidence. Use the same read-model builder as the API instead of an
        # incomplete empty-summary stub; projection persistence is tested below.
        second["metadata"].update(current_ledger_version=3, snapshot_id="independent-test-snapshot")
        second["projection"]={"state":"READY","projection_version":3,
                              "graph":{"nodes":second["entities"],"edges":second["relationships"]}}
        view=snapshot_view(second)
        before=deepcopy(view)
        answer=answer_query("BOM 소요량",view,{})
        self.assertFalse(old_ids & set(answer["evidence_node_ids"]))
        self.assertEqual(answer["evidence_refs"][-1]["bom_id"],second["bom"]["bom_id"])
        self.assertEqual(view,before)
        self.assertTrue(old_ids <= {entity["id"] for entity in view["entities"]})


@unittest.skipUnless(os.environ.get("PRIVATE_INPUT_ROOT"),"Full actual schema unavailable; synthetic sources only are public")
class BomFullSchemaProjectionTests(unittest.TestCase):
    def test_all_source_rows_conform_to_unchanged_full_schema_and_sparql(self):
        catalog=OntologyCatalog.from_private_root(os.environ["PRIVATE_INPUT_ROOT"])
        before=catalog.export()["counts"]
        payload=apply_bom_event(initial_payload(),registration_event(row_count=74))
        projection=OntologyProjector(catalog).project(payload,2,"DEMO-test-bom","INITIAL")
        self.assertEqual(projection["validation"]["state"],"CONFORMS",projection["validation"])
        self.assertEqual(projection["graph"]["node_count"],len(payload["entities"]))
        self.assertEqual(projection["graph"]["edge_count"],len(payload["relationships"]))
        rdf=Graph().parse(data=projection["rdf_turtle"],format="turtle")
        count=list(rdf.query("SELECT (COUNT(?row) AS ?count) WHERE { ?row a <urn:ontologics:schema#SourceRow> . }"))[0][0]
        self.assertEqual(int(count),74)
        linked=list(rdf.query("SELECT (COUNT(?line) AS ?count) WHERE { ?bom <urn:ontologics:schema#hasBOMLine> ?line . ?line <urn:ontologics:schema#lineSourceRow> ?row . }"))[0][0]
        self.assertEqual(int(linked),74)
        self.assertEqual(catalog.export()["counts"],before)


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"),"Actual PostgreSQL test requires TEST_DATABASE_URL")
class BomLedgerPersistenceTests(unittest.TestCase):
    def setUp(self):
        # Reuse the independent domain fixture, whose parser is explicitly a
        # double; this class tests real PostgreSQL reduction, not file isolation.
        from test_bom import BomDomainTests
        self.context=BomDomainTests("test_durable_all_rows_review_approval_event_and_no_inventory_mutation")
        self.context.setUp()
        self.addCleanup(self.context.doCleanups)

    def test_post_reduce_repeat_and_reopen_preserve_original_facts(self):
        from service.ledger import LedgerReader
        context=self.context
        original=context.ledger.read(context.author)
        approved=context.approved()
        posted=context.action("post",context.author,approved,"projection-post")
        self.assertEqual(context.ledger.advance_documents(),1)
        self.assertEqual(context.ledger.advance_documents(),0)
        resumed=LedgerReader(context.db,context.identity)
        latest=resumed.read(context.author)
        oldest=resumed.read(context.author,version=1)
        self.assertEqual(latest["metadata"]["server_ledger_version"],2)
        self.assertEqual(latest["bom"]["bom_id"],posted["id"])
        self.assertEqual(latest["opening_balance"],original["opening_balance"])
        self.assertEqual(oldest["entities"],original["entities"])
        self.assertEqual(oldest["relationships"],original["relationships"])
        self.assertEqual(oldest["opening_balance"],original["opening_balance"])
        self.assertNotIn("bom",oldest)
        self.assertIsNone(latest["projection"])
        self.assertGreater(latest["metadata"]["known_as_of"],original["metadata"]["known_as_of"])
        calculated=requirements_for_snapshot(latest)
        self.assertEqual(calculated["row_count"],3)
        self.assertEqual(calculated["items"][0]["required_qty"],"22")
        self.assertEqual(calculated["items"][0]["shortage_qty"],"10")
        self.assertEqual(calculated["unknown_shortage_count"],1)
        with context.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM service_snapshots").fetchone()["n"],2)


@unittest.skipUnless(os.environ.get("PRIVATE_INPUT_ROOT") and os.environ.get("TEST_NEO4J_URI") and os.environ.get("TEST_NEO4J_PASSWORD"),
                     "Actual full-schema Neo4j test requires configured test connection")
class BomActualNeo4jProjectionTests(unittest.TestCase):
    def test_complete_registered_graph_is_published_and_read_at_exact_version(self):
        from neo4j import GraphDatabase
        catalog=OntologyCatalog.from_private_root(os.environ["PRIVATE_INPUT_ROOT"])
        scope="DEMO-bom-test-"+uuid4().hex
        initial=initial_payload(company_id=scope,run_id="BOM-TEST")
        payload=apply_bom_event(initial,registration_event(initial,row_count=74))
        driver=GraphDatabase.driver(os.environ["TEST_NEO4J_URI"],
            auth=(os.environ.get("TEST_NEO4J_USER","neo4j"),os.environ["TEST_NEO4J_PASSWORD"]))
        self.addCleanup(driver.close)
        projector=OntologyProjector(catalog,driver)
        projection=projector.project(payload,2,scope,"BOM-TEST")
        key=projection["snapshot_key"]

        def clean_created_scope():
            # This test owns this randomly generated scope only; existing
            # application snapshots, schema and private source data are intact.
            with driver.session() as session:
                session.run("MATCH (n:OntologyEntity {snapshot_key:$key}) DETACH DELETE n",key=key).consume()
                session.run("MATCH (n:OntologySnapshot {key:$key}) DELETE n",key=key).consume()

        self.addCleanup(clean_created_scope)
        self.assertEqual(projection["state"],"READY",projection["validation"])
        self.assertEqual(projection["validation"]["state"],"CONFORMS")
        live=projector.read_projection(projection)
        self.assertEqual(live,projection["graph"])
        self.assertEqual(sum(row["type"]=="SourceRow" for row in live["nodes"]),74)
        self.assertEqual(sum(row["type"]=="BOMLine" for row in live["nodes"]),74)
        self.assertEqual(sum(row["predicate"]=="lineSourceRow" for row in live["edges"]),74)
        self.assertEqual(projector.project(payload,2,scope,"BOM-TEST")["graph"],live)


if __name__ == "__main__":
    unittest.main()
