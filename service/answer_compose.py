"""Model-written answer summary over server facts (design: DESIGN-HYBRID-RAG-ANSWER, scope A).

The model writes sentences only. It receives an answer packet built here from a
completed server result: fact IDs with names, origins and qualitative states,
and retrieval evidence IDs with kinds. Quantities, ledger values and BOM source
text never enter the packet; every digit in it belongs to a listed identifier.
The model refers to quantities through placeholders such as ⟦F3⟧, which the
server replaces with its own computed values after a deterministic check.
A summary that fails any check is discarded and the server answer stands.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import json
import re
from time import monotonic
from typing import Literal

import httpx
from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from service.contracts import Contract, canonical_bytes, content_hash
from service.errors import ServiceError
from service.identity import Principal
from service.model_gateway import (ModelContext, ModelUsage, OpenRouterRouteProvider,
                                   ProviderFailure)

PROFILE_ID = "compose-v1"
PLACEHOLDER = re.compile(r"⟦(F\d{1,2})⟧")
# Lookarounds, not \b: Korean particles attach directly ("IM-0016은").
IDENTIFIER = re.compile(r"(?<![A-Za-z0-9-])[A-Z]{2,5}(?:-[A-Z0-9]{1,10}){1,3}(?![A-Za-z0-9-])")
_DIGIT = re.compile(r"\d")
MAX_FACTS, MAX_EVIDENCE, MAX_PARAGRAPHS = 30, 5, 4
ORIGINS = ("ledger", "calculation", "actual_source", "synthetic", "assumption", "unknown", "development")
EVIDENCE_KINDS = {"ledger_entity": "원장 개체", "source_row": "BOM 원문 행",
                  "application_review": "BOM 적용 검토", "development_document": "개발 문서"}
# Server verdicts that leave delivery undecided; the summary must not decide it.
HOLD = re.compile(r"보류|판단(할 수 없|하지 않|이 어렵|을 미루)")
# "가능 여부", "충족 여부", "도착할 수 있는지" ask the question rather than answer it.
POSITIVE_DELIVERY = re.compile(r"(도착할 수 있(?!는지)|맞출 수 있(?!는지)|납기[^.。]{0,12}(가능|충족)(?!\s*(여부|한지|할지))"
                               r"|충족(합니다|됩니다|한다)|문제\s*없|확정(입니다|됩니다|되었|했)|보장(합니다|됩니다|된다))")
SYSTEM_PROMPT = (
    "You write a short Korean summary of a manufacturing answer for a planner. "
    "Use only the facts and evidence IDs given. Never write numbers: refer to any quantity by its placeholder "
    "such as ⟦F3⟧ exactly as listed, and write identifiers only as listed. Keep every UNKNOWN or 미확인 state "
    "as unknown; never call delivery possible, confirmed or guaranteed when the verdict is on hold. "
    "Be brief: at most 3 paragraphs of about 200 characters each; lead with the verdict and the main shortage, "
    "and skip facts that add nothing. Return only the requested JSON, each paragraph citing the fact or evidence IDs it uses.")


def _scope_a(text):
    """Digits are allowed only inside listed identifiers."""
    return not _DIGIT.search(IDENTIFIER.sub("", text))


class PacketFact(Contract):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(pattern=r"^F\d{1,2}$")
    name: str = Field(min_length=1, max_length=120)
    origin: Literal[ORIGINS]
    state: str = Field(min_length=1, max_length=40)
    has_value: bool


class PacketEvidence(Contract):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(pattern=r"^E\d$")
    kind: str = Field(min_length=1, max_length=20)
    origin: str = Field(min_length=1, max_length=40)
    review: str = Field(min_length=1, max_length=40)


class AnswerPacket(Contract):
    """Scope A: names, origins, qualitative states and IDs only."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    question_kind: str = Field(min_length=1, max_length=80)
    verdict: str = Field(min_length=1, max_length=160)
    delivery_on_hold: bool
    facts: tuple[PacketFact, ...] = Field(min_length=1, max_length=MAX_FACTS)
    evidence: tuple[PacketEvidence, ...] = Field(default=(), max_length=MAX_EVIDENCE)
    identifiers: tuple[str, ...] = Field(default=(), max_length=60)

    @field_validator("identifiers")
    @classmethod
    def identifier_shape(cls, value):
        if any(not IDENTIFIER.fullmatch(item) for item in value):
            raise ValueError("PACKET_IDENTIFIER_INVALID")
        return value

    @model_validator(mode="after")
    def no_quantities(self):
        texts = [self.question_kind, self.verdict]
        texts += [part for fact in self.facts for part in (fact.name, fact.state)]
        texts += [part for item in self.evidence for part in (item.kind, item.origin, item.review)]
        if not all(_scope_a(text) for text in texts):
            raise ValueError("PACKET_QUANTITY_FORBIDDEN")
        listed = set(self.identifiers)
        if any(found not in listed for text in texts for found in IDENTIFIER.findall(text)):
            raise ValueError("PACKET_IDENTIFIER_UNLISTED")
        if len({fact.id for fact in self.facts}) != len(self.facts) or len({e.id for e in self.evidence}) != len(self.evidence):
            raise ValueError("PACKET_ID_DUPLICATE")
        return self


