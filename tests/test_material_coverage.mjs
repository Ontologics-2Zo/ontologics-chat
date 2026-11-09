import assert from 'node:assert/strict';
import test from 'node:test';
import { materialCoverage, renderMaterialCoverage } from '../web/mks2/dashboard.js';

const item = overrides => ({ item_id: 'sample-item', item_entity_id: 'urn:sample:item', item_name: '합성 검사 품목',
  required_qty: '20', available_qty: '30', shortage_qty: '0', quantity_status: 'COVERED', unit: 'EA', ...overrides });

test('surplus retains the ratio while the bar stops at the requirement', () => {
  assert.deepEqual(materialCoverage(item()), { state: 'covered', width: 100, label: '150%' });
  const html = renderMaterialCoverage([item()]);
  assert.match(html, /width:100%/); assert.match(html, /150%/); assert.match(html, /30\/20/);
});

test('tiny positive shortages remain exact and never become full coverage', () => {
  const source = item({ required_qty: '9007199254740993.00001', available_qty: '9007199254740993', shortage_qty: '0.00001', quantity_status: 'SHORTAGE' });
  const result = materialCoverage(source);
  assert.equal(result.state, 'shortage'); assert.ok(result.width < 100);
  const html = renderMaterialCoverage([source]);
  assert.match(html, /−0\.00001/); assert.ok(!html.includes('<b>100%'));
});

test('unknown balances or server UNKNOWN do not become zero or covered', () => {
  for (const overrides of [{ available_qty: null }, { required_qty: null }, { shortage_qty: null },
    { quantity_status: 'UNKNOWN' }, { available_qty: '' }, { available_qty: '-1' }, { available_qty: Infinity }]) {
    const value = item(overrides), result = materialCoverage(value);
    assert.equal(result.state, 'unknown'); assert.equal(result.width, null);
    assert.ok(!renderMaterialCoverage([value]).includes('style="width:'));
  }
  assert.equal(renderMaterialCoverage([]), '');
});

test('confirmed zero stock and zero demand remain distinct', () => {
  assert.deepEqual(materialCoverage(item({ available_qty: '0', shortage_qty: '20', quantity_status: 'SHORTAGE' })),
    { state: 'shortage', width: 0, label: '0%' });
  assert.deepEqual(materialCoverage(item({ required_qty: '0' })), { state: 'not_required', width: null, label: '소요 없음' });
  assert.equal(materialCoverage(item({ required_qty: '0', available_qty: null })).state, 'unknown');
});

test('planned basis uses its own server requirement, shortage and judgment', () => {
  const source = item({ planned_required_qty: '40', planned_shortage_qty: '10', planned_quantity_status: 'SHORTAGE', conditional: true });
  const before = JSON.stringify(source);
  assert.equal(materialCoverage(source).state, 'covered');
  assert.deepEqual(materialCoverage(source, { planned: true }), { state: 'shortage', width: 75, label: '75%' });
  const html = renderMaterialCoverage([source], { planned: true });
  assert.match(html, /−10/); assert.match(html, /30\/40/); assert.match(html, /시연 적용 가정/);
  assert.equal(JSON.stringify(source), before);
});

test('inconsistent status is held and very small positive ratios are not labelled zero', () => {
  assert.equal(materialCoverage(item({ quantity_status: 'SHORTAGE', shortage_qty: '1' })).state, 'unknown');
  const result = materialCoverage(item({ required_qty: '1000000', available_qty: '0.000001', shortage_qty: '999999.999999', quantity_status: 'SHORTAGE' }));
  assert.equal(result.label, '1% 미만'); assert.equal(result.state, 'shortage');
});

test('item text and evidence attributes are escaped and quantities stay literal', () => {
  const html = renderMaterialCoverage([item({ item_id: '<script>bad</script>', item_entity_id: '\" onfocus=bad', item_name: '<img src=x>', unit_label: '<tag>' })]);
  assert.ok(!html.includes('<script>') && !html.includes('<img'));
  assert.match(html, /&lt;script&gt;/); assert.match(html, /&quot; onfocus=bad/);
  assert.match(html, /role="img" tabindex="0" aria-label=/);
});
