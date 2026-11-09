import assert from 'node:assert/strict';
import test from 'node:test';
import { formatQuantity, mountDashboard, renderMaterialTableRows } from '../web/mks2/dashboard.js';

test('positive fractional quantities retain every decimal digit', () => {
  assert.equal(formatQuantity('0.00001'), '0.00001');
  assert.equal(formatQuantity('0.000001'), '0.000001');
  assert.equal(formatQuantity('12.000010'), '12.00001');
  assert.equal(formatQuantity('1.234567'), '1.234567');
  assert.equal(formatQuantity(0.0000001), '0.0000001');
});

test('decimal strings retain precision beyond JavaScript safe integers', () => {
  assert.equal(formatQuantity('9007199254740993.123456'), '9,007,199,254,740,993.123456');
  assert.equal(formatQuantity('001234.560000'), '1,234.56');
  assert.equal(formatQuantity('-0.000010'), '-0.00001');
  assert.equal(formatQuantity('-0.000000'), '0');
  assert.equal(formatQuantity(1000), '1,000');
});

test('unknown or invalid quantities do not become zero', () => {
  for (const value of [null, undefined, '', ' ', true, false, NaN, Infinity, 'NaN', 'Infinity', '<b>1</b>']) {
    assert.equal(formatQuantity(value), '미확인');
  }
});

test('material table renders a positive shortage instead of a rounded zero', () => {
  const previousDocument = globalThis.document;
  globalThis.document = { addEventListener() {} };
  const container = {
    innerHTML: '', classList: { add() {}, remove() {} },
    addEventListener() {}, replaceChildren() {},
  };
  const snapshot = {
    summary: { bom_status: 'POSTED_SOURCE', production_remaining_qty: 11 },
    meta: { ledger_version: 2 },
    material_requirements: {
      bom_id: 'synthetic-test', bom_version: 2, state: 'PARTIAL',
      basis: { production_remaining_qty: 11, planned_input_qty: 12, plan_yield: '0.94' },
      row_count: 1, rows: [{ row_id: 'synthetic-row', decision: {} }],
      items: [{ item_id: 'synthetic-item', unit: 'piece', conditional: true,
        required_qty: '12.00001', available_qty: '12', shortage_qty: '0.00001',
        row_ids: ['synthetic-row'], inventory_scope: 'LOT' }],
    },
  };
  let dashboard;
  try {
    dashboard = mountDashboard(container, { snapshot });
    const itemTable = container.innerHTML.match(/data-register="material-items"[\s\S]*?<\/table>/)?.[0];
    assert.ok(itemTable);
    assert.match(itemTable, /<td>12\.00001<small>/);
    assert.match(itemTable, /<td>12<small>/);
    assert.match(itemTable, /<td>0\.00001<\/td>/);
    assert.match(container.innerHTML, /전체 BOM 중 일부 품목 기준/);
    assert.match(container.innerHTML, /재고는 합성 초기 원장 기준/);
  } finally {
    dashboard?.destroy();
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  }
});

test('item explanation escapes source text and distinguishes unit from review state', () => {
  const row = { item_id: 'sample-item', item_entity_id: 'urn:sample', item_name: '<script>unsafe</script>',
    unit: 'EA', unit_label: '개 (EA)', quantity_per_unit: '2', required_qty: '22',
    available_qty: null, shortage_qty: null, quantity_status: 'UNKNOWN', conditional: true,
    inventory_scope_label: '재고 범위 미확인', row_ids: ['sample'] };
  const html = renderMaterialTableRows([row], { targetQty: 11 });
  assert.ok(!html.includes('<script>'));
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /단위: 개 \(EA\)/);
  assert.match(html, /11대 × 2/);
  assert.match(html, /판정 보류/);
  assert.match(html, /시연 입력 · 기술검수 대기/);
  assert.ok(!html.includes('계산상 충족'));
});
