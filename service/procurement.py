"""Internal purchasing documents with independent exact-version approval.

This development slice records explicitly hypothetical offers and local workflow
events. It cannot transmit orders, certify supplier acceptance or change opening
balances. Only independently approved inspection passes release current stock.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING, localcontext
from hashlib import sha256
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from service.contracts import CANONICAL_VERSION, DocumentAction, canonical_bytes, content_hash
from service.domain import DomainService, _public
from service.errors import ServiceError
from service.materials import requirements_for_snapshot
from service.procurement_contracts import CONTENT_TYPES, ProcurementCreate, ProcurementReject, ProcurementRevise


def quote_calculation(quote, shortage):
    """Exact MOQ/multiple/cost calculation; unknown freight keeps total unknown."""
    with localcontext() as context:
        context.prec = 80
        shortage = Decimal(str(shortage))
        if not shortage.is_finite() or shortage < 0:
            raise ServiceError("PROCUREMENT_SHORTAGE_UNKNOWN", 409)
        needed = int(shortage.to_integral_value(rounding=ROUND_CEILING))
        multiple = quote["order_multiple"]
        quantity = ((max(needed, quote["moq"]) + multiple - 1) // multiple) * multiple if needed else 0
        subtotal = Decimal(quote["unit_price"]) * quantity
        total = subtotal + Decimal(quote["freight"]) if quote["freight"] is not None else None
    limit = quote.get("supply_limit_qty")
    if limit is None:
        condition, reason = "UNKNOWN", "SUPPLY_LIMIT_UNKNOWN"
    elif quantity > limit:
        condition, reason = "NOT_MET", "ORDER_EXCEEDS_SUPPLY_LIMIT"
    else:
        condition, reason = "MET", None
    return {"shortage_qty": format(shortage, "f"), "recommended_qty": quantity,
            "required_qty": needed, "margin_qty": quantity - needed,
            "supply_limit_qty": limit, "quantity_condition": condition, "quantity_condition_reason": reason,
            "limit_shortfall_qty": max(needed - limit, 0) if limit is not None else None,
            "subtotal": format(subtotal, "f"), "freight": quote["freight"],
            "total": format(total, "f") if total is not None else None,
            "currency": quote["currency"], "freight_status": "KNOWN" if total is not None else "UNKNOWN"}


class ProcurementService(DomainService):
    @staticmethod
    def _snapshot(conn, company, expected):
        state = conn.execute("SELECT version FROM ledger_state WHERE company_id=%s FOR UPDATE", (company,)).fetchone()
        if not state or state["version"] != expected:
            raise ServiceError("PROCUREMENT_LEDGER_VERSION_CHANGED", 409)
        row = conn.execute("SELECT payload FROM service_snapshots WHERE company_id=%s AND run_id='INITIAL' AND ledger_version=%s",
                           (company, expected)).fetchone()
        if not row:
            raise ServiceError("PROCUREMENT_LEDGER_NOT_READY", 409)
        return row["payload"]

    def _document(self, conn, company, document_id, *, lock=False):
        try:
            identifier = UUID(str(document_id))
        except (ValueError, TypeError, AttributeError):
            raise ServiceError("PROCUREMENT_NOT_FOUND", 404) from None
        row = conn.execute("SELECT * FROM procurement_documents WHERE company_id=%s AND id=%s" + (" FOR UPDATE" if lock else ""),
                           (company, identifier)).fetchone()
        if not row:
            raise ServiceError("PROCUREMENT_NOT_FOUND", 404)
        return row

    @staticmethod
    def _audit(conn, principal, document, action):
        conn.execute("INSERT INTO procurement_audit(audit_id,company_id,actor_id,action,document_id,details) VALUES (%s,%s,%s,%s,%s,%s)",
            (uuid4(), document["company_id"], principal.actor_id, action, document["id"], Jsonb({
                "version": document["version"], "payload_hash": document["payload_hash"], "status": document["status"],
                "identity_revision": principal.identity_revision, "rejection_reason": document.get("rejection_reason")})))

    @staticmethod
    def _revision(conn, principal, document):
        conn.execute("INSERT INTO procurement_revisions(document_id,version,content,canonical_bytes,payload_hash,actor_id) VALUES (%s,%s,%s,%s,%s,%s)",
            (document["id"], document["version"], Jsonb(document["content"]), document["canonical_bytes"], document["payload_hash"], principal.actor_id))

    @staticmethod
    def _reference(document):
        return {"id": str(document["id"]), "kind": document["kind"], "version": document["version"],
                "payload_hash": document["payload_hash"], "ledger_version": document["ledger_version"]}

    def _parent(self, conn, company, identifier, kind, *, allow_cancelled=False):
        row = self._document(conn, company, identifier, lock=True)
        if row["kind"] != kind or row["status"] != "POSTED":
            raise ServiceError("PROCUREMENT_POSTED_PARENT_REQUIRED", 409)
        if row["cancelled_by"] and not allow_cancelled:
            raise ServiceError("PROCUREMENT_PARENT_CANCELLED", 409)
        self._exact(row, row["version"], row["payload_hash"])
        return row

    @staticmethod
    def _used(conn, document):
        if document["kind"] == "PR":
            # Cancelling an order releases only its unreceived allocation.
            rows = conn.execute("SELECT id,quantity,cancelled_by FROM procurement_documents WHERE parent_id=%s AND kind='PO' AND status='POSTED'",
                                (document["id"],)).fetchall()
            total = 0
            for row in rows:
                total += (conn.execute("SELECT COALESCE(sum(quantity),0) AS n FROM procurement_documents WHERE parent_id=%s AND kind='RECEIPT' AND status='POSTED'", (row["id"],)).fetchone()["n"]
                          if row["cancelled_by"] else row["quantity"])
            return int(total)
        child = "RECEIPT" if document["kind"] == "PO" else "IQC"
        return int(conn.execute("SELECT COALESCE(sum(quantity),0) AS n FROM procurement_documents WHERE parent_id=%s AND kind=%s AND status='POSTED'",
                                (document["id"], child)).fetchone()["n"])

    @staticmethod
    def _confirmed(conn, order):
        return int(conn.execute("SELECT COALESCE(sum(quantity),0) AS n FROM procurement_documents WHERE parent_id=%s AND kind='SUPPLY_CONFIRMATION' AND status='POSTED'",
                                (order["id"],)).fetchone()["n"])

    @staticmethod
    def _entity(payload, kind, identifier):
        matches = [row for row in payload["entities"] if row["type"] == kind and identifier in {row["id"], row["display_id"]}]
        if len(matches) != 1:
            raise ServiceError("PROCUREMENT_ENTITY_UNCONFIRMED", 422)
        return matches[0]

    @staticmethod
    def _valid_quote(quote):
        if date.fromisoformat(quote["valid_until"]) < datetime.now(timezone.utc).date():
            raise ServiceError("PROCUREMENT_QUOTE_EXPIRED", 409)

    def _resolve(self, conn, company, payload, content):
        kind = content.kind
        value = content.model_dump(mode="json")
        if kind == "QUOTE":
            self._valid_quote(value)
            item = self._entity(payload, "InternalItem", content.item_id)
            supplier = self._entity(payload, "Supplier", content.supplier_id)
            inventories = payload.get("current_inventory", payload["opening_balance"]["inventory"])
            units = {row["unit"] for row in inventories if row["item_id"] == item["display_id"]}
            if not units or units != {content.unit}:
                raise ServiceError("PROCUREMENT_UNIT_UNCONFIRMED", 409)
            return {"item_id": item["display_id"], "item_entity_id": item["id"], "unit": content.unit,
                    "supplier_id": supplier["display_id"], "supplier_entity_id": supplier["id"],
                    "supplier_confirmation": False, "transmission_status": "NOT_SENT"}
        if kind == "PR":
            parent = self._parent(conn, company, content.quote_id, "QUOTE")
            quote = parent["content"]
            self._valid_quote(quote)
            requirements = requirements_for_snapshot(payload)
            if requirements["state"] == "BLOCKED_BOM":
                raise ServiceError("PROCUREMENT_POSTED_BOM_REQUIRED", 409)
            if not requirements["full_coverage"] and not content.acknowledge_partial_bom:
                raise ServiceError("PROCUREMENT_PARTIAL_BOM_ACK_REQUIRED", 409)
            matches = [row for row in requirements["items"] if row["item_id"] == quote["resolved"]["item_id"] and row["unit"] == quote["unit"]]
            key = "planned_shortage_qty" if content.material_basis == "planned" else "shortage_qty"
            if len(matches) != 1 or matches[0][key] is None:
                raise ServiceError("PROCUREMENT_SHORTAGE_UNKNOWN", 409)
            calculation = quote_calculation(quote, matches[0][key])
            if not calculation["recommended_qty"] or content.quantity != calculation["recommended_qty"]:
                raise ServiceError("PROCUREMENT_QUANTITY_CHANGED", 409)
            if calculation["quantity_condition"] == "NOT_MET":
                raise ServiceError("PROCUREMENT_SUPPLY_LIMIT_EXCEEDED", 409)
            # Two active requests must not procure the same selected demand twice.
            active = conn.execute("SELECT * FROM procurement_documents WHERE company_id=%s AND kind='PR' AND status='POSTED'", (company,)).fetchall()
            for old in active:
                # A BOM revision must not hide still-open purchases for this
                # same item and selected demand. Settle/cancel them first.
                if old["content"]["resolved"]["item_id"] != quote["resolved"]["item_id"]:
                    continue
                pending = not old["cancelled_by"] and self._used(conn, old) < old["quantity"]
                orders = conn.execute("SELECT * FROM procurement_documents WHERE parent_id=%s AND kind='PO' AND status='POSTED'", (old["id"],)).fetchall()
                for order in orders:
                    pending |= not order["cancelled_by"] and self._used(conn, order) < order["quantity"]
                    receipts = conn.execute("SELECT * FROM procurement_documents WHERE parent_id=%s AND kind='RECEIPT' AND status='POSTED'", (order["id"],)).fetchall()
                    pending |= any(self._used(conn, receipt) < receipt["quantity"] for receipt in receipts)
                if pending:
                    raise ServiceError("PROCUREMENT_DEMAND_ALREADY_REQUESTED", 409)
            return {**quote["resolved"], "parent": self._reference(parent), "quote": quote,
                    "bom_id": requirements["bom_id"], "bom_version": requirements["bom_version"],
                    "material_rule_version": requirements["rule_version"], "calculation": calculation,
                    "partial_bom": not requirements["full_coverage"], "basis": requirements["basis"]}
        if kind == "PO":
            parent = self._parent(conn, company, content.pr_id, "PR")
            resolved = parent["content"]["resolved"]
            quote = resolved["quote"]
            self._valid_quote(quote)
            remaining = parent["quantity"] - self._used(conn, parent)
            if content.quantity > remaining:
                raise ServiceError("PROCUREMENT_ORDER_EXCEEDS_REQUEST", 409)
            if content.quantity < quote["moq"] or content.quantity % quote["order_multiple"]:
                raise ServiceError("PROCUREMENT_ORDER_PACKAGING_INVALID", 422)
            if quote["freight"] is None and not content.acknowledge_unknown_freight:
                raise ServiceError("PROCUREMENT_FREIGHT_ACK_REQUIRED", 409)
            calculation = quote_calculation(quote, content.quantity)
            if calculation["quantity_condition"] == "NOT_MET":
                raise ServiceError("PROCUREMENT_SUPPLY_LIMIT_EXCEEDED", 409)
            return {**resolved, "parent": self._reference(parent), "remaining_before": remaining,
                    "calculation": calculation, "supplier_confirmation": False, "transmission_status": "NOT_SENT"}
        if kind == "SUPPLY_CONFIRMATION":
            parent = self._parent(conn, company, content.po_id, "PO")
            remaining = parent["quantity"] - self._confirmed(conn, parent)
            if content.confirmed_qty > remaining:
                raise ServiceError("PROCUREMENT_OVER_CONFIRMATION", 409)
            return {**parent["content"]["resolved"], "parent": self._reference(parent),
                    "unconfirmed_before": remaining, "confirmation_mode": "INTERNAL_SIMULATED",
                    "supplier_confirmation": False, "promised_arrival": None, "transmission_status": "NOT_SENT"}
        if kind == "RECEIPT":
            parent = self._parent(conn, company, content.po_id, "PO")
            remaining = parent["quantity"] - self._used(conn, parent)
            if content.quantity > remaining:
                raise ServiceError("PROCUREMENT_OVER_RECEIPT", 409)
            receivable = self._confirmed(conn, parent) - self._used(conn, parent)
            if content.quantity > receivable:
                raise ServiceError("PROCUREMENT_SUPPLY_CONFIRMATION_REQUIRED", 409)
            if any(row.get("lot_id") == content.lot_code for row in payload["opening_balance"]["inventory"]):
                raise ServiceError("PROCUREMENT_LOT_REFERENCE_CONFLICT", 409)
            resolved = parent["content"]["resolved"]
            return {**resolved, "parent": self._reference(parent), "remaining_before": remaining,
                    "confirmed_receivable_before": receivable, "inspection_pending_qty": content.quantity, "available_delta": 0}
        if kind == "IQC":
            parent = self._parent(conn, company, content.receipt_id, "RECEIPT")
            remaining = parent["quantity"] - self._used(conn, parent)
            if content.passed_qty + content.failed_qty > remaining:
                raise ServiceError("PROCUREMENT_OVER_INSPECTION", 409)
            return {**parent["content"]["resolved"], "parent": self._reference(parent),
                    "remaining_before": remaining, "receipt_qty": parent["quantity"],
                    "lot_code": parent["lot_code"], "available_delta": content.passed_qty}
        target = self._document(conn, company, content.target_id, lock=True)
        if target["kind"] not in {"PR", "PO"}:
            raise ServiceError("PROCUREMENT_CANCELLATION_KIND_FORBIDDEN", 409)
        target = self._parent(conn, company, content.target_id, target["kind"])
        remaining = target["quantity"] - self._used(conn, target)
        if remaining <= 0:
            raise ServiceError("PROCUREMENT_NOTHING_TO_CANCEL", 409)
        return {"parent": self._reference(target), "cancelled_remaining_qty": remaining,
                "inventory_delta": 0, "supplier_transmission": "NOT_SENT"}

    @staticmethod
    def _columns(content, resolved):
        quantity = getattr(content, "quantity", 0)
        if content.kind == "IQC":
            quantity = content.passed_qty + content.failed_qty
        elif content.kind == "SUPPLY_CONFIRMATION":
            quantity = content.confirmed_qty
        key = next((getattr(content, name) for name in ("quote_ref", "confirmation_ref", "receipt_ref", "inspection_ref") if hasattr(content, name)), None)
        return (resolved.get("parent", {}).get("id"), key, getattr(content, "lot_code", None), quantity,
                getattr(content, "passed_qty", 0), getattr(content, "failed_qty", 0))

    @staticmethod
    def _unique(conn, company, kind, business_key, lot_code, excluding=None):
        rows = conn.execute("SELECT id,business_key,lot_code,kind FROM procurement_documents WHERE company_id=%s AND ((kind=%s AND business_key=%s) OR lot_code=%s)",
                            (company, kind, business_key, lot_code)).fetchall()
        if any(str(row["id"]) != str(excluding) for row in rows):
            raise ServiceError("PROCUREMENT_REFERENCE_ALREADY_EXISTS", 409)

    def create(self, principal, company_id, body: ProcurementCreate):
        body = ProcurementCreate.model_validate(body)
        def work(conn):
            payload = self._snapshot(conn, company_id, body.expected_ledger_version)
            resolved = self._resolve(conn, company_id, payload, body.content)
            value = {**body.content.model_dump(mode="json"), "basis_ledger_version": body.expected_ledger_version, "resolved": resolved}
            raw = canonical_bytes(value)
            columns = self._columns(body.content, resolved)
            self._unique(conn, company_id, body.content.kind, columns[1], columns[2])
            document = conn.execute("INSERT INTO procurement_documents(id,company_id,kind,parent_id,business_key,lot_code,quantity,passed_qty,failed_qty,content,canonical_bytes,payload_hash,canonical_version,version,status,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,'DRAFT',%s) RETURNING *",
                (uuid4(), company_id, body.content.kind, *columns, Jsonb(value), raw, sha256(raw).hexdigest(), CANONICAL_VERSION, principal.actor_id)).fetchone()
            self._revision(conn, principal, document)
            self._audit(conn, principal, document, "procurement.create")
            return document
        return self._run(principal, company_id, "document.create", body.idempotency_key,
                         {"procurement_action": "create", **body.model_dump(mode="json")}, work)

    def revise(self, principal, company_id, document_id, body: ProcurementRevise):
        body = ProcurementRevise.model_validate(body)
        def work(conn):
            document = self._document(conn, company_id, document_id, lock=True)
            self._exact(document, body.expected_version, body.payload_hash)
            if document["status"] == "POSTED":
                raise ServiceError("POSTED_DOCUMENT_IMMUTABLE", 409)
            if document["created_by"] != principal.actor_id:
                raise ServiceError("DOCUMENT_AUTHOR_REQUIRED", 403)
            if body.content.kind != document["kind"]:
                raise ServiceError("PROCUREMENT_KIND_IMMUTABLE", 422)
            payload = self._snapshot(conn, company_id, body.expected_ledger_version)
            resolved = self._resolve(conn, company_id, payload, body.content)
            value = {**body.content.model_dump(mode="json"), "basis_ledger_version": body.expected_ledger_version, "resolved": resolved}
            raw = canonical_bytes(value)
            columns = self._columns(body.content, resolved)
            self._unique(conn, company_id, body.content.kind, columns[1], columns[2], document_id)
            document = conn.execute("UPDATE procurement_documents SET parent_id=%s,business_key=%s,lot_code=%s,quantity=%s,passed_qty=%s,failed_qty=%s,content=%s,canonical_bytes=%s,payload_hash=%s,version=version+1,status='DRAFT',reviewed_by=NULL,approved_by=NULL,approved_version=NULL,approved_hash=NULL,approval_policy_version=NULL,rejection_reason=NULL,updated_at=now() WHERE id=%s RETURNING *",
                (*columns, Jsonb(value), raw, sha256(raw).hexdigest(), document["id"])).fetchone()
            self._revision(conn, principal, document)
            self._audit(conn, principal, document, "procurement.revise")
            return document
        return self._run(principal, company_id, "document.update", body.idempotency_key,
                         {"procurement_action": "revise", "document_id": document_id, **body.model_dump(mode="json")}, work)

    def _validate_current(self, conn, company, document):
        value = document["content"]
        payload = self._snapshot(conn, company, value["basis_ledger_version"])
        content = CONTENT_TYPES[document["kind"]].model_validate({k: v for k, v in value.items() if k not in {"resolved", "basis_ledger_version"}})
        resolved = self._resolve(conn, company, payload, content)
        if content_hash(resolved) != content_hash(value["resolved"]):
            raise ServiceError("PROCUREMENT_BASIS_CHANGED", 409)
        return content

    def _action(self, principal, company_id, document_id, body, action):
        body = DocumentAction.model_validate(body)
        permission = {"review": "document.review", "approve": "document.approve", "post": "document.post"}[action]
        def work(conn):
            document = self._document(conn, company_id, document_id, lock=True)
            self._exact(document, body.expected_version, body.payload_hash)
            if action in {"review", "approve"} and document["created_by"] == principal.actor_id:
                raise ServiceError("SELF_REVIEW_FORBIDDEN" if action == "review" else "SELF_APPROVAL_FORBIDDEN", 403)
            expected = {"review": "DRAFT", "approve": "REVIEWED", "post": "APPROVED"}[action]
            if document["status"] != expected:
                raise ServiceError("PROCUREMENT_STATE_CHANGED", 409)
            self._validate_current(conn, company_id, document)
            if action == "review":
                document = conn.execute("UPDATE procurement_documents SET status='REVIEWED',reviewed_by=%s,updated_at=now() WHERE id=%s RETURNING *", (principal.actor_id, document["id"])).fetchone()
            elif action == "approve":
                self.identity.require_actor(document["reviewed_by"], company_id, "document.review", conn)
                document = conn.execute("UPDATE procurement_documents SET status='APPROVED',approved_by=%s,approved_version=version,approved_hash=payload_hash,approval_policy_version=%s,updated_at=now() WHERE id=%s RETURNING *", (principal.actor_id, principal.permission_profile_version, document["id"])).fetchone()
            else:
                if (document["approved_version"] != body.expected_version or document["approved_hash"] != body.payload_hash
                        or document["approved_by"] == document["created_by"] or document["reviewed_by"] == document["created_by"]):
                    raise ServiceError("EXACT_APPROVAL_REQUIRED", 409)
                approver = self.identity.require_actor(document["approved_by"], company_id, "document.approve", conn)
                if approver["permission_profile_version"] != document["approval_policy_version"]:
                    raise ServiceError("APPROVAL_PERMISSION_CHANGED", 409)
                self.identity.require_actor(document["reviewed_by"], company_id, "document.review", conn)
                version = conn.execute("UPDATE ledger_state SET version=version+1 WHERE company_id=%s RETURNING version", (company_id,)).fetchone()["version"]
                document = conn.execute("UPDATE procurement_documents SET status='POSTED',ledger_version=%s,updated_at=now() WHERE id=%s RETURNING *", (version, document["id"])).fetchone()
                if document["kind"] == "CANCEL":
                    conn.execute("UPDATE procurement_documents SET cancelled_by=%s,updated_at=now() WHERE id=%s", (document["id"], document["parent_id"]))
                event_id = uuid4()
                event = {"event_type": "ProcurementPosted", "company_id": company_id, "ledger_version": version,
                         "document_id": str(document["id"]), "document_version": document["version"],
                         "payload_hash": document["payload_hash"], "content": document["content"],
                         "approval": {key: document[key] for key in ("created_by", "reviewed_by", "approved_by", "approved_version", "approved_hash", "approval_policy_version")}}
                conn.execute("INSERT INTO ledger_events(event_id,company_id,ledger_version,document_id,procurement_document_id,event_type,payload,actor_id,recorded_at) VALUES (%s,%s,%s,NULL,%s,'ProcurementPosted',%s,%s,clock_timestamp())",
                    (event_id, company_id, version, document["id"], Jsonb(event), principal.actor_id))
                conn.execute("INSERT INTO outbox(event_id,company_id,ledger_version,payload) VALUES (%s,%s,%s,%s)", (event_id, company_id, version, Jsonb(event)))
                document = {**document, "event_id": str(event_id), "projection_status": "PENDING"}
            self._audit(conn, principal, document, "procurement." + action)
            return document
        return self._run(principal, company_id, permission, body.idempotency_key,
                         {"procurement_action": action, "document_id": document_id, **body.model_dump()}, work)

    def review(self, principal, company_id, document_id, body):
        return self._action(principal, company_id, document_id, body, "review")

    def approve(self, principal, company_id, document_id, body):
        return self._action(principal, company_id, document_id, body, "approve")

    def post(self, principal, company_id, document_id, body):
        return self._action(principal, company_id, document_id, body, "post")

    def reject(self, principal, company_id, document_id, body: ProcurementReject):
        body = ProcurementReject.model_validate(body)
        def work(conn):
            document = self._document(conn, company_id, document_id, lock=True)
            self._exact(document, body.expected_version, body.payload_hash)
            if document["status"] == "POSTED":
                raise ServiceError("POSTED_DOCUMENT_IMMUTABLE", 409)
            if document["created_by"] == principal.actor_id:
                raise ServiceError("SELF_REVIEW_FORBIDDEN", 403)
            self._snapshot(conn, company_id, document["content"]["basis_ledger_version"])
            document = conn.execute("UPDATE procurement_documents SET status='DRAFT',reviewed_by=NULL,approved_by=NULL,approved_version=NULL,approved_hash=NULL,approval_policy_version=NULL,rejection_reason=%s,updated_at=now() WHERE id=%s RETURNING *", (body.reason, document["id"])).fetchone()
            self._audit(conn, principal, document, "procurement.reject")
            return document
        return self._run(principal, company_id, "document.review", body.idempotency_key,
                         {"procurement_action": "reject", "document_id": document_id, **body.model_dump()}, work)

    def get(self, principal, company_id, document_id):
        with self.db.connect() as conn:
            self.identity.require(principal, "document.read", conn, company_id=company_id)
            return _public(self._document(conn, company_id, document_id))

    def list(self, principal, company_id, limit=200):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ServiceError("INVALID_PAGE_LIMIT", 422)
        with self.db.connect() as conn:
            self.identity.require(principal, "document.read", conn, company_id=company_id)
            rows = conn.execute("SELECT * FROM procurement_documents WHERE company_id=%s ORDER BY created_at,id LIMIT %s", (company_id, limit)).fetchall()
            for row in rows:
                if row["kind"] in {"PR", "PO", "RECEIPT"} and row["status"] == "POSTED":
                    row["remaining_qty"] = 0 if row["cancelled_by"] else row["quantity"] - self._used(conn, row)
                    if row["kind"] == "PO":
                        row["confirmed_qty"] = self._confirmed(conn, row)
                        row["receivable_qty"] = 0 if row["cancelled_by"] else row["confirmed_qty"] - self._used(conn, row)
            return _public(rows)

    def options(self, principal, company_id, expected_ledger_version):
        with self.db.connect() as conn:
            self.identity.require(principal, "document.read", conn, company_id=company_id)
            self.identity.require(principal, "ledger.read", conn, company_id=company_id)
            payload = self._snapshot(conn, company_id, expected_ledger_version)
            requirements = requirements_for_snapshot(payload)
            quotes = conn.execute("SELECT * FROM procurement_documents WHERE company_id=%s AND kind='QUOTE' AND status='POSTED' ORDER BY created_at,id LIMIT 200", (company_id,)).fetchall()
            choices = []
            for row in quotes:
                quote = row["content"]
                item = next((x for x in requirements["items"] if x["item_id"] == quote["resolved"]["item_id"] and x["unit"] == quote["unit"]), None)
                for basis, key in (("base", "shortage_qty"), ("planned", "planned_shortage_qty")):
                    calculation = quote_calculation(quote, item[key]) if item and item[key] is not None else None
                    choices.append({"quote_id": str(row["id"]), "quote_ref": quote["quote_ref"], "item_id": quote["resolved"]["item_id"],
                        "supplier_id": quote["resolved"]["supplier_id"], "unit_price": quote["unit_price"],
                        "currency": quote["currency"], "moq": quote["moq"], "order_multiple": quote["order_multiple"],
                        "freight": quote["freight"], "valid_until": quote["valid_until"],
                        "lead_business_days": quote.get("lead_business_days"), "supply_limit_qty": quote.get("supply_limit_qty"),
                        "schedule_status": "UNCONFIRMED", "cost_status": "KNOWN" if quote["freight"] is not None else "FREIGHT_UNKNOWN",
                        "unit": quote["unit"], "material_basis": basis, "calculation": calculation,
                        "expired": date.fromisoformat(quote["valid_until"]) < datetime.now(timezone.utc).date(),
                        "partial_bom": not requirements["full_coverage"], "provenance": "DEMO_ASSUMPTION",
                        "supplier_confirmation": False, "transmission_status": "NOT_SENT"})
            return {"ledger_version": expected_ledger_version, "quotes": choices,
                    "scope": "INTERNAL_DEVELOPMENT_ONLY", "manufacturing_feasibility": "UNKNOWN"}
