import test from 'node:test';
import assert from 'node:assert/strict';
import { isBomPosted } from '../web/mks2/dashboard.js';

test('BOM entry stays registration until the server reports a posted BOM', () => {
  assert.equal(isBomPosted({ summary: { bom_status: 'unregistered' }, bom: null }), false);
  assert.equal(isBomPosted({ summary: { bom_status: 'POSTED_SOURCE' }, bom: null }), false);
  assert.equal(isBomPosted({ summary: { bom_status: 'POSTED_SOURCE' }, bom: { bom_id: 'b-1' } }), true);
  assert.equal(isBomPosted(undefined), false);
});

test('the round cards fill from the same snapshot: stages, days to due, records and flow', async () => {
  const { roundStageRows, daysToDue, fulfilmentRecords, roundFlowSteps } = await import('../web/mks2/dashboard.js');
  const row = { promised_qty: 100, arrived_qty: 32, transit_qty: 3, warehouse_remaining_qty: 15, good_not_received_qty: 10, production_remaining_qty: 40 };
  assert.deepEqual(roundStageRows(row).map(stage => [stage.label, stage.qty, stage.share]),
    [['도착', 32, 32], ['운송 중', 3, 3], ['창고', 15, 15], ['입고 대기', 10, 10], ['남은 생산', 40, 40]]);
  assert.equal(daysToDue('2026-11-27', new Date(2026, 10, 12)), 15);
  assert.equal(daysToDue('미확인'), null);
  const snapshot = {
    procurement: { documents: [
      { id: 'aaaa1111', kind: 'PR', status: 'POSTED', content: { kind: 'PR', quantity: 30 } },
      { id: 'bbbb2222', kind: 'PO', status: 'POSTED', content: { kind: 'PO', quantity: 30 } },
      { id: 'cccc3333', kind: 'SHIPMENT', status: 'POSTED', content: { kind: 'SHIPMENT', board_serials: ['B1', 'B2'] } }] },
    events: [{ document_kind: 'SHIPMENT', document_ref: { id: 'cccc3333' }, recorded_at: '2026-11-12T05:00:00Z', ledger_version: 9 },
             { document_kind: 'PO', document_ref: { id: 'bbbb2222' }, recorded_at: '2026-11-12T04:00:00Z', ledger_version: 8 }] };
  assert.deepEqual(fulfilmentRecords(snapshot).map(record => [record.label, record.qty, record.version]), [['출하 SH-CCCC', '2대', 9]]);
  const steps = roundFlowSteps(snapshot);
  assert.deepEqual(steps.slice(0, 4).map(step => step.state), ['done', 'done', 'next', 'todo']);
  assert.equal(steps.find(step => step.kind === 'SHIPMENT').state, 'done');
  assert.equal(fulfilmentRecords({}).length, 0);
});
