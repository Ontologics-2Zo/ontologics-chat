from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from service.agent_config import load_agent_settings
from service.errors import ServiceError


class AgentConfigTests(unittest.TestCase):
    def test_allowlist_no_interpolation_no_database_override_or_secret_repr(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('DATABASE_URL=private-db\nONTO_OPENROUTER_API_KEY="${DO_NOT_EXPAND}"\n')
            result = load_agent_settings(path, environ={"DATABASE_URL": "other"})
        self.assertEqual(result.openrouter_api_key, '${DO_NOT_EXPAND}')
        self.assertFalse(hasattr(result, 'database_url'))
        self.assertNotIn('DO_NOT_EXPAND', repr(result))
        self.assertNotIn('DO_NOT_EXPAND', str(result.public()))
        self.assertIsNone(result.prior_spend_usd)

    def test_explicit_environment_precedence(self):
        result = load_agent_settings(environ={"ONTO_MODEL_PRIOR_SPEND_USD": "1.20"})
        self.assertEqual(result.prior_spend_usd, Decimal('1.20'))
        self.assertEqual(result.mode, 'fake')

    def test_local_content_is_explicit_and_evaluation_purpose_disables_it(self):
        self.assertFalse(load_agent_settings().langfuse_content_enabled)
        values = {"ONTO_LANGFUSE_ENABLED": "true", "LANGFUSE_PUBLIC_KEY": "synthetic-public",
                  "LANGFUSE_SECRET_KEY": "synthetic-secret", "ONTO_LANGFUSE_CONTENT_ENABLED": "true"}
        self.assertTrue(load_agent_settings(environ=values).public()["local_observability_content_enabled"])
        evaluation = load_agent_settings(environ={**values, "ONTO_OBSERVABILITY_PURPOSE": "evaluation"})
        self.assertFalse(evaluation.public()["local_observability_content_enabled"])
        for overrides in ({"ONTO_LANGFUSE_CONTENT_ENABLED": "yes"}, {"ONTO_OBSERVABILITY_PURPOSE": "automatic"}):
            with self.subTest(overrides=overrides), self.assertRaises(ServiceError):
                load_agent_settings(environ=overrides)

    def test_invalid_spend_model_mode_and_remote_telemetry_rejected(self):
        for values in ({"ONTO_MODEL_PRIOR_SPEND_USD": "NaN"},
                       {"ONTO_MODEL_PRIOR_SPEND_USD": "-1"},
                       {"ONTO_MODEL": "unapproved"}, {"ONTO_MODEL_MODE": "auto"},
                       {"LANGFUSE_BASE_URL": "https://example.org"},
                       {"LANGFUSE_BASE_URL": "http://secret@127.0.0.1:53000"},
                       {"ONTO_LANGFUSE_ENABLED": "true"}):
            with self.subTest(values=values), self.assertRaises(ServiceError):
                load_agent_settings(environ=values)
