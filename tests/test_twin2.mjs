import assert from 'node:assert/strict';
import test from 'node:test';
import { formatTwinValue, twinMaterialStatus, twinMaterialSummary, twinPromiseStatus, twinNodeType,
  twinRecentEvents, twinEventTime, twinOntologyCounts, twinOriginNote, prepareTwinGraph,
  twinHighlight, twinRelationEvidence, twinAnswerZones, mountTwin } from '../web/mks2/twin2.js';

const item = extra => ({ item_id: 'synthetic-part', required_qty: '20', available_qty: '15', shortage_qty: '5', quantity_status: 'SHORTAGE', ...extra });
const node = (id, type = 'InternalItem') => ({ node_id: id, type, node_type: 'design', label: id });
const edge = (id, source, target, predicate = 'synthetic:uses') => ({ edge_id: id, source_node_id: source, target_node_id: target, relation_type: predicate, label: '합성 관계' });
const meta = { company_id: 'synthetic-company', identity_revision: 'synthetic-session', snapshot_id: 'synthetic-snapshot', ledger_version: 7, projection_version: 7 };
const highlight = extra => ({ companyId: meta.company_id, identityRevision: meta.identity_revision,
  snapshotId: meta.snapshot_id, ledgerVersion: 7, projectionVersion: 7, ...extra });