class ComposeRequest(Contract):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")
    call_id: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,200}$")
    packet: AnswerPacket
    profile_id: Literal["compose-v1"] = PROFILE_ID
    max_output_tokens: int = Field(default=800, ge=64, le=800)


class ComposeContext(ModelContext):
    prompt_version: Literal["compose-scope-a/1"] = "compose-scope-a/1"
    schema_version: Literal["compose-output/1"] = "compose-output/1"


# ---------------------------------------------------------------------------
# Packet from a completed server result
_ORIGIN = {"synthetic_opening_balance": "synthetic", "conditional_demo": "assumption", "actual_bom": "actual_source",
           "unknown": "unknown", "ledger": "ledger", "development_workflow": "development",
           "synthetic_operation": "synthetic", "synthetic_assumption": "assumption"}
_SKIP_SECTIONS = {"next"}


def _clean(text, limit):
    """Digits outside identifiers become '#'; the value itself stays on the server."""
    text = " ".join(str(text).split())
    out, last = [], 0
    for match in IDENTIFIER.finditer(text):
        out.append(re.sub(r"\d+(?:[.,]\d+)?", "#", text[last:match.start()]))
        out.append(match.group(0)); last = match.end()
    out.append(re.sub(r"\d+(?:[.,]\d+)?", "#", text[last:]))
    return "".join(out)[:limit]


def build_packet(result, evidence_hits=()):
    """Returns (packet, values, cites) or None when the result has nothing to summarize.

    values maps fact IDs to server strings substituted after verification;
    cites maps evidence IDs to retrieval evidence IDs for the screen.
    """
    if not isinstance(result, dict) or result.get("status") not in {"ANSWERED", "PARTIAL", "UNKNOWN"}:
        return None
    facts, values = [], {}

    def add(name, origin, state, value=None):
        if len(facts) >= MAX_FACTS:
            return
        fid = f"F{len(facts) + 1}"
        has_value = value is not None and str(value).strip() != ""
        facts.append(PacketFact(id=fid, name=_clean(name, 120) or "항목", origin=origin,
                                state=_clean(state, 40) or "확인됨", has_value=has_value))
        if has_value:
            values[fid] = " ".join(str(value).split())[:120]

    requirements = ((result.get("calculation") or {}).get("material_requirements") or {}).get("items") or []
    for item in requirements:
        shortage = item.get("planned_shortage_qty")
        if item.get("item_id") is None:
            continue
        state = "미확인" if shortage is None else ("부족 있음" if Decimal(str(shortage)) > 0 else "부족 없음")
        unit = "개" if item.get("unit") in (None, "EA") else f" {item['unit']}"
        add(f"{item['item_id']} 계획 투입 기준 부족 수량", "calculation", state,
            None if shortage is None else f"{Decimal(str(shortage)).normalize():f}{unit}")
    for section in result.get("sections") or []:
        if section.get("id") in _SKIP_SECTIONS or section.get("id") == "materials":
            continue
        origin = _ORIGIN.get(section.get("origin"), "ledger")
        for entry in section.get("items") or []:
            value = str(entry.get("value", ""))
            numeric = bool(_DIGIT.search(IDENTIFIER.sub("", value)))
            state = "서버 값" if numeric else (value or section.get("status") or "확인됨")
            add(f"{section.get('title', '')} · {entry.get('label', '')}",
                "unknown" if section.get("status") == "UNKNOWN" and not numeric else origin,
                state, value if numeric else None)
    if not facts:
        return None
    evidence, cites = [], {}
    for hit in list(evidence_hits)[:MAX_EVIDENCE]:
        eid = f"E{len(evidence) + 1}"
        evidence.append(PacketEvidence(id=eid, kind=EVIDENCE_KINDS.get(hit.get("kind"), "근거"),
                                       origin=_clean(hit.get("origin") or "unknown", 40),
                                       review=_clean(hit.get("review_status") or "unknown", 40)))
        cites[eid] = {"evidence_id": hit.get("evidence_id"), "kind": hit.get("kind"),
                      "origin": hit.get("origin"), "node_ids": list(hit.get("node_ids") or [])[:4]}
    texts = [result.get("conclusion") or ""] + [f.name for f in facts] + [f.state for f in facts]
    identifiers = sorted({found for text in texts for found in IDENTIFIER.findall(text)})[:60]
    delivery = next((s for s in result.get("sections") or [] if s.get("id") == "delivery"), None)
    on_hold = bool(delivery and delivery.get("status") == "UNKNOWN") or \
        (result.get("calculation") or {}).get("actual_delivery_feasibility") == "UNKNOWN"
    packet = AnswerPacket(question_kind=_clean(result.get("intent_label") or "업무 조회", 80),
                          verdict=_clean(result.get("conclusion") or "판단 보류", 160),
                          delivery_on_hold=on_hold, facts=tuple(facts), evidence=tuple(evidence),
                          identifiers=tuple(identifiers))
    return packet, values, cites


