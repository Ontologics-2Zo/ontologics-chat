"""Synthetic provider and isolated PostgreSQL tests; no real model calls."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from psycopg import sql
from pydantic import ValidationError

from service.db import Database
from service.agent_host import verified_prices, verified_zdr_endpoint
from service.errors import ServiceError
from service.gateway import ExecutionBudget
from service.identity import Principal
from service.model_gateway import (FakeRouteProvider, GatewayPolicy, ModelContext, ModelGateway,
    ModelRequest, ModelUsage, OpenRouterRouteProvider, ProviderFailure, ProviderReply)
from service.model_store import PostgresModelStore
from service.observability import MetadataObserver


def context(**updates):
    values = dict(execution_id="execution-one", request_id="request-one", context_revision="context-one",
                  company_id="company-one", read_snapshot_id="snapshot-one", ledger_version=2,
                  projection_version=2, ontology_version="ontology-one", rule_version="rules-one",
                  segment_id="segment-one", parent_span_id="parent-one", budget_id="new-scope")
    values.update(updates)
    return ModelContext(**values)


def request(**updates):
    values = dict(call_id="route-one", features=("materials",))
    values.update(updates)
    return ModelRequest(**values)


def principal(**updates):
    values = dict(actor_id="actor-one", tenant_id="company-one", site_scope=(), permissions=("query.read",),
                  permission_profile_version=1, identity_revision="identity-one", session_ref="session-one")
    values.update(updates)
    return Principal(**values)


def adapter(client, key="synthetic", **kwargs):
    return OpenRouterRouteProvider(key, client=client, input_usd_per_token=Decimal("0.000002"),
                                   output_usd_per_token=Decimal("0.00001"), **kwargs)


class RecordingObserver:
    def __init__(self, *, broken=False):
        self.spans = []
        self.broken = broken

    def emit_span(self, **span):
        self.spans.append(span)
        if self.broken:
            raise RuntimeError("secret-observer-failure")


class SyntheticLiveProvider:
    """Explicit billable-mode contract double, with no networking at all."""
    execution_mode = "live"
    provider_name = "synthetic-provider"
    model_id = "synthetic-model"

    def __init__(self, *, cost=Decimal("0.01"), failure=None):
        self.calls = 0
        self.cost = cost
        self.failure = failure

    def input_token_bound(self, request):
        return 1

    def complete(self, request, *, timeout_seconds):
        self.calls += 1
        if self.failure:
            raise self.failure
        return ProviderReply("material_analysis", ModelUsage(input_tokens=2, output_tokens=3, total_tokens=5,
            cost_usd=self.cost, source="provider", unknown_reason="COST_NOT_RETURNED" if self.cost is None else None),
            self.provider_name, self.model_id, "synthetic-generation")


class ModelContractTests(unittest.TestCase):
    def test_requests_cannot_carry_raw_text_authority_or_arbitrary_features(self):
        for extra in ({"prompt": "private source"}, {"question": "private question"}, {"actor_id": "forged"},
                      {"features": ("read private BOM",)}, {"features": ("materials", "materials")},
                      {"max_output_tokens": True}, {"max_output_tokens": 257}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValidationError):
                    ModelRequest(**{**request().model_dump(), **extra})

    def test_fake_routes_only_to_bounded_capabilities_without_usage(self):
        provider = FakeRouteProvider()
        for features, expected in ((("materials",), "material_analysis"), (("delivery",), "fulfillment_read"), (("unknown",), "needs_clarification")):
            result = provider.complete(request(features=features), timeout_seconds=1)
            self.assertEqual(result.capability, expected)
            self.assertEqual(result.usage.source, "fake")
            self.assertIsNone(result.usage.input_tokens)
            self.assertIsNone(result.usage.cost_usd)

    def test_provider_serializes_only_fixed_synthetic_features_and_disables_fallbacks(self):
        seen = []
        def handler(req):
            seen.append(req)
            return httpx.Response(200, json={"id": "gen-synthetic", "model": "openai/gpt-6.1-sol", "provider": "OpenAI",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"capability":"material_analysis"}'}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12, "cost": 0.000056}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            provider = adapter(client, "synthetic-test-key")
            result = provider.complete(request(), timeout_seconds=2)
        body = json.loads(seen[0].content)
        self.assertEqual(str(seen[0].url), provider.ENDPOINT)
        self.assertEqual(body["provider"], {"only": ["openai"], "allow_fallbacks": False, "require_parameters": True,
            "data_collection": "deny", "zdr": True, "max_price": {"prompt": 2, "completion": 10, "request": 0}})
        self.assertEqual(set(json.loads(body["messages"][1]["content"]).values()), {True, False})
        self.assertNotIn("synthetic-test-key", seen[0].content.decode())
        self.assertNotIn("tools", body)
        self.assertNotIn("models", body)
        self.assertEqual(result.capability, "material_analysis")
        self.assertEqual(result.usage.cost_usd, Decimal("0.000056"))
        self.assertIsNone(result.error_code)

    def test_missing_usage_and_actual_provider_are_not_fabricated(self):
        requests = []
        def handler(req):
            requests.append(req)
            if req.method == "GET":
                return httpx.Response(200, json={"data": {"provider_name": "OpenAI"}})
            return httpx.Response(200, json={"id": "gen-test", "model": "openai/gpt-6.1-sol",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"capability":"fulfillment_read"}'}}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = adapter(client).complete(request(), timeout_seconds=2)
        self.assertEqual([r.method for r in requests], ["POST", "GET"])
        self.assertEqual(result.actual_provider, "OpenAI")
        self.assertIsNone(result.usage.cost_usd)
        self.assertEqual(result.usage.unknown_reason, "USAGE_NOT_RETURNED")

    def test_unexpected_model_provider_and_authority_output_are_rejected(self):
        baseline = {"id": "gen-test", "model": "openai/gpt-6.1-sol", "provider": "OpenAI",
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"capability":"material_analysis"}'}}]}
        for replacement, code in (({"model": "unapproved/model"}, "MODEL_RETURNED_MODEL_MISMATCH"),
                                  ({"provider": "Other"}, "MODEL_RETURNED_PROVIDER_MISMATCH"),
                                  ({"choices": [{"finish_reason": "stop", "message": {"content": '{"capability":"material_analysis","approved":true}'}}]}, "MODEL_OUTPUT_INVALID")):
            with self.subTest(code=code):
                with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={**baseline, **replacement}))) as client:
                    result = adapter(client).complete(request(), timeout_seconds=2)
                self.assertEqual(result.error_code, code)

    def test_catalog_canonical_alias_is_exact_and_preserves_requested_model(self):
        canonical = "openai/gpt-6.1-sol-20260929"
        for returned in ("openai/gpt-6.1-sol", canonical,
                         "openai/gpt-6.1-sol-20990101", "openai/gpt-6.1-sol-other"):
            with self.subTest(returned=returned):
                seen = []
                def handler(req):
                    seen.append(json.loads(req.content))
                    return httpx.Response(200, json={"model": returned, "provider": "OpenAI",
                        "choices": [{"finish_reason": "stop", "message": {
                            "content": '{"capability":"material_analysis"}'}}]})
                with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                    result = adapter(client, canonical_model_id=canonical).complete(request(), timeout_seconds=2)
                self.assertEqual(result.actual_model, returned)
                self.assertEqual(result.error_code, None if returned in {"openai/gpt-6.1-sol", canonical}
                                 else "MODEL_RETURNED_MODEL_MISMATCH")
                self.assertEqual(seen[0]["model"], "openai/gpt-6.1-sol")
                self.assertEqual(seen[0]["provider"]["only"], ["openai"])
                self.assertFalse(seen[0]["provider"]["allow_fallbacks"])

    def test_alias_not_from_host_and_wrong_provider_remain_rejected(self):
        canonical = "openai/gpt-6.1-sol-20260929"
        for options, provider, error in (({}, "OpenAI", "MODEL_RETURNED_MODEL_MISMATCH"),
                ({"canonical_model_id": canonical}, "Other", "MODEL_RETURNED_PROVIDER_MISMATCH")):
            with self.subTest(options=options, provider=provider):
                with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
                        "model": canonical, "provider": provider, "choices": [{"finish_reason": "stop",
                        "message": {"content": '{"capability":"material_analysis"}'}}]}))) as client:
                    result = adapter(client, **options).complete(request(), timeout_seconds=2)
                self.assertEqual(result.error_code, error)

    def test_public_catalog_binds_prices_and_canonical_alias_to_exact_model(self):
        records = [{"id": "unapproved/model", "canonical_slug": "unapproved/canonical", "pricing": {}},
                   {"id": "openai/gpt-6.1-sol", "canonical_slug": "openai/gpt-6.1-sol-20260929",
                    "pricing": {"prompt": "0.000002", "completion": "0.00001"}}]
        digests = []
        for canonical in ("openai/gpt-6.1-sol-20260929", "openai/gpt-6.1-sol-20990101"):
            records[1]["canonical_slug"] = canonical
            seen = []
            def handler(req):
                seen.append(req)
                return httpx.Response(200, json={"data": records})
            client = httpx.Client(transport=httpx.MockTransport(handler))
            with patch("service.agent_host.httpx.Client", return_value=client):
                prompt, completion, digest, actual = verified_prices("openai/gpt-6.1-sol")
            self.assertEqual((prompt, completion), (Decimal("0.000002"), Decimal("0.00001")))
            self.assertEqual(actual, canonical)
            self.assertEqual(str(seen[0].url), "https://openrouter.ai/api/v1/models")
            self.assertEqual(seen[0].method, "GET")
            self.assertNotIn("authorization", seen[0].headers)
            digests.append(digest)
        self.assertNotEqual(*digests)

    def test_missing_or_invalid_catalog_alias_blocks_live_profile(self):
        for canonical in (None, 7, "other/gpt-6.1-sol", "openai/gpt-6.1-sol*", "openai/gpt-6.1-sol\n"):
            with self.subTest(canonical=canonical):
                row = {"id": "openai/gpt-6.1-sol", "pricing": {"prompt": "0.000002", "completion": "0.00001"}}
                if canonical is not None:
                    row["canonical_slug"] = canonical
                client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"data": [row]})))
                with patch("service.agent_host.httpx.Client", return_value=client):
                    with self.assertRaises(ServiceError) as caught:
                        verified_prices("openai/gpt-6.1-sol")
                self.assertEqual(caught.exception.code, "MODEL_PRICING_UNVERIFIED")

    def test_fixed_azure_route_uses_supported_limit_and_checks_actual_provider(self):
        for actual in ("Azure", "OpenAI"):
            with self.subTest(actual=actual):
                seen = []
                def handler(req):
                    seen.append(json.loads(req.content))
                    return httpx.Response(200, json={"model": "openai/gpt-6.1-sol", "provider": actual,
                        "choices": [{"finish_reason": "stop", "message": {
                            "content": '{"capability":"material_analysis"}'}}]})
                with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                    result = adapter(client, provider_slug="azure", output_token_parameter="max_completion_tokens").complete(request(), timeout_seconds=2)
                self.assertEqual(seen[0]["max_completion_tokens"], 128)
                self.assertNotIn("max_tokens", seen[0])
                self.assertEqual(seen[0]["provider"], {"only": ["azure"], "allow_fallbacks": False,
                    "require_parameters": True, "data_collection": "deny", "zdr": True,
                    "max_price": {"prompt": 2, "completion": 10, "request": 0}})
                self.assertEqual(result.error_code, None if actual == "Azure" else "MODEL_RETURNED_PROVIDER_MISMATCH")
        with self.assertRaises(ServiceError):
            adapter(None, output_token_parameter="arbitrary_parameter")

    def test_zdr_catalog_requires_exact_fixed_endpoint_and_safe_price_tiers(self):
        baseline = {"model_id": "openai/gpt-6.1-sol", "tag": "azure", "provider_name": "Azure",
            "status": 0, "safety_retention": False,
            "supported_parameters": ["max_completion_tokens", "response_format", "structured_outputs"],
            "pricing": {"prompt": "0.000002", "completion": "0.00001", "web_search": "0.01",
                        "overrides": [{"min_prompt_tokens": 272000, "prompt": "0.000004", "completion": "0.000015"}]}}
        cases = [(None, [baseline]), ("missing", []), ("duplicate", [baseline, baseline])]
        for field, value in (("model_id", "openai/other"), ("tag", "openai"), ("provider_name", "OpenAI"),
                ("safety_retention", True), ("safety_retention", 0), ("status", 1), ("status", False),
                ("supported_parameters", ["max_tokens", "response_format", "structured_outputs"]),
                ("supported_parameters", ["max_completion_tokens", "response_format"])):
            cases.append((field, [{**baseline, field: value}]))
        for change in ({"prompt": "0.000003"}, {"completion": "0.00002"}, {"request": "0.01"},
                       {"overrides": [{"min_prompt_tokens": 3000}]}, {"overrides": [{"min_prompt_tokens": "272000"}]},
                       {"overrides": [{}]}, {"overrides": {}}):
            cases.append(("pricing", [{**baseline, "pricing": {**baseline["pricing"], **change}}]))
        for error, records in cases:
            with self.subTest(error=error, records=records):
                seen = []
                def handler(req):
                    seen.append(req)
                    return httpx.Response(200, json={"data": records})
                client = httpx.Client(transport=httpx.MockTransport(handler))
                with patch("service.agent_host.httpx.Client", return_value=client):
                    if error is None:
                        self.assertTrue(verified_zdr_endpoint("openai/gpt-6.1-sol", Decimal("0.000002"),
                            Decimal("0.00001"), input_token_bound=3000))
                    else:
                        with self.assertRaises(ServiceError) as caught:
                            verified_zdr_endpoint("openai/gpt-6.1-sol", Decimal("0.000002"),
                                Decimal("0.00001"), input_token_bound=3000)
                        self.assertEqual(caught.exception.code, "MODEL_ZDR_PROFILE_UNVERIFIED")
                self.assertEqual(str(seen[0].url), "https://openrouter.ai/api/v1/endpoints/zdr")
                self.assertEqual(seen[0].method, "GET")
                self.assertNotIn("authorization", seen[0].headers)

    def test_transport_failure_never_exposes_response_or_key(self):
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503, text="private provider body secret-key"))) as client:
            with self.assertRaises(ProviderFailure) as caught:
                adapter(client, "secret-key").complete(request(), timeout_seconds=2)
        self.assertEqual(str(caught.exception), "MODEL_PROVIDER_HTTP_503")

    def test_http_diagnostics_retain_only_the_safe_numeric_status(self):
        for status in (400, 402, 403, 404, 429):
            with self.subTest(status=status):
                with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, text="private source and key"))) as client:
                    with self.assertRaises(ProviderFailure) as caught:
                        adapter(client).complete(request(), timeout_seconds=2)
                self.assertEqual(caught.exception.code, f"MODEL_PROVIDER_HTTP_{status}")
                self.assertEqual(str(caught.exception), f"MODEL_PROVIDER_HTTP_{status}")
                self.assertIsNone(caught.exception.usage.cost_usd)


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL not configured")
class ModelGatewayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.base = Database(os.environ["TEST_DATABASE_URL"])
        self.schema = "test_model_" + uuid4().hex
        with self.base.connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        self.db = Database(os.environ["TEST_DATABASE_URL"], schema=self.schema)
        self.db.migrate()
        self.store = PostgresModelStore(self.db)
        self.actor, self.context = principal(), context()
        self.authorized = []

    def drop_schema(self):
        with self.base.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def authorize(self, *args):
        self.authorized.append(args)

    def assert_code(self, code, callback):
        with self.assertRaises(ServiceError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.details, {})
        return caught.exception

    def live_gateway(self, provider=None, *, prior=Decimal(0), limit=Decimal(10), reservation=Decimal("0.01"), observer=None):
        self.store.initialize_budget("new-scope", prior_spend_usd=prior, limit_usd=limit,
                                     authorization_ref="SYNTHETIC-NEW-SCOPE" if prior is not None else None)
        policy = GatewayPolicy(budget_id="new-scope", live_enabled=True, transmission_approved=True,
                               input_usd_per_token=reservation, output_usd_per_token=Decimal(0), pricing_version="synthetic-price-v1")
        return ModelGateway(self.store, provider or SyntheticLiveProvider(), self.authorize, policy=policy, observer=observer)

    def call(self, gateway, req=None, ctx=None, actor=None, budget=None):
        return gateway.complete(req or request(), ctx or self.context, principal=actor or self.actor, budget=budget)

    def rows(self):
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM model_attempts ORDER BY created_at,attempt_id").fetchall()

    def test_fake_and_restarted_replay_share_one_durable_attempt_and_span(self):
        observer = RecordingObserver()
        provider = FakeRouteProvider()
        gateway = ModelGateway(self.store, provider, self.authorize, policy=GatewayPolicy(budget_id="new-scope"), observer=observer)
        port = gateway.bind(principal=self.actor, context=self.context, budget=ExecutionBudget(self.context.execution_id))
        first = port.complete(request())
        self.assertEqual(first.capability, "material_analysis")
        self.assertFalse(hasattr(port, "db"))
        self.assertFalse(hasattr(port, "_provider"))
        second_provider = FakeRouteProvider()
        restarted = ModelGateway(PostgresModelStore(self.db), second_provider, self.authorize,
                                 policy=GatewayPolicy(budget_id="new-scope"), observer=observer)
        new_context = context(context_revision="new-session", segment_id="resume-segment", request_id="resume-request")
        second = self.call(restarted, ctx=new_context)
        self.assertEqual(second.attempt_id, first.attempt_id)
        self.assertEqual(second.context, new_context)
        self.assertTrue(second.replayed)
        self.assertEqual(second_provider.calls, 0)
        self.assertEqual(len(observer.spans), 1)
        self.assertIsNone(first.usage.cost_usd)
        self.assertEqual(self.store.execution_summary(), {"live_calls": 0, "fake_calls": 1, "known_cost_usd": "0", "held_usd": "0", "usage_unknown": 0})

    def test_prior_unknown_blocks_live_and_new_scope_never_resets_old_scope(self):
        provider = SyntheticLiveProvider()
        gateway = self.live_gateway(provider, prior=None)
        self.assert_code("MODEL_PRIOR_SPEND_UNKNOWN", lambda: self.call(gateway))
        self.assertEqual(provider.calls, 0)
        self.assert_code("MODEL_BUDGET_ALREADY_INITIALIZED", lambda: self.store.initialize_budget("new-scope", prior_spend_usd=Decimal(0), authorization_ref="new-auth"))
        self.store.initialize_budget("separate-authorized-scope", prior_spend_usd=Decimal(0), authorization_ref="explicit-new-auth")
        self.assertEqual(self.store.budget_status("new-scope")["prior_spend_status"], "UNKNOWN")
        self.assertEqual(self.store.budget_status("separate-authorized-scope")["prior_spend_usd"], "0")

    def test_missing_live_policy_pricing_and_wrong_budget_never_dispatch(self):
        provider = SyntheticLiveProvider()
        gateway = ModelGateway(self.store, provider, self.authorize, policy=GatewayPolicy(budget_id="new-scope"))
        self.assert_code("MODEL_LIVE_NOT_AUTHORIZED", lambda: self.call(gateway))
        gateway.policy = GatewayPolicy(budget_id="new-scope", live_enabled=True, transmission_approved=True)
        self.assert_code("MODEL_PRICING_UNVERIFIED", lambda: self.call(gateway))
        self.assert_code("MODEL_BUDGET_SCOPE_MISMATCH", lambda: self.call(gateway, ctx=context(budget_id="other")))
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self.rows(), [])

    def test_shared_question_and_total_budget_are_checked_before_dispatch(self):
        provider = SyntheticLiveProvider(cost=Decimal("0.30"))
        gateway = self.live_gateway(provider, reservation=Decimal("0.30"))
        self.call(gateway)
        self.assert_code("MODEL_QUESTION_BUDGET_EXHAUSTED", lambda: self.call(gateway, request(call_id="second-child-call")))
        self.assertEqual(provider.calls, 1)
        status = self.store.execution_summary(self.context.execution_id)
        self.assertEqual(Decimal(status["known_cost_usd"]), Decimal("0.30"))
        self.assertEqual(status["live_calls"], 1)

    def test_total_budget_remains_shared_across_concurrent_executions(self):
        provider = SyntheticLiveProvider(cost=Decimal("0.30"))
        gateway = self.live_gateway(provider, limit=Decimal(1), reservation=Decimal("0.30"))
        def invoke(index):
            try:
                self.call(gateway, ctx=context(execution_id=f"parallel-{index}"))
                return "completed"
            except ServiceError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(invoke, range(4)))
        self.assertEqual(outcomes.count("completed"), 3)
        self.assertEqual(outcomes.count("MODEL_TOTAL_BUDGET_EXHAUSTED"), 1)
        self.assertEqual(Decimal(self.store.budget_status("new-scope")["known_cost_usd"]), Decimal("0.90"))

    def test_known_prior_spend_is_included_in_total(self):
        provider = SyntheticLiveProvider(cost=Decimal("0.01"))
        gateway = self.live_gateway(provider, prior=Decimal("9.99"))
        self.call(gateway)
        self.assert_code("MODEL_TOTAL_BUDGET_EXHAUSTED", lambda: self.call(gateway, ctx=context(execution_id="new-question")))
        self.assertEqual(provider.calls, 1)

    def test_unknown_outcome_holds_budget_and_cannot_retry_or_replay_dispatch(self):
        provider = SyntheticLiveProvider(failure=ProviderFailure("MODEL_PROVIDER_TIMEOUT"))
        observer = MetadataObserver()
        self.addCleanup(observer.close)
        gateway = self.live_gateway(provider, reservation=Decimal("0.30"), observer=observer)
        self.assert_code("MODEL_PROVIDER_TIMEOUT", lambda: self.call(gateway))
        row = self.rows()[0]
        self.assertEqual(row["status"], "outcome_unknown")
        self.assertEqual(row["held_usd"], Decimal("0.30"))
        self.assertIsNone(row["cost_usd"])
        self.assert_code("MODEL_PROVIDER_TIMEOUT", lambda: self.call(gateway))
        self.assert_code("MODEL_RETRY_OUTCOME_UNRESOLVED", lambda: self.call(gateway, request(retry_of=row["attempt_id"])))
        self.assert_code("MODEL_QUESTION_BUDGET_EXHAUSTED", lambda: self.call(gateway, request(call_id="child-two")))
        self.assertEqual(provider.calls, 1)
        self.assertEqual(self.store.execution_summary()["usage_unknown"], 1)
        self.assertEqual(observer.invalid, 0)
        self.assertEqual(observer.receipts.records()[0]["span"]["status"], "unknown")
        self.assertEqual(self.rows()[0]["status"], "outcome_unknown")

    def test_success_without_cost_keeps_a_reservation_not_fabricated_zero(self):
        gateway = self.live_gateway(SyntheticLiveProvider(cost=None))
        result = self.call(gateway)
        self.assertIsNone(result.usage.cost_usd)
        self.assertEqual(self.rows()[0]["held_usd"], Decimal("0.01"))
        self.assertEqual(self.store.execution_summary()["usage_unknown"], 1)

    def test_only_one_explicit_retry_is_shared_by_all_calls(self):
        class FailingFake(FakeRouteProvider):
            def complete(self, *args, **kwargs):
                self.calls += 1
                raise ProviderFailure("MODEL_OUTPUT_INVALID", usage=ModelUsage(source="fake", unknown_reason="FAKE_NOT_BILLABLE"), outcome_unknown=False)
        gateway = ModelGateway(self.store, FailingFake(), self.authorize, policy=GatewayPolicy(budget_id="new-scope"))
        self.assert_code("MODEL_OUTPUT_INVALID", lambda: self.call(gateway))
        first = self.rows()[0]
        self.assert_code("MODEL_OUTPUT_INVALID", lambda: self.call(gateway, request(retry_of=first["attempt_id"])))
        self.assert_code("MODEL_OUTPUT_INVALID", lambda: self.call(gateway, request(call_id="another-call")))
        other = next(row for row in self.rows() if row["call_id"] == "another-call")
        self.assert_code("MODEL_RETRY_BUDGET_EXHAUSTED", lambda: self.call(gateway, request(call_id="another-call", retry_of=other["attempt_id"])))

    def test_changed_request_snapshot_and_actor_cannot_reuse_an_attempt(self):
        gateway = ModelGateway(self.store, FakeRouteProvider(), self.authorize, policy=GatewayPolicy(budget_id="new-scope"))
        self.call(gateway)
        self.assert_code("MODEL_IDEMPOTENCY_CONFLICT", lambda: self.call(gateway, request(features=("delivery",))))
        self.assert_code("MODEL_EXECUTION_SCOPE_MISMATCH", lambda: self.call(gateway, ctx=context(ledger_version=3)))
        self.assert_code("MODEL_EXECUTION_SCOPE_MISMATCH", lambda: self.call(gateway, actor=principal(actor_id="different-actor")))
        self.assert_code("PERMISSION_DENIED", lambda: self.call(gateway, actor=principal(tenant_id="other-company")))

    def test_revocation_and_deadline_are_checked_before_replaying(self):
        gateway = ModelGateway(self.store, FakeRouteProvider(), self.authorize, policy=GatewayPolicy(budget_id="new-scope"))
        self.call(gateway)
        self.assert_code("MODEL_DEADLINE_EXCEEDED", lambda: self.call(gateway, ctx=context(deadline_at=datetime.now(timezone.utc)-timedelta(seconds=1))))
        def revoked(*args):
            raise ServiceError("IDENTITY_CHANGED", 403, {"raw": "secret"})
        gateway._authorize = revoked
        self.assert_code("IDENTITY_CHANGED", lambda: self.call(gateway))
        self.assertEqual(len(self.rows()), 1)

    def test_observer_failure_does_not_undo_success_or_duplicate_cost(self):
        observer = RecordingObserver(broken=True)
        gateway = self.live_gateway(observer=observer)
        first = self.call(gateway)
        second = self.call(gateway)
        self.assertEqual(first.attempt_id, second.attempt_id)
        self.assertEqual(gateway.observation_failures, 1)
        self.assertEqual(Decimal(self.store.budget_status("new-scope")["known_cost_usd"]), Decimal("0.01"))
        self.assertFalse(any(key in observer.spans[0]["metadata"] for key in ("prompt", "response", "question", "raw_error", "api_key")))

    def test_provider_error_is_safe_and_fake_never_invents_actual_usage(self):
        class UnsafeFake(FakeRouteProvider):
            def complete(self, *args, **kwargs):
                raise RuntimeError("secret-provider-key private-BOM")
        observer = RecordingObserver()
        gateway = ModelGateway(self.store, UnsafeFake(), self.authorize, policy=GatewayPolicy(budget_id="new-scope"), observer=observer)
        self.assert_code("MODEL_PROVIDER_FAILED", lambda: self.call(gateway))
        self.assertNotIn("secret-provider-key", str(self.rows()))
        self.assertNotIn("private-BOM", str(observer.spans))
        self.assertEqual(observer.spans[0]["status"], "unknown")

    def test_pre_dispatch_failure_releases_hold_and_does_not_count_as_model_call(self):
        class NoCallsBudget:
            def check(self): pass
            def reserve(self, execution_id): raise ServiceError("TOOL_BUDGET_EXHAUSTED", 429)
        gateway = self.live_gateway()
        self.assert_code("TOOL_BUDGET_EXHAUSTED", lambda: self.call(gateway, budget=NoCallsBudget()))
        self.assertEqual(self.rows()[0]["held_usd"], 0)
        self.assertEqual(self.store.execution_summary()["live_calls"], 0)

    def test_cost_above_reserved_ceiling_is_recorded_and_blocks_further_dispatch(self):
        provider = SyntheticLiveProvider(cost=Decimal("0.02"))
        gateway = self.live_gateway(provider)
        self.assert_code("MODEL_COST_EXCEEDED_RESERVATION", lambda: self.call(gateway))
        self.assertEqual(self.rows()[0]["cost_usd"], Decimal("0.02"))
        self.assertTrue(self.store.budget_status("new-scope")["blocked"])
        self.assert_code("MODEL_BUDGET_RECONCILIATION_REQUIRED", lambda: self.call(gateway, ctx=context(execution_id="new-question")))
        self.assertEqual(provider.calls, 1)

    def test_accounting_failure_is_safe_and_prevents_provider_dispatch(self):
        gateway = self.live_gateway()
        def unavailable(**kwargs):
            raise RuntimeError("postgres-password-and-path")
        gateway._store.reserve = unavailable
        self.assert_code("MODEL_ACCOUNTING_UNAVAILABLE", lambda: self.call(gateway))
        self.assertEqual(gateway._provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