test('decimal strings retain exact shortages beyond floating-point precision', () => {
  const value = item({ required_qty: '9007199254740993.000000001', available_qty: '9007199254740993', shortage_qty: '0.000000001' });
  assert.deepEqual(twinMaterialStatus(value), { k: 'bad', text: '부족 0.000000001' });
  assert.equal(formatTwinValue(value.required_qty), '9007199254740993.000000001');
  assert.equal(formatTwinValue(0.0000001), '0.0000001');
});
test('null, inconsistent and unknown quantity judgments never become covered', () => {
  for (const changes of [{ shortage_qty: null }, { available_qty: null }, { required_qty: null },
    { quantity_status: 'UNKNOWN' }, { shortage_qty: '0' }, { available_qty: '-1' }, { shortage_qty: Infinity }]) {
    assert.equal(twinMaterialStatus(item(changes)).k, 'warn');
  }
  assert.equal(twinMaterialStatus(item({ required_qty: '0', available_qty: '0', shortage_qty: '0', quantity_status: 'COVERED' })).k, 'ok');
});
test('calculated means arithmetic completed, not that shortages disappeared', () => {
  const data = { state: 'CALCULATED', full_coverage: true, unknown_shortage_count: 0, unresolved_count: 0, items: [item()] };
  assert.deepEqual(twinMaterialSummary(data), { k: 'bad', text: '부족 1품목' });
  data.items = [item({ available_qty: '21', shortage_qty: '0', quantity_status: 'COVERED' })];
  assert.deepEqual(twinMaterialSummary(data), { k: 'ok', text: '계산상 충족' });
});
test('an unmapped active source uses current counts with no prior example fallback', () => {
  const data = { bom_id: 'synthetic-bom', state: 'PARTIAL', row_count: 35, applicable_count: 0,
    unresolved_count: 35, unknown_shortage_count: 35, items: [], full_coverage: false };
  const before = JSON.stringify(data);
  assert.equal(twinMaterialSummary(data).k, 'warn');
  assert.equal(twinMaterialSummary({ ...data, state: 'CALCULATED', full_coverage: true, unresolved_count: 0, unknown_shortage_count: 0 }).k, 'warn');
  assert.equal(twinMaterialSummary({ state: 'CALCULATED' }).k, 'warn');
  assert.equal(JSON.stringify(data), before);
});
test('remaining quantity must be confirmed zero before a completion label appears', () => {
  for (const value of [null, undefined, '', NaN, -1]) assert.equal(twinPromiseStatus(value).tone, 'warn');
  assert.equal(twinPromiseStatus(0).text, '원장상 완료');
  assert.equal(twinPromiseStatus(9).text, '진행 중');
});
test('current server type aliases keep their facility classification', () => {
  for (const [alias, type] of [['item', 'InternalItem'], ['quotation', 'SupplierOffer'], ['bom_line', 'BOMLine'], ['delivery_round', 'DeliveryRound']]) {
    assert.equal(twinNodeType({ entity_ref: { entity_type: alias } }), type);
    assert.equal(twinNodeType({ type }), type);
  }
  const data = prepareTwinGraph([{ node_id: 'a', entity_ref: { entity_type: 'item' } }, node('b', 'PartsReceiptLine'), node('c', 'PurchaseOrder')], []);
  assert.deepEqual(data.shown.map(value => value.zone), ['mat', 'mat', 'sup']);
});
test('source aggregation retains every member and internal, self, parallel and reverse edge ID', () => {
  const nodes = Array.from({ length: 42 }, (_, index) => node(`synthetic-row-${index}`, 'SourceRow'));
  const edges = [edge('a', nodes[6].node_id, nodes[7].node_id), edge('b', nodes[6].node_id, nodes[6].node_id),
    edge('c', nodes[0].node_id, nodes[6].node_id), edge('d', nodes[0].node_id, nodes[6].node_id, 'synthetic:other'),
    edge('e', nodes[6].node_id, nodes[0].node_id)];
  const before = JSON.stringify({ nodes, edges }), data = prepareTwinGraph(nodes, edges);
  const aggregate = data.shown.find(value => value.aggregate);
  assert.equal(aggregate.members.length, 37);
  assert.equal(new Set([...data.shown.filter(value => !value.aggregate).map(value => value.id), ...aggregate.members]).size, 42);
  assert.deepEqual(twinRelationEvidence(data, aggregate.id).map(value => value.id).sort(), ['a', 'b', 'c', 'd', 'e']);
  assert.equal(twinRelationEvidence(data, aggregate.id).filter(value => value.internal).length, 2);
  assert.equal(JSON.stringify({ nodes, edges }), before);
});
test('matching display labels do not collapse distinct predicates or reverse directions', () => {
  const data = prepareTwinGraph([node('a'), node('b')], [edge('one', 'a', 'b'), edge('two', 'a', 'b', 'synthetic:other'), edge('reverse', 'b', 'a')]);
  assert.equal(data.shownEdges.length, 3);
  assert.deepEqual(data.shownEdges.map(value => value.ledgerIds), [['one'], ['two'], ['reverse']]);
});
test('highlight requires exact evidence edge IDs and never infers from selected endpoint nodes', () => {
  const data = prepareTwinGraph([node('a'), node('b')], [edge('one', 'a', 'b'), edge('two', 'a', 'b', 'synthetic:other'), edge('reverse', 'b', 'a')]);
  assert.equal(twinHighlight(meta, data, highlight({ nodeIds: ['a', 'b'] })).edges.size, 0);
  const selected = twinHighlight(meta, data, highlight({ nodeIds: ['a'], edgeIds: ['two', 'outside'] }));
  assert.deepEqual([...selected.ledgerEdgeIds], ['two']);
  assert.equal(selected.edges.size, 1); assert.deepEqual([...selected.nodes].sort(), ['a', 'b']);
});
test('aggregation highlights exact original evidence counts including internal edges', () => {
  const nodes = Array.from({ length: 9 }, (_, index) => node(`n${index}`, 'SourceRow'));
  const data = prepareTwinGraph(nodes, [edge('first', 'n6', 'n7'), edge('second', 'n7', 'n8')]);
  const selected = twinHighlight(meta, data, highlight({ edgeIds: ['second'] }));
  assert.equal(selected.nodes.size, 1); assert.equal(selected.edges.size, 1);
  assert.deepEqual([...selected.ledgerEdgeIds], ['second']);
});
test('stale or missing identity, company, snapshot or version cannot apply a highlight', () => {
  const data = prepareTwinGraph([node('a')], []);
  for (const key of ['companyId', 'identityRevision', 'snapshotId', 'ledgerVersion', 'projectionVersion']) {
    assert.equal(twinHighlight(meta, data, highlight({ [key]: 'wrong' })), null);
    const missing = highlight(); delete missing[key]; assert.equal(twinHighlight(meta, data, missing), null);
  }
});
test('answer zones count only shown evidence nodes per facility, most first, excluding the core plate', () => {
  const data = prepareTwinGraph([node('i1'), node('i2'), node('r1', 'DeliveryRound'), node('s1', 'SourceRow')], []);
  const zones = twinAnswerZones(data, new Set(['r1', 'i1', 'i2', 's1', 'missing']));
  assert.deepEqual([...zones], [['mat', 2], ['cust', 1]]);
  assert.equal(twinAnswerZones(data, new Set(['s1'])).size, 0);
  assert.equal(twinAnswerZones(data, null).size, 0);
});
test('recent events are newest first and cannot include future ledger versions or raw unknown event labels', () => {
  const events = [2, 7, 4, 8, 1, 6, 5, 3].map(version => ({ ledger_version: version, event_type: '<private-note>' }));
  const before = JSON.stringify(events), result = twinRecentEvents(events, 7);
  assert.deepEqual(result.map(value => value.ledger_version), [7, 6, 5, 4, 3]);
  assert.ok(result.every(value => value.summary === '원장 사건'));
  assert.equal(JSON.stringify(events), before);
  assert.equal(twinRecentEvents([{ ledger_version: 1, summary: '서버 요약' }], 1)[0].summary, '서버 요약');
});
test('event times are Asia Seoul and reject malformed or impossible timestamps', () => {
  assert.equal(twinEventTime('2001-01-01T00:05:00Z'), '09:05');
  for (const value of ['bad', null, '2001-02-30T00:00:00Z', '2001-01-01T00:00:00', '2001-01-01T25:00:00Z']) assert.equal(twinEventTime(value), '—');
});
test('ontology counts use actual class, relation and shape fields, never the combined graph count', () => {
  assert.deepEqual(twinOntologyCounts({ classes: [1, 2], relations: [1], shapes: [], counts: { classes: 99 } }), { classes: 2, relations: 1, shapes: 0 });
  assert.deepEqual(twinOntologyCounts({ counts: { classes: 4, relations: 5, shapes: 6 } }), { classes: 4, relations: 5, shapes: 6 });
  assert.deepEqual(twinOntologyCounts({ meta: { node_count: 999 } }), { classes: null, relations: null, shapes: null });
});
test('origin notes retain synthetic, posted source and conditional application distinctions', () => {
  const text = twinOriginNote({ synthetic: true, data_origins: { inventory: 'mixed_sources' } }, { bom_id: 'synthetic-bom', has_demo_assumptions: true });
  for (const phrase of ['합성 초기 이관', '실제 BOM 원문 등록', '개발 입하·검사', '제조 가능성은 미확인', '시연 가정']) assert.ok(text.includes(phrase));
});