# ---------------------------------------------------------------------------
# Deterministic verification and substitution
@dataclass(frozen=True)
class Verified:
    status: Literal["VERIFIED", "REJECTED"]
    reason: str | None
    paragraphs: tuple = ()


def verify(packet: AnswerPacket, values: dict, cites: dict, paragraphs) -> Verified:
    def reject(code):
        return Verified("REJECTED", code)
    if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= MAX_PARAGRAPHS:
        return reject("SUMMARY_SHAPE_INVALID")
    fact_ids = {fact.id for fact in packet.facts}
    evidence_ids = {item.id for item in packet.evidence}
    listed = set(packet.identifiers)
    total, out = 0, []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict) or set(paragraph) != {"text", "cites"}:
            return reject("SUMMARY_SHAPE_INVALID")
        text, used = paragraph["text"], paragraph["cites"]
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 400 or not isinstance(used, list) or not used:
            return reject("SUMMARY_SHAPE_INVALID")
        if any(not isinstance(cite, str) or cite not in fact_ids | evidence_ids for cite in used):
            return reject("SUMMARY_CITE_UNKNOWN")
        holders = PLACEHOLDER.findall(text)
        if any(holder not in fact_ids for holder in holders):
            return reject("SUMMARY_PLACEHOLDER_UNKNOWN")
        if any(holder not in values for holder in holders):
            return reject("SUMMARY_PLACEHOLDER_WITHOUT_VALUE")
        bare = PLACEHOLDER.sub("", text)
        if "⟦" in bare or "⟧" in bare:
            return reject("SUMMARY_PLACEHOLDER_INVALID")
        if any(found not in listed for found in IDENTIFIER.findall(bare)):
            return reject("SUMMARY_IDENTIFIER_UNLISTED")
        if _DIGIT.search(IDENTIFIER.sub("", bare)):
            return reject("SUMMARY_NUMBER_UNPLACED")
        total += len(text)
        out.append((text, tuple(dict.fromkeys(used))))
    if total > 1200:
        return reject("SUMMARY_TOO_LONG")
    joined = " ".join(text for text, _ in out)
    if packet.delivery_on_hold:
        if POSITIVE_DELIVERY.search(joined):
            return reject("SUMMARY_DECIDES_HELD_DELIVERY")
        if not HOLD.search(joined):
            return reject("SUMMARY_OMITS_HOLD")
    rendered = tuple({"text": PLACEHOLDER.sub(lambda m: values[m.group(1)], text),
                      "cites": [{"id": cite, **({"evidence": cites[cite]} if cite in cites else {})} for cite in used]}
                     for text, used in out)
    return Verified("VERIFIED", None, rendered)


# ---------------------------------------------------------------------------
# Providers
@dataclass(frozen=True)
class ComposeReply:
    paragraphs: list | None
    usage: ModelUsage
    actual_provider: str | None
    actual_model: str | None
    provider_request_id: str | None = None
    error_code: str | None = None


