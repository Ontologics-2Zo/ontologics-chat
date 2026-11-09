import assert from 'node:assert/strict';
import test from 'node:test';
import { renderProductRow, createProductReview } from '../web/mks2/product.js';

function response(rowId = 'a'.repeat(64)) {
  return { meta: { ledger_version: 2 }, bom_id: 'independent-bom', bom_version: 3, payload_hash: 'b'.repeat(64),
    ledger_mutations: 0, model_calls: 0, all_row_count: 74, source_conflicts: [], rows: [{ row_id: rowId,
      source: { source_description: 'Synthetic component', source_quantity: '0.0000000000000000123', locator: 'Independent!row=2', designators: ['X1'] },
      current_decision: { application_status: 'UNRESOLVED', review_basis: 'UNREVIEWED', quantity_per_unit: null, item_id: null, unit: null },
      candidate_count: 1, candidates: [{ item_entity_id: 'urn:synthetic-part', item_id: 'synthetic-part', display_name: 'Synthetic candidate',
        origin: 'synthetic_operation', match_methods: ['EXACT_PART_TOKEN'], evidence_refs: [{ locator: '/synthetic/item', source_sha256: 'c'.repeat(64) }] }],
      issues: ['SOURCE_UNIT_UNCONFIRMED', 'TECHNICAL_EQUIVALENCE_UNVERIFIED'], evidence_refs: [{ locator: 'Independent!row=2', source_sha256: 'c'.repeat(64) }],
      retrieval: { status: 'NO_HIT' } }] };
}

test('Product presentation distinguishes current decision, candidate and unverified unit without apply commands', () => {
  const data = response(), before = JSON.stringify(data);
  const html = renderProductRow(data.rows[0], data);
  for (const text of ['현재 등록된 판단', '미연결', '미검수 후보', '단위를 확인하지 못했습니다', '실물·기술 동일성', '일치 근거 없음']) assert.ok(html.includes(text), text);
  assert.ok(html.includes('0.0000000000000000123'));
  assert.ok(!html.includes('data-bom-action') && !html.includes('data-product-apply'));
  assert.equal(JSON.stringify(data), before);
});

test('supplier source and candidate strings are escaped, including evidence attributes', () => {
  const data = response(), attack = '<img src=x onerror="attack()"><script>attack()</script>';
  const row = data.rows[0]; row.source.source_description = attack; row.source.locator = attack;
  row.current_decision.evidence_note = attack; row.candidates[0].display_name = attack; row.candidates[0].item_entity_id = attack;
  const html = renderProductRow(row, data);
  assert.ok(html.includes('&lt;img')); assert.ok(!html.includes('<img') && !html.includes('<script>'));
  assert.ok(!html.includes('data-product-item="<'));
});

const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const settle = () => new Promise(resolve => setImmediate(resolve));
function snapshot() { return { meta: { ledger_version: 2 } }; }
function documentRecord() { return { id: 'independent-bom', version: 3, payload_hash: 'b'.repeat(64) }; }

test('existing BOM row selection ignores stale results and sends exact read-only context', async () => {
  const first = deferred(), second = deferred(), calls = [];
  const view = createProductReview({ api: (path, body) => { calls.push({ path, body }); return calls.length === 1 ? first.promise : second.promise; } });
  const firstLoad = view.load(snapshot(), documentRecord(), 'a'.repeat(64));
  const secondLoad = view.load(snapshot(), documentRecord(), 'd'.repeat(64));
  first.resolve(response()); await firstLoad; assert.ok(!view.render().includes('data-product-inline'));
  second.resolve(response('d'.repeat(64))); await secondLoad; assert.ok(view.render().includes('data-product-inline'));
  assert.ok(!view.render().includes('<button') && !view.render().includes('<select') && !view.render().includes('role="dialog"'));
  assert.equal(calls.length, 2); assert.ok(calls.every(call => call.path === '/api/mks2/product/review'));
  assert.equal(calls[1].body.row_id, 'd'.repeat(64)); assert.equal(calls[1].body.payload_hash, 'b'.repeat(64));
  assert.ok(!('approved_by' in calls[1].body));
  await view.load(snapshot(), documentRecord(), 'd'.repeat(64)); assert.equal(calls.length, 2);
});

test('closed BOM or mismatched-version responses cannot display candidates as current', async () => {
  const pending = deferred(); let changes = 0;
  const view = createProductReview({ api: () => pending.promise, onChange: () => changes++ });
  const loading = view.load(snapshot(), documentRecord(), 'a'.repeat(64)); view.reset();
  pending.resolve(response()); await loading; assert.equal(view.render(), ''); assert.equal(changes, 0);
  const wrong = response(); wrong.meta.ledger_version = 3;
  const other = createProductReview({ api: async () => wrong });
  await other.load(snapshot(), documentRecord(), 'a'.repeat(64));
  assert.ok(other.render().includes('조회하지 못했습니다'));
  assert.ok(!other.render().includes('data-product-inline'));
});

test('embedded candidates add no navigation and do not replace the original BOM decision form', () => {
  const data = response(), html = renderProductRow(data.rows[0], data, { embedded: true });
  assert.ok(html.includes('미검수 후보'));
  for (const forbidden of ['<button', '<form', '<input', '<select', 'role="dialog"', '현재 등록된 판단']) assert.ok(!html.includes(forbidden), forbidden);
});
