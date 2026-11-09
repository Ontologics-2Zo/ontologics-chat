import assert from 'node:assert/strict';
import test from 'node:test';
import { layoutGraph, routeEdges, fitGraphBounds, interpolateView } from '../web/mks2/graph-layout.js';

const node = (id, type) => ({ node_id: id, entity_ref: { entity_type: type }, label: id });
const edge = (id, source, target) => ({ edge_id: id, source_node_id: source, target_node_id: target, relation_type: id });
const many = [node('order', 'contract_line'), node('job', 'production_job'),
  ...Array.from({ length: 300 }, (_, i) => node(`row-${i}`, i % 2 ? 'bom_line' : 'SourceRow')),
  ...Array.from({ length: 180 }, (_, i) => node(`item-${i}`, 'item'))];

test('dense source grids preserve every node and input, with no coincident coordinates', () => {
  const before = JSON.stringify(many), result = layoutGraph(many);
  assert.equal(result.nodes.length, many.length);
  assert.deepEqual(result.nodes.map(n => n.node_id), many.map(n => n.node_id));
  assert.equal(new Set(result.nodes.map(n => `${n.x},${n.y}`)).size, many.length);
  assert.ok(result.nodes.every(n => Number.isFinite(n.x) && Number.isFinite(n.y)));
  assert.ok(Math.max(...result.nodes.map(n => n.y)) < 6000);
  assert.equal(JSON.stringify(many), before);
});

test('question core and all source rows occupy separate planes without removing context', () => {
  const ids = many.slice(0, 302).map(n => n.node_id), result = layoutGraph(many, { evidenceIds: ids });
  assert.equal(result.coreCount, 2); assert.equal(result.rowCount, 300);
  assert.equal(result.nodes.length, 482);
  assert.equal(result.layers.find(l => l.id === 'context').count, 180);
  assert.ok(result.layers.find(l => l.id === 'source-rows').y > result.focusBounds.y + result.focusBounds.height);
  assert.ok(result.evidenceBounds.height > result.focusBounds.height);
  assert.equal(new Set(result.nodes.map(n => `${n.x},${n.y}`)).size, 482);
});

test('coordinates are stable on input reordering and unknown types remain accessible', () => {
  const nodes = [...many, node('unknown', 'FutureDomainType')];
  const points = values => new Map(values.map(n => [n.node_id, [n.x, n.y]]));
  assert.deepEqual(points(layoutGraph(nodes).nodes), points(layoutGraph([...nodes].reverse()).nodes));
});

test('full schema ignores question focus and preserves its complete class set', () => {
  const result = layoutGraph(many, { schema: true, evidenceIds: ['order'] });
  assert.equal(result.focusing, false);
  assert.equal(result.layers.length, 1); assert.equal(result.nodes.length, many.length);
});

test('server BOM and procurement DTO aliases retain their business layers', () => {
  const types = ['bom', 'source_snapshot', 'approval', 'document_version', 'PurchaseRequest', 'PurchaseOrder', 'PurchaseOrderLine', 'PartsReceiptLine', 'InspectionResult', 'SupplierConfirmation'];
  const result = layoutGraph(types.map(type => node(type, type)));
  assert.ok(result.nodes.slice(0, 4).every(n => n.layoutLayer === 'design'));
  assert.ok(result.nodes.slice(4).every(n => n.layoutLayer === 'supply'));
});

test('source-row evidence uses separate descending routes without merging parallel edges', () => {
  const nodes = [{ ...node('a', 'bom'), x: 100, y: 50, layoutLayer: 'design' },
    { ...node('b', 'source_row'), x: 600, y: 900, layoutLayer: 'source-rows' }];
  const routes = routeEdges([edge('one', 'a', 'b'), edge('two', 'a', 'b'), edge('reverse', 'b', 'a')], nodes);
  assert.equal(routes.size, 3);
  assert.equal(new Set([...routes.values()].map(r => r.d)).size, 3);
  assert.ok([...routes.values()].every(r => r.d.includes(' C ') && !/NaN|Infinity/.test(r.d)));
});

test('parallel, reverse and self relations have distinct paths and unchanged identities', () => {
  const nodes = layoutGraph([node('a', 'item'), node('b', 'lot')]).nodes;
  const edges = [edge('one', 'a', 'b'), edge('two', 'a', 'b'), edge('reverse', 'b', 'a'), edge('loop', 'a', 'a'), edge('loop2', 'a', 'a')];
  const before = JSON.stringify(edges), routes = routeEdges(edges, nodes);
  assert.equal(routes.size, edges.length);
  assert.equal(new Set([...routes.values()].map(r => r.d)).size, edges.length);
  assert.ok([...routes.values()].every(r => Number.isFinite(r.x) && Number.isFinite(r.y) && !/NaN|Infinity/.test(r.d)));
  assert.equal(JSON.stringify(edges), before);
});

test('fit encloses the complete basis for portrait and wide viewports', () => {
  const bounds = { x: -80, y: -30, width: 1340, height: 980 };
  for (const ratio of [.5, 1, 2.5, NaN, 0]) {
    const view = fitGraphBounds(bounds, ratio);
    assert.ok(Object.values(view).every(Number.isFinite));
    assert.ok(view.x <= bounds.x && view.y <= bounds.y);
    assert.ok(view.x + view.width >= bounds.x + bounds.width);
    assert.ok(view.y + view.height >= bounds.y + bounds.height);
  }
});

test('negative animation timestamps cannot produce invalid viewBox dimensions', () => {
  const start = { x: 0, y: 0, width: 1400, height: 840 };
  const next = { x: -300, y: -500, width: 80000, height: 48000 };
  assert.deepEqual(interpolateView(start, next, -.2), start);
  assert.deepEqual(interpolateView(start, next, 2), next);
  for (const time of [-100, -.001, 0, .5, 1, 100, NaN]) {
    const view = interpolateView(start, next, time);
    assert.ok(view.width > 0 && view.height > 0 && Object.values(view).every(Number.isFinite));
  }
});
