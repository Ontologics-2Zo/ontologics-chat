"""Host composition; credentials, database and provider objects stay here."""

from decimal import Decimal
import re

import httpx

from service.agent_contracts import WORKFLOW_VERSION
from service.agent_workflow import AgentWorkflow
from service.contracts import content_hash
from service.errors import ServiceError
from service.model_gateway import (FakeRouteProvider, GatewayPolicy, ModelContext,
                                   ModelGateway, ModelRequest, OpenRouterRouteProvider)
from service.model_store import PostgresModelStore
from service.observability import LangfuseConfig, LangfuseExporter, MetadataObserver


def verified_prices(model):
    """Verify public prices and exact canonical alias, without credentials."""
    try:
        with httpx.Client(trust_env=False, timeout=15, follow_redirects=False) as client:
            response = client.get("https://openrouter.ai/api/v1/models")
            response.raise_for_status()
            record = next(row for row in response.json()["data"] if row["id"] == model)
        canonical = record["canonical_slug"]
        if (not isinstance(canonical, str) or not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", canonical)
                or canonical.partition("/")[0] != model.partition("/")[0]):
            raise ValueError()
        prices = record["pricing"]
        input_price, output_price = Decimal(prices["prompt"]), Decimal(prices["completion"])
        if not all(p.is_finite() and p > 0 for p in (input_price, output_price)):
            raise ValueError()
        return input_price, output_price, content_hash({"model": model, "canonical_slug": canonical,
                                                       "pricing": prices}), canonical
    except Exception:
        raise ServiceError("MODEL_PRICING_UNVERIFIED", 503) from None


def verified_zdr_endpoint(model, input_price, output_price, *, input_token_bound):
    """Require the fixed Azure route in the public ZDR catalog; never reroute."""
    try:
        if type(input_token_bound) is not int or not 0 < input_token_bound <= 10000:
            raise ValueError()
        with httpx.Client(trust_env=False, timeout=15, follow_redirects=False) as client:
            response = client.get("https://openrouter.ai/api/v1/endpoints/zdr")
            response.raise_for_status()
            matches = [row for row in response.json()["data"]
                       if row.get("model_id") == model and row.get("tag") == "azure"]
        if len(matches) != 1:
            raise ValueError()
        record = matches[0]
        required = {"max_completion_tokens", "response_format", "structured_outputs"}
        if (record["provider_name"] != "Azure" or record["safety_retention"] is not False
                or type(record["status"]) is not int or record["status"] != 0
                or not isinstance(record["supported_parameters"], list)
                or not required.issubset(record["supported_parameters"])):
            raise ValueError()
        prices = record["pricing"]
        if (Decimal(prices["prompt"]) != input_price or Decimal(prices["completion"]) != output_price
                or Decimal(prices.get("request", "0")) != 0):
            raise ValueError()
        # The fixed synthetic route cannot reach long-context price tiers.
        # Refuse any new/unknown tier that could affect its reservation.
        tiers = prices.get("overrides", [])
        if not isinstance(tiers, list):
            raise ValueError()
        for tier in tiers:
            threshold = tier["min_prompt_tokens"]
            if type(threshold) is not int or threshold <= input_token_bound:
                raise ValueError()
        return content_hash({"model": model, "provider": "azure", "safety_retention": False,
                             "pricing": prices, "required_parameters": sorted(required),
                             "input_token_bound": input_token_bound})
    except Exception:
        raise ServiceError("MODEL_ZDR_PROFILE_UNVERIFIED", 503) from None


class _ModelGuard:
    """Model attempts use canonical money accounting; tool counters stay tools."""
    def __init__(self, budget, execution_id):
        self._budget, self._execution_id = budget, execution_id

    def check(self):
        self._budget.check()

    def reserve(self, execution_id):
        if execution_id != self._execution_id:
            raise ServiceError("EXECUTION_BUDGET_MISMATCH", 409)
        self.check()


def build_agent_host(db, identity, gateway, context_provider, settings, *, receipt_path=None):
    store = PostgresModelStore(db)
    store.initialize_budget(settings.budget_id, prior_spend_usd=settings.prior_spend_usd,
                            authorization_ref=settings.budget_authorization_ref)
    exporter = (LangfuseExporter(LangfuseConfig(settings.langfuse_base_url,
                  settings.langfuse_public_key, settings.langfuse_secret_key))
                if settings.langfuse_enabled else None)
    observer = MetadataObserver(exporter, receipt_path=receipt_path)
    options = {"budget_id": settings.budget_id}
    if settings.mode == "live":
        if not settings.openrouter_api_key:
            raise ServiceError("MODEL_CREDENTIAL_MISSING", 503)
        if settings.prior_spend_usd is None:
            raise ServiceError("MODEL_PRIOR_SPEND_UNKNOWN", 503)
        prompt, completion, digest, canonical = verified_prices(settings.model)
        # First-party OpenAI is not ZDR-eligible. This is one fixed, verified
        # Azure route for the same approved model, not a fallback list.
        provider = OpenRouterRouteProvider(settings.openrouter_api_key, model_id=settings.model,
            canonical_model_id=canonical, provider_slug="azure", output_token_parameter="max_completion_tokens",
            input_usd_per_token=prompt, output_usd_per_token=completion)
        bound = provider.input_token_bound(ModelRequest(call_id="profile-preflight", features=("unknown",),
                                                       max_output_tokens=256))
        endpoint_digest = verified_zdr_endpoint(settings.model, prompt, completion, input_token_bound=bound)
        options.update(live_enabled=True, transmission_approved=True,
                       input_usd_per_token=prompt, output_usd_per_token=completion,
                       pricing_version=content_hash({"catalog": digest, "endpoint": endpoint_digest}))
    else:
        provider = FakeRouteProvider()
    models = ModelGateway(store, provider, identity.authorize, observer=observer,
                          policy=GatewayPolicy(**options))

    def route(principal, context, segment_id, features, budget):
        model_context = ModelContext(**context.model_dump(), segment_id=segment_id,
            parent_span_id=segment_id, budget_id=settings.budget_id,
            workflow_version=WORKFLOW_VERSION, deadline_at=budget.deadline_at)
        # No question, business quantities, item IDs, sources or raw results
        # cross this port. Only the seven schema-checked feature flags do.
        request = ModelRequest(call_id="route-v1", features=tuple(features))
        return models.bind(principal=principal, context=model_context,
                           budget=_ModelGuard(budget, context.execution_id)).complete(request).capability

    workflow = AgentWorkflow(db, identity, gateway, context_provider,
                             route_model=route, observer=observer)
    workflow.setup()

    def summary(execution_id=None):
        return {"mode": settings.mode, **store.execution_summary(execution_id)}

    return workflow, summary, observer