class FakeComposeProvider:
    """Deterministic, offline summary that follows the packet rules."""
    execution_mode = "fake"
    provider_name = "fake"
    model_id = "synthetic-compose/1"

    def __init__(self, override=None):
        self.calls, self.override = 0, override

    def input_token_bound(self, request):
        return 0

    def complete(self, request, *, timeout_seconds):
        self.calls += 1
        if self.override is not None:
            return ComposeReply(self.override(request), ModelUsage(source="fake", unknown_reason="FAKE_NOT_BILLABLE"), "fake", self.model_id)
        packet = request.packet
        valued = [f for f in packet.facts if f.has_value][:3]
        first = " · ".join(f"{f.name} ⟦{f.id}⟧" for f in valued) or packet.facts[0].name
        paragraphs = [{"text": f"{packet.question_kind}: {first}.", "cites": [f.id for f in valued] or [packet.facts[0].id]}]
        tail = "실제 납기 판단은 보류합니다." if packet.delivery_on_hold else "결론은 서버 판정을 따릅니다."
        paragraphs.append({"text": f"{packet.verdict}. {tail}", "cites": [packet.facts[-1].id] + [e.id for e in packet.evidence[:2]]})
        return ComposeReply(paragraphs, ModelUsage(source="fake", unknown_reason="FAKE_NOT_BILLABLE"), "fake", self.model_id)