class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.style = { setProperty() {} }; this.attributes = {}; this.events = {}; this._text = ''; this.classList = { toggle() {} }; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(value => value.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  getAttribute(key) { return this.attributes[key]; }
  addEventListener(key, callback) { this.events[key] = callback; }
  removeEventListener() {}
}
test('without WebGL the supplied panel still shows current unknown data and literal server text', () => {
  const keys = ['document', 'window', 'getComputedStyle', 'ResizeObserver', 'MutationObserver', 'requestAnimationFrame', 'cancelAnimationFrame'];
  const previous = Object.fromEntries(keys.map(key => [key, globalThis[key]]));
  globalThis.document = { createElement: tag => new Element(tag), documentElement: new Element('html'),
    createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; } };
  globalThis.window = { matchMedia: () => ({ matches: true }) };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => '#888888' });
  globalThis.ResizeObserver = globalThis.MutationObserver = class { observe() {} disconnect() {} };
  globalThis.requestAnimationFrame = () => 1; globalThis.cancelAnimationFrame = () => {};
  try {
    const container = new Element('section'), attack = '<img src=x onerror=bad>';
    const view = mountTwin(container, { snapshot: { meta, summary: { promise_remaining_qty: null },
      graph: { data: { nodes: [], edges: [] } }, event_count: 1, events: [{ ledger_version: 7, summary: attack }],
      material_requirements: { bom_id: 'synthetic-bom', state: 'PARTIAL', row_count: 35, unknown_shortage_count: 35, items: [] } }, ontology: {} });
    assert.match(container.textContent, /3D를 그릴 수 없습니다/);
    assert.match(container.textContent, /계산 대상 미확인/); assert.ok(container.textContent.includes(attack));
    assert.ok(!container.textContent.includes('계산상 충족'));
    assert.equal(view.highlight(highlight({ ledgerVersion: 8 })), false);
    assert.equal(view.highlight(highlight({ nodeIds: [] })), true);
    view.destroy(); assert.equal(container.children.length, 0);
  } finally { for (const key of keys) { if (previous[key] === undefined) delete globalThis[key]; else globalThis[key] = previous[key]; } }
});

