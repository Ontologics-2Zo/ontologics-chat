import test from 'node:test';
import assert from 'node:assert/strict';
import { materialAlerts, changeText, resetMaterialWatch, CHANGE_SHOW_MS } from '../web/mks2/material_watch.js';

const snap = (version, shortage, state = 'PARTIAL') => ({ meta: { company_id: 'c', identity_revision: 1, ledger_version: version },
  material_requirements: { state, items: [{ item_id: 'IM-0016', planned_shortage_qty: shortage == null ? null : String(shortage), shortage_qty: shortage == null ? null : '0' },
    { item_id: 'IM-0008', planned_shortage_qty: '0' }] } });

test('alerts mark short items and show a fresh change across ledger versions', () => {
  resetMaterialWatch();
  const first = materialAlerts(snap(9, 23), 1000);
  assert.deepEqual(first.map(a => [a.item_id, a.state, a.shortage, a.change]), [['IM-0016', 'short', 23, null], ['IM-0008', 'ok', 0, null]]);
  const after = materialAlerts(snap(10, 28), 2000);
  assert.deepEqual([after[0].change.before, after[0].change.after, after[0].change.kind], [23, 28, 'worse']);
  assert.equal(changeText(after[0].change), '부족 23 → 28 ↑');
  assert.equal(after[1].change, null);
  // Same version again (another panel mounting): same change, not a new comparison.
  assert.equal(materialAlerts(snap(10, 28), 3000)[0].change.kind, 'worse');
  // The chip expires.
  assert.equal(materialAlerts(snap(10, 28), 2000 + CHANGE_SHOW_MS + 1)[0].change, null);
  const fixed = materialAlerts(snap(15, 0), 50000);
  assert.deepEqual([fixed[0].state, fixed[0].change.kind], ['ok', 'resolved']);
  assert.equal(changeText(fixed[0].change), '부족 28 → 0 · 해소 ✓');
});

test('unknown stays unknown, a blocked BOM shows no shortage and an older version never compares', () => {
  resetMaterialWatch();
  assert.equal(materialAlerts(snap(5, null), 0)[0].state, 'unknown');
  assert.equal(materialAlerts(snap(6, 23, 'BLOCKED_BOM'), 0)[0].state, 'unknown');
  resetMaterialWatch();
  materialAlerts(snap(12, 28), 0);
  assert.equal(materialAlerts(snap(11, 23), 0)[0].change, null);
});

test('the change follows the chosen basis (planned input or production target)', () => {
  resetMaterialWatch();
  const item = (planned, base) => ({ meta: { company_id: 'c', identity_revision: 1, ledger_version: planned === 23 ? 9 : 10 },
    material_requirements: { state: 'PARTIAL', items: [{ item_id: 'IM-0016', planned_shortage_qty: String(planned), shortage_qty: String(base) }] } });
  materialAlerts(item(23, 20), 0);
  assert.equal(changeText(materialAlerts(item(28, 25), 1)[0].change), '부족 23 → 28 ↑');
  assert.equal(changeText(materialAlerts(item(28, 25), 1, { basis: 'base' })[0].change), '부족 20 → 25 ↑');
});

test('switching account in the same company keeps the comparison', () => {
  resetMaterialWatch();
  const at = (version, revision, shortage) => ({ meta: { company_id: 'c', identity_revision: revision, ledger_version: version },
    material_requirements: { state: 'PARTIAL', items: [{ item_id: 'IM-0016', planned_shortage_qty: String(shortage) }] } });
  materialAlerts(at(11, 'author-1', 28), 0);
  assert.equal(changeText(materialAlerts(at(12, 'reviewer-7', 0), 1)[0].change), '부족 28 → 0 · 해소 ✓');
  resetMaterialWatch();
  materialAlerts({ ...at(11, 'a', 28), meta: { company_id: 'other', ledger_version: 11 } }, 0);
  assert.equal(materialAlerts(at(12, 'a', 0), 1)[0].change, null);  // another company never compares
});
