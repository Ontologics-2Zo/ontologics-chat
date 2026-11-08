"""Start the explicit local-development host with an external runtime file."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
from threading import Event, Thread

from neo4j import GraphDatabase
import uvicorn

from dev.service_stack import load_runtime, runtime_path, StackError
from service.db import Database
from service.agent_config import load_agent_settings
from service.errors import ServiceError
from service.identity import IdentityService
from service.imports import InitialInputError, load_initial
from service.ledger import LedgerReader
from service.main import create_app
from service.ontology import OntologyCatalog, OntologyInputError
from service.projections import OntologyProjector, ProjectionError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-file",required=True)
    parser.add_argument("--port",type=int,default=8870)
    parser.add_argument("--dev-mode",action="store_true")
    parser.add_argument("--reference-preview",action="store_true")
    parser.add_argument("--import-initial",action="store_true")
    parser.add_argument("--agent-env-file",help="Optional private Agent settings; does not override DB runtime")
    args=parser.parse_args()
    if not 1<=args.port<=65535:
        parser.error("INVALID_PORT")
    private_root=os.environ.get("PRIVATE_INPUT_ROOT")
    if not private_root:
        parser.error("PRIVATE_INPUT_UNAVAILABLE")
    driver=None
    thread=None
    stop=Event()
    try:
        settings=load_runtime(runtime_path(args.runtime_file),(55432,57474,57687))
        agent_settings=load_agent_settings(args.agent_env_file,environ=os.environ)
        db=Database(settings["DATABASE_URL"]);db.migrate()
        identity=IdentityService(db,enabled=args.dev_mode)
        if args.dev_mode:identity.provision_dev_accounts()
        ledger=LedgerReader(db,identity)
        catalog=OntologyCatalog.from_private_root(Path(private_root))
        if args.import_initial:
            payload=load_initial(private_root,company_id="DEMO-A",run_id="INITIAL",profile_id="initial-baseline",imported_at=datetime.now(timezone.utc).isoformat())
            ledger.install_initial(payload,local_development=args.dev_mode)
        driver=GraphDatabase.driver(settings.get("NEO4J_URI","bolt://127.0.0.1:57687"),auth=("neo4j",settings["NEO4J_PASSWORD"]),connection_timeout=5)
        driver.verify_connectivity()
        projector=OntologyProjector(catalog,driver)

        def project_pending():
            if hasattr(ledger,"advance_documents"):ledger.advance_documents()
            for row in ledger.pending():
                result=projector.project(row["payload"],row["ledger_version"],row["company_id"],row["run_id"])
                rdf=result.pop("rdf_turtle")
                ledger.save_projection(row["company_id"],row["run_id"],row["ledger_version"],result,rdf)

        project_pending()
        app=create_app(db,private_root=private_root,origin=f"http://127.0.0.1:{args.port}",dev_mode=args.dev_mode,
                       reference_preview=args.reference_preview,catalog=catalog,projector=projector,
                       agent_settings=agent_settings,
                       agent_receipt_path=Path(args.runtime_file).resolve().parent / 'agent-observability.sqlite3')

        def worker():
            while not stop.wait(2):
                try:project_pending()
                except Exception:
                    # No SQL/credentials/private payload is sent to logs.
                    app.state.projection_worker="RETRY_PENDING"
                else:app.state.projection_worker="READY"

        thread=Thread(target=worker,daemon=True);thread.start()
        print(f"Local MK-S2 service: http://127.0.0.1:{args.port}/ (this machine only)",flush=True)
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