test('node name labels start hidden when the ledger graph is large', async () => {
  const { namesShownByDefault, NAMES_AUTO_LIMIT } = await import('../web/mks2/twin2.js');
  assert.equal(namesShownByDefault(NAMES_AUTO_LIMIT), true);
  assert.equal(namesShownByDefault(NAMES_AUTO_LIMIT + 1), false);
});

test('the ledger replay maps events to process zones and ends at the current arrival total', async () => {
  const { buildTwinReplay } = await import('../web/mks2/twin2.js');
  const doc = (id, kind, content) => ({ id, kind, status: 'POSTED', content: { kind, ...content } });
  const snapshot = { summary: { promise_qty: 100, arrived_qty: 72 },
    procurement: { documents: [doc('pr000001', 'PR', { quantity: 30 }), doc('iqc00002', 'IQC', { passed_qty: 30 }),
      doc('ar000003', 'ARRIVAL', { board_serials: ['B1', 'B2', 'B3'] }), doc('ch000004', 'COHORT', { stage: 'ARRIVAL', quantity: 5 })] },
    events: [
      { event_type: 'OPENING_BALANCE_IMPORTED', ledger_version: 1 },
      { event_type: 'BomRegistered', ledger_version: 6 },
      { event_type: 'InventoryReserved', ledger_version: 7, reservation: { demand_ref: 'ORD-2', item_id: 'IM-0016', quantity: 5, available_before: 20, available_after: 15 } },
      { event_type: 'ProcurementPosted', document_kind: 'PR', document_ref: { id: 'pr000001' }, ledger_version: 8 },
      { event_type: 'ProcurementPosted', document_kind: 'IQC', document_ref: { id: 'iqc00002' }, ledger_version: 9 },
      { event_type: 'ProcurementPosted', document_kind: 'ARRIVAL', document_ref: { id: 'ar000003' }, ledger_version: 10 },
      { event_type: 'ProcurementPosted', document_kind: 'COHORT', document_ref: { id: 'ch000004' }, ledger_version: 11 }] };
  const replay = buildTwinReplay(snapshot);
  assert.deepEqual(replay.steps.map(step => [step.version, step.zone]), [[6, 'line'], [7, 'mat'], [8, 'sup'], [9, 'mat'], [10, 'cust'], [11, 'cust']]);
  assert.equal(replay.steps[1].detail, 'IM-0016 5개 · 가용 20→15');
  assert.equal(replay.steps[3].detail, '합격 30개 · 가용 반영');
  assert.deepEqual(replay.steps.map(step => step.arrivedTotal), [64, 64, 64, 64, 67, 72]);
  assert.equal(replay.steps.at(-1).remaining, 28);
  assert.equal(buildTwinReplay({ events: [{ event_type: 'OPENING_BALANCE_IMPORTED', ledger_version: 1 }] }), null);
});

