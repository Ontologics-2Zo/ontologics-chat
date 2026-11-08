"""Local FastAPI host. Development identity is explicit and loopback only."""

from datetime import datetime
from pathlib import Path
import re
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import Field
import psycopg

from dev.preview_server import CSP, REPO_ROOT, STATIC_FILES, PreviewServer, PreviewUnavailable
from service.contracts import Contract, CreateDocument, DocumentAction, ReviseDocument
from service.bom import BomService
from service.bom_contracts import BomCreate, BomRevise, BomReview
from service.bom_runner import BomRunner
from service.domain import DomainService
from service.errors import ServiceError
from service.gateway import EvidenceRef, ExecutionBudget, ToolContext, ToolGateway, ToolOutput, ToolSpec
from service.identity import IdentityService, csrf_for_token
from service.ledger import LedgerReader
from service.ontology import OntologyCatalog
from service.projections import ProjectionError
from service.views import answer_query, ontology_view, snapshot_view


MAX_REQUEST_BYTES = 128 * 1024
MAX_BOM_REVISION_BYTES = 2 * 1024 * 1024


class SelectIdentity(Contract):
    profile_id: str = Field(min_length=1, max_length=100)


class Query(Contract):
    question: str = Field(min_length=1, max_length=2000)
    ledger_version: int = Field(ge=1)
    context_entity_id: str | None = Field(default=None, min_length=1, max_length=250)


class Snapshot(Contract):
    ledger_version: int = Field(ge=1)


