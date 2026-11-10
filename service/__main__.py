"""Start the explicit local-development host with an external runtime file."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
from threading import Event, Thread

from neo4j import GraphDatabase
import uvicorn

from dev.service_stack import load_database_url, load_runtime, runtime_path, StackError
from service.db import Database
from service.agent_config import load_agent_settings
from service.errors import ServiceError
from service.identity import IdentityService
from service.imports import InitialInputError, load_initial
from service.ledger import LedgerReader
from service.main import create_app
from service.ontology import OntologyCatalog, OntologyInputError
from service.projections import OntologyProjector, ProjectionError


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-file",required=True)
    parser.add_argument("--port",type=int,default=8870)
    parser.add_argument("--origin-host",choices=("127.0.0.1","localhost"),default="127.0.0.1",
                        help="Exact loopback browser origin; use a separate host for an independent demo session")
    parser.add_argument("--postgres-port",type=int,default=55432)
    parser.add_argument("--neo4j-http-port",type=int,default=57474)
    parser.add_argument("--neo4j-bolt-port",type=int,default=57687)
    parser.add_argument("--dev-mode",action="store_true")
    parser.add_argument("--reference-preview",action="store_true")
    parser.add_argument("--import-initial",action="store_true")
    parser.add_argument("--agent-env-file",help="Optional private Agent settings; does not override DB runtime")
    parser.add_argument("--model-budget-runtime-file",
                        help="Keep model budget records in another local runtime's PostgreSQL (one shared budget)")
    parser.add_argument("--model-budget-postgres-port",type=int)
    args=parser.parse_args(argv)
    if not 1<=args.port<=65535:
        parser.error("INVALID_PORT")
    ports=(args.postgres_port,args.neo4j_http_port,args.neo4j_bolt_port)
    if not all(1024<=port<=65535 for port in ports) or len(set((*ports,args.port)))!=4:
        parser.error("STACK_ARGUMENT_INVALID")
    if bool(args.model_budget_runtime_file)!=(args.model_budget_postgres_port is not None) or (
            args.model_budget_postgres_port is not None and (not 1024<=args.model_budget_postgres_port<=65535
                                                            or args.model_budget_postgres_port in (*ports,args.port))):
        parser.error("MODEL_BUDGET_ARGUMENT_INVALID")
    return args


def main():
    args=parse_args()
    private_root=os.environ.get("PRIVATE_INPUT_ROOT")
    if not private_root:
        print("PRIVATE_INPUT_UNAVAILABLE",flush=True)
        raise SystemExit(2)
    driver=None
    thread=None
    stop=Event()
    try:
        settings=load_runtime(runtime_path(args.runtime_file),
                              (args.postgres_port,args.neo4j_http_port,args.neo4j_bolt_port))
        agent_settings=load_agent_settings(args.agent_env_file,environ=os.environ)
        db=Database(settings["DATABASE_URL"]);db.migrate()
        # The shared budget database belongs to another service: never migrate it here.
        budget_db=(Database(load_database_url(runtime_path(args.model_budget_runtime_file),args.model_budget_postgres_port))
                   if args.model_budget_runtime_file else None)
        identity=IdentityService(db,enabled=args.dev_mode)
        if args.dev_mode:identity.provision_dev_accounts()
        ledger=LedgerReader(db,identity)
        catalog=OntologyCatalog.from_private_root(Path(private_root))
        if args.import_initial:
            payload=load_initial(private_root,company_id="DEMO-A",run_id="INITIAL",profile_id="initial-baseline",imported_at=datetime.now(timezone.utc).isoformat())
            ledger.install_initial(payload,local_development=args.dev_mode)
        driver=GraphDatabase.driver(settings.get("NEO4J_URI",f"bolt://127.0.0.1:{args.neo4j_bolt_port}"),auth=("neo4j",settings["NEO4J_PASSWORD"]),connection_timeout=5)
        driver.verify_connectivity()
        projector=OntologyProjector(catalog,driver)

        def project_pending():
            if hasattr(ledger,"advance_documents"):ledger.advance_documents()
            for row in ledger.pending():
                result=projector.project(row["payload"],row["ledger_version"],row["company_id"],row["run_id"])
                rdf=result.pop("rdf_turtle")
                ledger.save_projection(row["company_id"],row["run_id"],row["ledger_version"],result,rdf)

        project_pending()
        from service.retrieval_embedding import LocalMultilingualEmbedding
        retrieval_settings = Path(args.runtime_file).resolve().parent / "model-settings.json"
        retrieval_embedding = (LocalMultilingualEmbedding.from_settings(retrieval_settings)
                               if retrieval_settings.is_file() else None)
        origin=f"http://{args.origin_host}:{args.port}"
        app=create_app(db,private_root=private_root,origin=origin,dev_mode=args.dev_mode,
                       reference_preview=args.reference_preview,catalog=catalog,projector=projector,
                       agent_settings=agent_settings, model_budget_db=budget_db, retrieval_embedding=retrieval_embedding,
                       agent_receipt_path=Path(args.runtime_file).resolve().parent / 'agent-observability.sqlite3')

        def worker():
            while not stop.wait(2):
                try:project_pending()
                except Exception:
                    # No SQL/credentials/private payload is sent to logs.
                    app.state.projection_worker="RETRY_PENDING"
                else:app.state.projection_worker="READY"

        thread=Thread(target=worker,daemon=True);thread.start()
        print(f"Local MK-S2 service: {origin}/ (this machine only)",flush=True)
        uvicorn.run(app,host="127.0.0.1",port=args.port,access_log=False,log_level="warning")
    except (ServiceError,StackError,InitialInputError,OntologyInputError,ProjectionError) as error:
        print(getattr(error,"code",str(error)),flush=True)
        raise SystemExit(1) from None
    except Exception:
        print("SERVICE_STARTUP_FAILED",flush=True)
        raise SystemExit(1) from None
    finally:
        stop.set()
        if thread:thread.join(timeout=10)
        if driver:driver.close()


if __name__=="__main__":main()