class OpenRouterComposeProvider(OpenRouterRouteProvider):
    """Same fixed ZDR route, price caps and response checks as routing; only the prompt and schema differ."""

    def _body(self, request):
        body = {
            "model": self.model_id, "stream": False, self.output_token_parameter: request.max_output_tokens,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": json.dumps(request.packet.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "answer_summary", "strict": True, "schema": {
                "type": "object", "additionalProperties": False, "required": ["paragraphs"],
                "properties": {"paragraphs": {"type": "array", "minItems": 1, "maxItems": MAX_PARAGRAPHS, "items": {
                    "type": "object", "additionalProperties": False, "required": ["text", "cites"],
                    "properties": {"text": {"type": "string"}, "cites": {"type": "array", "items": {"type": "string"}}}}}}}}},
            "provider": {"only": [self.provider_slug], "allow_fallbacks": False,
                         "require_parameters": True, "data_collection": "deny", "zdr": True},
            "plugins": [],
        }
        if self._price_caps is not None:
            body["provider"]["max_price"] = {"prompt": self._per_million(self._price_caps[0]),
                "completion": self._per_million(self._price_caps[1]), "request": 0}
        return body

    def complete(self, request, *, timeout_seconds):
        if self._price_caps is None:
            raise ProviderFailure("MODEL_PRICING_UNVERIFIED", outcome_unknown=False)
        deadline = monotonic() + timeout_seconds
        manager = nullcontext(self._client) if self._client is not None else httpx.Client(trust_env=False)
        try:
            with manager as client:
                payload = self._read(client, "POST", self.ENDPOINT, deadline=deadline, json=self._body(request))
                usage = self._usage(payload)
                model = payload.get("model")
                model = model if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}", model) else None
                generation_id = payload.get("id")
                generation_id = generation_id if isinstance(generation_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", generation_id) else None
                actual_provider = payload.get("provider")
                actual_provider = actual_provider if isinstance(actual_provider, str) and re.fullmatch(r"[A-Za-z0-9_./ -]{1,100}", actual_provider) else None
                paragraphs, error = None, None
                try:
                    choices = payload["choices"]
                    if len(choices) != 1 or choices[0].get("finish_reason") != "stop" or choices[0].get("error"):
                        raise ValueError
                    message = choices[0]["message"]
                    if message.get("tool_calls") or message.get("refusal"):
                        raise ValueError
                    decoded = json.loads(message["content"])
                    if not isinstance(decoded, dict) or set(decoded) != {"paragraphs"}:
                        raise ValueError
                    paragraphs = decoded["paragraphs"]
                except (KeyError, TypeError, ValueError, IndexError):
                    error = "MODEL_OUTPUT_INVALID"
                if model not in self._response_model_ids:
                    error = "MODEL_RETURNED_MODEL_MISMATCH"
                if actual_provider is None:
                    error = "MODEL_PROVIDER_UNCONFIRMED"
                elif actual_provider.casefold() != self.provider_slug.partition("/")[0].casefold():
                    error = "MODEL_RETURNED_PROVIDER_MISMATCH"
                return ComposeReply(paragraphs, usage, actual_provider, model, generation_id, error)
        except ProviderFailure:
            raise
        except httpx.TimeoutException:
            raise ProviderFailure("MODEL_PROVIDER_TIMEOUT") from None
        except httpx.HTTPError:
            raise ProviderFailure("MODEL_PROVIDER_TRANSPORT_ERROR") from None
        except Exception:
            raise ProviderFailure("MODEL_PROVIDER_FAILED") from None


# ---------------------------------------------------------------------------
# Gateway: same budget accounts, limits and recording as routing
class ComposeGateway:
    def __init__(self, store, provider, authorize, policy, *, observer=None):
        self._store, self._provider, self._authorize, self.policy = store, provider, authorize, policy
        self._observer = observer
        if isinstance(provider, OpenRouterRouteProvider) and policy.input_usd_per_token is not None:
            provider.configure_price_caps(policy.input_usd_per_token, policy.output_usd_per_token)

    def _account(self, method, *args, **kwargs):
        try:
            return getattr(self._store, method)(*args, **kwargs)
        except ServiceError as error:
            raise ServiceError(error.code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error.code) else "MODEL_ACCOUNTING_UNAVAILABLE", error.status_code) from None
        except Exception:
            raise ServiceError("MODEL_ACCOUNTING_UNAVAILABLE", 503) from None

    def compose(self, request, context, *, principal, budget=None):
        """Returns (paragraphs, attempt) or raises ServiceError; paragraphs are unverified."""
        if not isinstance(principal, Principal):
            raise ServiceError("SERVER_CONTEXT_REQUIRED", 403)
        try:
            request = ComposeRequest.model_validate(request)
            context = ComposeContext.model_validate(context)
        except (ValidationError, TypeError, ValueError):
            raise ServiceError("INVALID_MODEL_REQUEST", 422) from None
        if principal.company_id != context.company_id or "query.read" not in principal.permissions:
            raise ServiceError("PERMISSION_DENIED", 403)
        if context.budget_id != self.policy.budget_id:
            raise ServiceError("MODEL_BUDGET_SCOPE_MISMATCH", 403)
        if budget is not None:
            budget.check()
        self._authorize(principal, context.company_id, "query.read")
        mode = self._provider.execution_mode
        reservation = Decimal(0)
        if mode == "live":
            if not self.policy.live_enabled or not self.policy.transmission_approved:
                raise ServiceError("MODEL_LIVE_NOT_AUTHORIZED", 403)
            with localcontext() as decimal_context:
                decimal_context.prec = 80
                reservation = (self._provider.input_token_bound(request) * self.policy.input_usd_per_token
                               + request.max_output_tokens * self.policy.output_usd_per_token)
        scope = {key: getattr(context, key) for key in ("company_id", "read_snapshot_id", "ledger_version", "projection_version",
                                                         "ontology_version", "rule_version", "workflow_version", "policy_version")}
        material = {"request": request.model_dump(mode="json"), "provider": self._provider.provider_name,
                    "model": self._provider.model_id, "mode": mode, "prompt_version": context.prompt_version,
                    "schema_version": context.schema_version}
        started = datetime.now(timezone.utc)
        attempt = self._account("reserve", budget_id=context.budget_id, execution_id=context.execution_id,
            company_id=context.company_id, actor_id=principal.actor_id, scope_hash=content_hash(scope),
            call_id=request.call_id, retry_of=None, request_hash=content_hash(material), mode=mode,
            provider=self._provider.provider_name, model=self._provider.model_id, reserved_usd=reservation,
            metadata={"segment_id": context.segment_id, "parent_span_id": context.parent_span_id,
                      "pricing_version": self.policy.pricing_version, "profile_id": PROFILE_ID})
        if not attempt["new"]:
            if attempt["status"] == "completed" and attempt["result"] is not None:
                return attempt["result"].get("paragraphs"), attempt
            raise ServiceError(attempt.get("error_code") or "MODEL_ATTEMPT_OUTCOME_UNKNOWN", 409)
        self._account("mark_dispatched", attempt["attempt_id"])
        reply = None
        try:
            reply = self._provider.complete(request, timeout_seconds=self.policy.timeout_seconds)
            if reply.error_code:
                raise ProviderFailure(reply.error_code, usage=reply.usage, outcome_unknown=False)
            if not isinstance(reply.paragraphs, list):
                raise ProviderFailure("MODEL_OUTPUT_INVALID", usage=reply.usage, outcome_unknown=False)
            finished = self._account("finish", attempt["attempt_id"], status="completed", usage=reply.usage.model_dump(),
                result={"profile_id": PROFILE_ID, "paragraphs": reply.paragraphs, "execution_mode": mode},
                actual_provider=reply.actual_provider, actual_model=reply.actual_model,
                provider_request_id=reply.provider_request_id)
        except Exception as error:
            if isinstance(error, ProviderFailure):
                usage, code, status = error.usage, error.code, "outcome_unknown" if error.outcome_unknown else "failed"
            else:
                usage, code, status = reply.usage if reply else ModelUsage(), "MODEL_PROVIDER_FAILED", "outcome_unknown"
            finished = self._account("finish", attempt["attempt_id"], status=status, usage=usage.model_dump(), error_code=code,
                actual_provider=getattr(reply, "actual_provider", None), actual_model=getattr(reply, "actual_model", None))
            self._observe(request, context, finished, usage, started, None)
            raise ServiceError(finished["error_code"] or code, 503) from None
        self._observe(request, context, finished, reply.usage, started, reply.paragraphs)
        return reply.paragraphs, finished

    def _observe(self, request, context, attempt, usage, started, paragraphs):
        if self._observer is None:
            return
        from service.observability import ComposeObservationInput, ComposeObservationOutput, ObservedUsage
        try:
            metadata = {"attempt_id": attempt["attempt_id"], "execution_mode": attempt["execution_mode"],
                        "provider": attempt.get("actual_provider"), "model": attempt.get("actual_model"),
                        "profile_id": PROFILE_ID, "segment_id": context.segment_id,
                        "workflow_version": context.workflow_version, "prompt_version": context.prompt_version,
                        "schema_version": context.schema_version, "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens, "total_tokens": usage.total_tokens,
                        "cost_usd": str(usage.cost_usd) if usage.cost_usd is not None else None,
                        "usage_source": usage.source, "error_code": attempt.get("error_code")}
            status = attempt["status"] if attempt["status"] in {"completed", "failed"} else "unknown"
            self._observer.emit_span(execution_id=context.execution_id, span_id=attempt["attempt_id"],
                parent_span_id=context.parent_span_id, name="model.compose", kind="generation", status=status,
                metadata=metadata, started_at=started, ended_at=datetime.now(timezone.utc),
                input=ComposeObservationInput(execution_mode=attempt["execution_mode"], fact_count=len(request.packet.facts),
                    evidence_count=len(request.packet.evidence), delivery_on_hold=request.packet.delivery_on_hold,
                    packet=json.dumps(request.packet.model_dump(mode="json"), ensure_ascii=False)),
                output=ComposeObservationOutput(status=attempt["status"] if attempt["status"] in {"completed", "failed", "outcome_unknown"} else "failed",
                    paragraph_count=len(paragraphs) if isinstance(paragraphs, list) else 0,
                    usage=ObservedUsage(**usage.model_dump()), error_code=attempt.get("error_code"),
                    summary=json.dumps(paragraphs, ensure_ascii=False) if isinstance(paragraphs, list) else None))
        except Exception:
            pass


def compose_summary(gateway, result, evidence_hits, *, principal, context, budget=None):
    """Full step used by the workflow. Never raises; returns the ai_summary record."""
    built = build_packet(result, evidence_hits)
    if built is None:
        return {"status": "NOT_APPLICABLE"}
    packet, values, cites = built
    try:
        paragraphs, attempt = gateway.compose(ComposeRequest(call_id=PROFILE_ID, packet=packet), context,
                                              principal=principal, budget=budget)
    except ServiceError as error:
        return {"status": "UNAVAILABLE", "reason_code": error.code, "packet_digest": content_hash(packet.model_dump(mode="json"))}
    checked = verify(packet, values, cites, paragraphs)
    record = {"status": checked.status, "reason_code": checked.reason, "packet_digest": content_hash(packet.model_dump(mode="json")),
              "execution_mode": attempt.get("execution_mode"), "model": attempt.get("actual_model"),
              "provider": attempt.get("actual_provider"), "facts": [{"id": f.id, "name": f.name} for f in packet.facts]}
    if checked.status == "VERIFIED":
        record["paragraphs"] = list(checked.paragraphs)
    return record