def create_app(db, *, private_root: str, origin="http://127.0.0.1:8870", dev_mode=False,
               reference_preview=False, catalog=None,projector=None,bom_runner=None):
    parsed=urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost","127.0.0.1"} or parsed.username or parsed.password or not parsed.port or parsed.path or parsed.query or parsed.fragment:
        raise ServiceError("LOCAL_ORIGIN_REQUIRED",503)
    identity=IdentityService(db,enabled=dev_mode)
    ledger=LedgerReader(db,identity)
    domain=DomainService(db,identity)
    bom=BomService(db,identity,private_root,bom_runner or BomRunner())
    catalog=catalog or OntologyCatalog.from_private_root(private_root)
    ontology=ontology_view(catalog)
    app=FastAPI(title="Ontologics local MK-S2 service",docs_url=None,redoc_url=None,openapi_url=None)
    app.state.identity,app.state.ledger,app.state.domain=identity,ledger,domain
    app.state.bom=bom
    app.state.catalog=catalog

    def error_response(code,status):
        return JSONResponse({"error":{"code":code}},status_code=status)

    @app.middleware("http")
    async def local_boundary(request,call_next):
        host=request.headers.getlist("host")
        origins=request.headers.getlist("origin")
        # Row review submits all source-row decisions together. Its bounded
        # allowance must not increase unrelated write endpoints' limits.
        body_limit = (MAX_BOM_REVISION_BYTES
                      if request.method == "POST" and re.fullmatch(
                          r"/api/mks2/bom/documents/[^/]+/revise", request.url.path)
                      else MAX_REQUEST_BYTES)
        lengths = request.headers.getlist("content-length")
        declared_size = (int(lengths[0]) if len(lengths) == 1
                         and re.fullmatch(r"[0-9]{1,10}", lengths[0]) else None)
        if host != [parsed.netloc] or len(origins)>1 or (origins and origins[0]!=origin) or request.headers.get("sec-fetch-site") in {"cross-site","same-site"}:
            response=error_response("LOCAL_ORIGIN_REQUIRED",403)
        elif request.method not in {"GET","POST","PATCH","DELETE"}:
            response=error_response("METHOD_NOT_ALLOWED",405)
        elif request.method != "GET" and (origins != [origin] or request.headers.get("x-requested-with")!="MK-S2" or request.headers.get("content-type","").split(";")[0]!="application/json"):
            response=error_response("SAME_ORIGIN_JSON_REQUIRED",403)
        elif request.method != "GET" and (declared_size is None or declared_size > body_limit):
            response=error_response("REQUEST_SIZE_REJECTED",413)
        else:
            try:
                if request.method != "GET":
                    chunks=[];size=0
                    async for chunk in request.stream():
                        size+=len(chunk)
                        if size > body_limit:
                            raise ServiceError("REQUEST_SIZE_REJECTED",413)
                        chunks.append(chunk)
                    if size != declared_size:
                        raise ServiceError("REQUEST_SIZE_REJECTED",413)
                    request._body=b"".join(chunks)
                response=await call_next(request)
            except ServiceError as error:
                response=error_response(error.code,error.status_code)
            except psycopg.Error:
                response=error_response("DATABASE_UNAVAILABLE",503)
            except Exception:
                # Never serialize SQL, source parser errors, request bodies or DSNs.
                response=error_response("SERVICE_UNAVAILABLE",503)
        response.headers.update({"Cache-Control":"no-store","Content-Security-Policy":CSP,
                                 "X-Content-Type-Options":"nosniff","X-Frame-Options":"SAMEORIGIN","Referrer-Policy":"no-referrer"})
        return response

    @app.exception_handler(ServiceError)
    async def safe_service_error(request,error):
        return error_response(error.code,error.status_code)

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request,error):
        return error_response("INVALID_REQUEST",422)

    def token(request):
        return request.cookies.get("onto_session","")

    def principal(request,*,write=False):
        if write:
            return identity.validate_write(token(request),request.headers.get("x-csrf-token"),request.headers.get("x-identity-revision"))
        actor=identity.authenticate(token(request))
        expected=request.headers.get("x-identity-revision")
        if expected and expected!=actor.identity_revision:
            raise ServiceError("IDENTITY_CHANGED",409)
        return actor

    def context(actor,payload):
        meta=payload["metadata"]
        projection=payload.get("projection") or {}
        return ToolContext(execution_id=str(uuid4()),request_id=str(uuid4()),context_revision=actor.identity_revision,
                           company_id=actor.company_id,read_snapshot_id=meta["snapshot_id"],ledger_version=meta["server_ledger_version"],
                           projection_version=projection.get("projection_version"),ontology_version=catalog.version,
                           rule_version=meta["rule_version"],as_of=datetime.fromisoformat(meta["effective_at"]),known_as_of=datetime.fromisoformat(meta["known_as_of"]))

    gateway=ToolGateway(identity.authorize)

    def verified_payload(actor,version):
        payload=ledger.read(actor,version=version)
        projection=payload.get("projection")
        if not projection or projection.get("state")!="READY":
            raise ServiceError("PROJECTION_NOT_READY",409)
        if projector is None:
            raise ServiceError("PROJECTION_READ_UNAVAILABLE",503)
        try:
            graph=projector.read_projection(projection)
        except ProjectionError:
            raise ServiceError("PROJECTION_READ_UNAVAILABLE",503) from None
        payload["projection"]["graph"]=graph
        return payload

    def snapshot_handler(actor,args,ctx):
        payload=verified_payload(actor,args.ledger_version)
        if payload["metadata"]["snapshot_id"] != ctx.read_snapshot_id:
            raise ServiceError("SNAPSHOT_MISMATCH")
        view=snapshot_view(payload)
        view["meta"]["identity_revision"]=actor.identity_revision
        return ToolOutput(data=view,snapshot=ctx,evidence_refs=[EvidenceRef(evidence_id=ctx.read_snapshot_id,source_version=str(ctx.ledger_version),content_hash=payload["metadata"]["source_sha256"])])

    def query_handler(actor,args,ctx):
        payload=verified_payload(actor,args.ledger_version)
        if payload["metadata"]["current_ledger_version"]!=args.ledger_version or payload["metadata"]["snapshot_id"]!=ctx.read_snapshot_id:
            raise ServiceError("SNAPSHOT_CHANGED")
        view=snapshot_view(payload)
        view["meta"]["identity_revision"]=actor.identity_revision
        if view["meta"]["projection_state"]!="READY":
            raise ServiceError("PROJECTION_NOT_READY")
        result=answer_query(args.question,view,ontology,context_entity_id=args.context_entity_id)
        result["execution_id"]=ctx.execution_id
        return ToolOutput(data=result,snapshot=ctx,evidence_refs=[EvidenceRef(evidence_id=ctx.read_snapshot_id,source_version=str(ctx.ledger_version),content_hash=payload["metadata"]["source_sha256"])])

    gateway.register(ToolSpec("ledger.snapshot","ledger.read",False,Snapshot,snapshot_handler))
    gateway.register(ToolSpec("ledger.query","query.read",False,Query,query_handler))

    class BomRevisionCall(BomRevise):
        bom_id: str = Field(min_length=1, max_length=100)

    class BomReviewCall(BomReview):
        bom_id: str = Field(min_length=1, max_length=100)

    class BomActionCall(DocumentAction):
        bom_id: str = Field(min_length=1, max_length=100)

    def bom_handler(action, model):
        def execute(actor,args,ctx):
            values=args.model_dump()
            identifier=values.pop("bom_id",None)
            body=model.model_validate(values)
            method=getattr(bom,action)
            result=method(actor,actor.company_id,body) if action=="create" else method(actor,actor.company_id,identifier,body)
            return ToolOutput(data=result,snapshot=ctx,evidence_refs=[])
        return execute

    for action,model,input_model,permission in (
        ("create",BomCreate,BomCreate,"document.create"),
        ("revise",BomRevise,BomRevisionCall,"document.update"),
        ("review",BomReview,BomReviewCall,"document.review"),
        ("approve",DocumentAction,BomActionCall,"document.approve"),
        ("post",DocumentAction,BomActionCall,"document.post"),
    ):
        gateway.register(ToolSpec("bom."+action,permission,True,input_model,bom_handler(action,model)))
    app.state.gateway=gateway

    @app.get("/health")
    def health():
        return {"status":"ok","mode":"local-development","database":db.health(),"serviceConnected":True,"model_calls":0}

    @app.get("/api/mks2/session")
    def get_session(request:Request):
        profiles=[{"id":row["dev_profile_id"],"label":row["label"],"company_id":row["tenant_id"]} for row in identity.list_accounts()]
        try:
            actor=principal(request)
        except ServiceError as error:
            if error.status_code not in {401,403,409}: raise
            return {"authenticated":False,"profiles":profiles,"principal":None,"csrf_token":None}
        # Profile labels/IDs are presentation. Authority comes from the session.
        with db.connect() as conn:
            record=conn.execute("SELECT dev_profile_id FROM identity_accounts WHERE actor_id=%s",(actor.actor_id,)).fetchone()
        return {"authenticated":True,"profiles":profiles,"principal":actor.public(),"profile_id":record["dev_profile_id"],"csrf_token":csrf_for_token(token(request))}

    @app.post("/api/mks2/session")
    def select_session(request:Request,body:SelectIdentity):
        existing=token(request)
        live=False
        if existing:
            try:
                identity.authenticate(existing);live=True
            except ServiceError as error:
                if error.code not in {"IDENTITY_REQUIRED","IDENTITY_CHANGED","IDENTITY_INACTIVE"}:raise
        credentials=identity.switch(existing,body.profile_id,request.headers.get("x-csrf-token"),request.headers.get("x-identity-revision")) if live else identity.login(body.profile_id)
        response=JSONResponse({"authenticated":True,"principal":credentials.principal.public(),"profile_id":body.profile_id,"csrf_token":credentials.csrf_token})
        response.set_cookie("onto_session",credentials.token,httponly=True,samesite="strict",path="/",max_age=identity.session_ttl_seconds)
        return response

    @app.delete("/api/mks2/session")
    def end_session(request:Request):
        identity.logout(token(request),request.headers.get("x-csrf-token"),request.headers.get("x-identity-revision"))
        response=JSONResponse({"authenticated":False,"principal":None,"csrf_token":None})
        response.delete_cookie("onto_session",path="/",httponly=True,samesite="strict")
        return response

    @app.get("/api/mks2/snapshot")
    def get_snapshot(request:Request):
        actor=principal(request);payload=ledger.read(actor);ctx=context(actor,payload)
        return gateway.execute("ledger.snapshot",{"ledger_version":ctx.ledger_version},principal=actor,context=ctx,budget=ExecutionBudget(ctx.execution_id)).data

    @app.get("/api/mks2/ontology")
    def get_ontology(request:Request):
        actor=principal(request);identity.authorize(actor,actor.company_id,"ontology.read")
        return {**ontology,"meta":{**ontology["meta"],"identity_revision":actor.identity_revision,"company_id":actor.company_id}}

    @app.post("/api/mks2/query")
    def query(request:Request,body:Query):
        actor=principal(request,write=True);payload=ledger.read(actor);ctx=context(actor,payload)
        result=gateway.execute("ledger.query",body.model_dump(),principal=actor,context=ctx,budget=ExecutionBudget(ctx.execution_id,max_tool_calls=1))
        return {**result.data,"execution_id":ctx.execution_id,"tool_name":result.tool_name,"tool_version":result.tool_version}

    @app.get("/api/mks2/events")
    def events(request:Request,after:int=0):
        if after<0:raise ServiceError("INVALID_CURSOR",422)
        actor=principal(request);payload=ledger.read(actor)
        return {"current_ledger_version":payload["metadata"]["current_ledger_version"],"projection_state":(payload.get("projection") or {}).get("state","PENDING"),"events":ledger.events(actor,after),"identity_revision":actor.identity_revision,"company_id":actor.company_id}

    @app.get("/api/mks2/documents")
    def list_documents(request:Request):
        actor=principal(request)
        return {"documents":domain.list(actor,actor.company_id)}

    @app.post("/api/mks2/documents")
    def create_document(request:Request,body:CreateDocument):
        actor=principal(request,write=True)
        return domain.create(actor,actor.company_id,body.content,body.idempotency_key)

    @app.get("/api/mks2/documents/{document_id}")
    def get_document(request:Request,document_id:str):
        actor=principal(request)
        return domain.get(actor,actor.company_id,document_id)

    @app.post("/api/mks2/documents/{document_id}/revise")
    def revise_document(request:Request,document_id:str,body:ReviseDocument):
        actor=principal(request,write=True)
        return domain.revise(actor,actor.company_id,document_id,body.expected_version,body.payload_hash,body.content,body.idempotency_key)

    @app.post("/api/mks2/documents/{document_id}/{action}")
    def document_action(request:Request,document_id:str,action:str,body:DocumentAction):
        if action not in {"review","approve","post"}:raise ServiceError("ACTION_NOT_ALLOWED",404)
        actor=principal(request,write=True)
        return getattr(domain,action)(actor,actor.company_id,document_id,body.expected_version,body.payload_hash,body.idempotency_key)

    @app.get("/api/mks2/operations/{operation_id}")
    def get_operation(request:Request,operation_id:str):
        actor=principal(request)
        return domain.operation(actor,actor.company_id,operation_id)

    @app.get("/api/mks2/bom/sources")
    def bom_sources(request:Request):
        return bom.sources(principal(request))

    @app.get("/api/mks2/bom/documents")
    def bom_documents(request:Request):
        actor=principal(request)
        return {"documents":bom.list(actor,actor.company_id)}

    def execute_bom(request,action,body,bom_id=None):
        actor=principal(request,write=True)
        payload=ledger.read(actor)
        ctx=context(actor,payload)
        args=body.model_dump()
        if bom_id is not None:args["bom_id"]=bom_id
        return gateway.execute("bom."+action,args,principal=actor,context=ctx,
                               budget=ExecutionBudget(ctx.execution_id,max_tool_calls=1)).data

    @app.post("/api/mks2/bom/documents")
    def create_bom(request:Request,body:BomCreate):
        return execute_bom(request,"create",body)

    @app.get("/api/mks2/bom/documents/{bom_id}")
    def get_bom(request:Request,bom_id:str):
        actor=principal(request)
        return bom.get(actor,actor.company_id,bom_id)

    @app.get("/api/mks2/bom/documents/{bom_id}/source")
    def get_bom_source(request:Request,bom_id:str):
        actor=principal(request)
        filename,data=bom.source_bytes(actor,actor.company_id,bom_id)
        return Response(data,media_type="application/octet-stream",
                        headers={"Content-Disposition":"attachment; filename*=UTF-8''"+quote(filename,safe="")})

    @app.post("/api/mks2/bom/documents/{bom_id}/revise")
    def revise_bom(request:Request,bom_id:str,body:BomRevise):
        return execute_bom(request,"revise",body,bom_id)

    @app.post("/api/mks2/bom/documents/{bom_id}/review")
    def review_bom(request:Request,bom_id:str,body:BomReview):
        return execute_bom(request,"review",body,bom_id)

    @app.post("/api/mks2/bom/documents/{bom_id}/{action}")
    def act_bom(request:Request,bom_id:str,action:str,body:DocumentAction):
        if action not in {"approve","post"}:raise ServiceError("ACTION_NOT_ALLOWED",404)
        return execute_bom(request,action,body,bom_id)

    @app.get("/api/preview")
    def preserved_preview():
        # Reuse the existing fixed-file reader without opening a second socket.
        reader=object.__new__(PreviewServer)
        reader.reference_preview,reader.private_root,reader.repo_root=reference_preview,private_root,REPO_ROOT
        try:
            return {"mode":"reference-preview","serviceConnected":False,"bundle":reader.load_bundle()}
        except PreviewUnavailable as error:
            raise ServiceError(str(error),503) from None

    @app.get("/{path:path}")
    def static(request:Request,path:str):
        raw=request.scope.get("raw_path",b"").decode("ascii","ignore")
        if request.url.query or raw!="/"+path or "/"+path not in STATIC_FILES:
            raise ServiceError("NOT_FOUND",404)
        filename,mime=STATIC_FILES["/"+path]
        root=(REPO_ROOT/"web").resolve()
        candidate=root/filename
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root) or not candidate.is_file():
            raise ServiceError("NOT_FOUND",404)
        return Response(candidate.read_bytes(),media_type=mime)

    return app