test('each replay step carries the zone values at that point, ending at the current summary', async () => {
  const { buildTwinReplay } = await import('../web/mks2/twin2.js');
  const doc = (id, kind, content) => ({ id, kind, status: 'POSTED', content: { kind, ...content } });
  const boards = n => Array.from({ length: n }, (_, i) => `B${i}`);
  const summary = { item_id: 'IM-0016', promise_qty: 100, on_hand_qty: 40, reserved_qty: 35, available_qty: 15,
    good_qty: 70, production_remaining_qty: 30, good_not_received_qty: 0, fg_received_qty: 70, warehouse_remaining_qty: 0,
    shipped_qty: 70, transit_qty: 0, arrived_qty: 70, promise_remaining_qty: 30 };
  const posted = (version, kind, id) => ({ event_type: 'ProcurementPosted', document_kind: kind, document_ref: { id }, ledger_version: version });
  const snapshot = { summary,
    procurement: { documents: [doc('rc', 'RECEIPT', { quantity: 30, resolved: { item_id: 'IM-0016' } }),
      doc('iq', 'IQC', { passed_qty: 30, resolved: { item_id: 'IM-0016' } }),
      doc('pd', 'PRODUCTION', { board_serials: boards(30), resolved: { item_id: 'IM-0016', consumed_qty: 30 } }),
      doc('fg', 'FG_RECEIPT', { board_serials: boards(30) }), doc('sh', 'SHIPMENT', { board_serials: boards(30) }),
      doc('ar', 'ARRIVAL', { board_serials: boards(30) }), doc('ch', 'COHORT', { stage: 'ARRIVAL', quantity: 8 })] },
    events: [{ event_type: 'BomRegistered', ledger_version: 6 },
      { event_type: 'InventoryReserved', ledger_version: 7, reservation: { item_id: 'IM-0016', quantity: 5, available_before: 20, available_after: 15 } },
      posted(8, 'RECEIPT', 'rc'), posted(9, 'IQC', 'iq'), posted(10, 'PRODUCTION', 'pd'), posted(11, 'FG_RECEIPT', 'fg'),
      posted(12, 'SHIPMENT', 'sh'), posted(13, 'ARRIVAL', 'ar'), posted(14, 'COHORT', 'ch')] };
  const { steps } = buildTwinReplay(snapshot);
  const pick = key => steps.map(step => step.state[key]);
  assert.deepEqual(pick('available_qty'), [20, 15, 15, 45, 15, 15, 15, 15, 15]);
  assert.deepEqual(pick('on_hand_qty'), [40, 40, 70, 70, 40, 40, 40, 40, 40]);
  assert.deepEqual(pick('reserved_qty'), [30, 35, 35, 35, 35, 35, 35, 35, 35]);
  assert.deepEqual(pick('good_qty'), [40, 40, 40, 40, 70, 70, 70, 70, 70]);
  assert.deepEqual(pick('warehouse_remaining_qty'), [0, 0, 0, 0, 0, 30, 0, 0, 0]);
  assert.deepEqual(pick('transit_qty'), [8, 8, 8, 8, 8, 8, 38, 8, 0]);
  assert.deepEqual(pick('arrived_qty'), [32, 32, 32, 32, 32, 32, 32, 62, 70]);
  assert.deepEqual(pick('promise_remaining_qty'), [68, 68, 68, 68, 68, 68, 68, 38, 30]);
  for (const [key, value] of Object.entries(steps.at(-1).state)) assert.equal(value, summary[key], key);
});

test('replay steps show the focus part filling up on the server planned-input rule', async () => {
  const { buildTwinReplay, plannedInputQty } = await import('../web/mks2/twin2.js');
  assert.equal(plannedInputQty(40, '0.94'), 43);
  assert.equal(plannedInputQty(10, '0.94'), 11);
  assert.equal(plannedInputQty(0, '0.94'), 0);
  assert.equal(plannedInputQty(40, '1.2'), null);
  const doc = (id, kind, content) => ({ id, kind, status: 'POSTED', content: { kind, ...content } });
  const posted = (version, kind, id) => ({ event_type: 'ProcurementPosted', document_kind: kind, document_ref: { id }, ledger_version: version });
  const snapshot = {
    summary: { item_id: 'IM-T1', promise_qty: 100, arrived_qty: 32, available_qty: 15, production_remaining_qty: 10, good_qty: 90 },
    material_requirements: { state: 'PARTIAL', basis: { plan_yield: '0.94' },
      items: [{ item_id: 'IM-T1', item_name: '테스트 부품', quantity_per_unit: '1', substitute_qty: '0', planned_required_qty: '11', available_qty: '15', planned_shortage_qty: '0' }] },
    procurement: { documents: [doc('iq', 'IQC', { passed_qty: 30, resolved: { item_id: 'IM-T1' } }),
      doc('pd', 'PRODUCTION', { board_serials: Array.from({ length: 30 }, (_, i) => `B${i}`), resolved: { item_id: 'IM-T1', consumed_qty: 30 } })] },
    events: [{ event_type: 'BomRegistered', ledger_version: 6 },
      { event_type: 'InventoryReserved', ledger_version: 7, reservation: { item_id: 'IM-T1', quantity: 5, available_before: 20, available_after: 15 } },
      posted(8, 'IQC', 'iq'), posted(9, 'PRODUCTION', 'pd')] };
  const { steps } = buildTwinReplay(snapshot);
  assert.deepEqual(steps.map(step => [step.material.required, step.material.available, step.material.shortage]),
    [[43, 20, 23], [43, 15, 28], [43, 45, 0], [11, 15, 0]]);
  assert.equal(steps[0].material.item_name, '테스트 부품');
  const last = snapshot.material_requirements.items[0];
  assert.equal(String(steps.at(-1).material.required), last.planned_required_qty);
  assert.equal(String(steps.at(-1).material.shortage), last.planned_shortage_qty);
  assert.equal(buildTwinReplay({ ...snapshot, material_requirements: { state: 'BLOCKED_BOM' } }).steps[0].material, null);
});
