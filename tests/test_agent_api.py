"""Authenticated Agent HTTP tests with synthetic data and isolated PostgreSQL."""
import os
import unittest
from unittest import mock
from uuid import uuid4

from psycopg import sql

from fastapi.testclient import TestClient

from service.agent_config import AgentSettings
from service.db import Database
from service.main import create_app
import test_service_api as api_fixture

ORIGIN = api_fixture.ORIGIN


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL not configured")
class AgentApiTests(unittest.TestCase):
    setUpClass = classmethod(api_fixture.ServiceApiIntegrationTests.setUpClass.__func__)
    tearDownClass = classmethod(api_fixture.ServiceApiIntegrationTests.tearDownClass.__func__)
    drop_schema = api_fixture.ServiceApiIntegrationTests.drop_schema
    headers = api_fixture.ServiceApiIntegrationTests.headers
    select = api_fixture.ServiceApiIntegrationTests.select
    mark_projection_ready = api_fixture.ServiceApiIntegrationTests.mark_projection_ready

    def setUp(self):
        api_fixture.ServiceApiIntegrationTests.setUp(self)
        self.mark_projection_ready()
        self.client.close()
        self.app = create_app(self.db, private_root=str(self.private), origin=ORIGIN,
            dev_mode=True, catalog=self.catalog, projector=self.projector,
            agent_settings=AgentSettings())
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(self.client.close)
        self.auth = self.select()
        self.client.headers.update(self.headers(self.auth))

    def create(self, question="고객 도착까지 얼마나 남았어?", **extra):
        r = self.client.post('/api/mks2/agent/executions', json={
            'question': question, 'ledger_version': 1, 'idempotency_key': 'request-1', **extra})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_fake_agent_same_snapshot_result_sse_and_idempotent_run(self):
        before = self.client.get('/api/mks2/snapshot').json()
        created = self.create()
        path = '/api/mks2/agent/executions/' + created['execution_id']
        r = self.client.post(path + '/run', json={})
        self.assertEqual(r.status_code, 200, r.text)
        state = r.json()
        self.assertEqual(state['status'], 'COMPLETED', state)
        self.assertEqual(state['model']['live_calls'], 0)
        self.assertEqual(state['model']['fake_calls'], 1)
        self.assertEqual(state['tool_calls'], 1)
        self.assertEqual(state['result']['meta']['ledger_version'], 1)
        again = self.client.post(path + '/run', json={}).json()
        self.assertEqual(again['model'], state['model'])
        self.assertEqual(again['tool_calls'], 1)
        events = self.client.get(path + '/events')
        self.assertIn('text/event-stream', events.headers['content-type'])
        self.assertIn('event: progress', events.text)
        self.assertNotIn('question', events.text)
        self.assertEqual(self.client.get('/api/mks2/snapshot').json(), before)

    def test_explicit_capability_bypasses_model_and_other_owner_denied(self):
        state = self.create(capability='fulfillment_read')
        path = '/api/mks2/agent/executions/' + state['execution_id']
        state = self.client.post(path + '/run', json={}).json()
        self.assertEqual(state['status'], 'COMPLETED', state)
        self.assertEqual(state['model']['fake_calls'], 0)
        other = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(other.close)
        r = other.post('/api/mks2/session', json={'profile_id': 'dev-reviewer-b'}, headers=self.headers())
        other.headers.update(self.headers(r.json()))
        self.assertEqual(other.get(path).status_code, 404)
        self.assertEqual(other.get(path + '/events').status_code, 404)

    def test_a_completed_delivery_answer_sends_the_demo_notice(self):
        cue = self.app.state.demo_cue = mock.Mock()
        state = self.create('납기 안에 고객에게 도착할 수 있어?', capability='fulfillment_read')
        state = self.client.post('/api/mks2/agent/executions/' + state['execution_id'] + '/run', json={}).json()
        self.assertEqual(state['status'], 'COMPLETED', state)
        self.assertIn('delivery', state['result']['intent'])
        cue.delivery_answered.assert_called_once_with('DEMO-A')
        other = self.create('수율을 적용하면 몇 대 투입하고 어떤 자재가 부족해?', capability='material_analysis', idempotency_key='request-2')
        other = self.client.post('/api/mks2/agent/executions/' + other['execution_id'] + '/run', json={}).json()
        self.assertEqual(other['status'], 'COMPLETED', {k: other.get(k) for k in ('status', 'error_code', 'interrupt')})
        self.assertNotIn('delivery', other['result']['intent'])
        cue.delivery_answered.assert_called_once()

    def test_model_records_can_live_in_a_shared_budget_database(self):
        schema = "test_budget_" + uuid4().hex
        with self.base.connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        def drop():
            with self.base.connect() as conn:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        self.addCleanup(drop)
        budget = Database(os.environ["TEST_DATABASE_URL"], schema=schema)
        budget.migrate()
        app = create_app(self.db, private_root=str(self.private), origin=ORIGIN, dev_mode=True,
            catalog=self.catalog, projector=self.projector, agent_settings=AgentSettings(), model_budget_db=budget)
        client = TestClient(app, base_url=ORIGIN)
        self.addCleanup(client.close)
        auth = client.post('/api/mks2/session', json={'profile_id': 'dev-operator-a'}, headers=self.headers()).json()
        client.headers.update(self.headers(auth))
        created = client.post('/api/mks2/agent/executions', json={'question': '고객 도착까지 얼마나 남았어?',
            'ledger_version': 1, 'idempotency_key': 'shared-1'}).json()
        state = client.post('/api/mks2/agent/executions/' + created['execution_id'] + '/run', json={}).json()
        self.assertEqual((state['status'], state['model']['fake_calls']), ('COMPLETED', 1), state)
        def count(db):
            with db.connect() as conn:
                return conn.execute("SELECT count(*) AS n FROM model_attempts").fetchone()["n"]
        self.assertEqual((count(budget), count(self.db)), (1, 0))

    def test_shared_budget_database_without_model_tables_is_refused(self):
        schema = "test_empty_" + uuid4().hex
        with self.base.connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        def drop():
            with self.base.connect() as conn:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        self.addCleanup(drop)
        from service.errors import ServiceError
        with self.assertRaises(ServiceError) as caught:
            create_app(self.db, private_root=str(self.private), origin=ORIGIN, dev_mode=True, catalog=self.catalog,
                projector=self.projector, agent_settings=AgentSettings(),
                model_budget_db=Database(os.environ["TEST_DATABASE_URL"], schema=schema))
        self.assertEqual(caught.exception.code, "MODEL_BUDGET_DATABASE_UNAVAILABLE")

    def test_request_authority_injection_and_missing_csrf_rejected(self):
        body = {'question': '도착 수량', 'ledger_version': 1, 'idempotency_key': 'x', 'actor_id': 'fake'}
        self.assertEqual(self.client.post('/api/mks2/agent/executions', json=body).status_code, 422)
        self.client.headers.pop('X-CSRF-Token')
        body.pop('actor_id')
        self.assertEqual(self.client.post('/api/mks2/agent/executions', json=body).status_code, 403)
