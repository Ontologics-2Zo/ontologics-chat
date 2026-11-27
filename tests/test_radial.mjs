import test from 'node:test';
import assert from 'node:assert/strict';
import { hopRings, radialLayout } from '../web/mks2/radial.js';

const node = (id, type) => ({ node_id: id, type, display_id: id });
const edge = (id, source, target) => ({ edge_id: id, source_node_id: source, target_node_id: target });
const nodes = [node('round', 'DeliveryRound'), node('job', 'ProductionJob'), node('cfg', 'ProductConfiguration'),
  node('b1', 'BoardUnit'), node('b2', 'BoardUnit'), node('lot', 'PartLot'), node('lonely', 'Evidence')];
const edges = [edge('e1', 'job', 'round'), edge('e2', 'round', 'cfg'), edge('e3', 'b1', 'round'), edge('e4', 'b2', 'round'),
  edge('e5', 'lot', 'b1'), edge('e6', 'b1', 'b1')];

test('hop distance follows recorded relations in both directions and keeps unreachable objects', () => {
  const { hop, rings, unreachable } = hopRings(nodes, edges, 'round');
  assert.equal(hop.get('round'), 0);
  assert.deepEqual(['job', 'cfg', 'b1', 'b2'].map(id => hop.get(id)), [1, 1, 1, 1]);
  assert.equal(hop.get('lot'), 2);
  assert.deepEqual(unreachable, ['lonely']);
  assert.equal(rings.flat().length + unreachable.length, nodes.length);
});

test('layout places every object exactly once on its hop ring without overlap or invalid numbers', () => {
  const before = JSON.stringify({ nodes, edges });
  const { positions, radius, outer } = radialLayout(nodes, edges, 'round');
  assert.equal(positions.size, nodes.length);
  assert.deepEqual(positions.get('round'), { x: 0, y: 0, hop: 0 });
  for (const [id, p] of positions) {
    assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y), id);
    const r = Math.hypot(p.x, p.y);
    const expected = p.hop === null ? outer : radius[p.hop];
    assert.ok(Math.abs(r - expected) < 1e-6, id);
  }
  assert.ok(radius[2] > radius[1] && outer > radius[2]);
  const ring1 = ['job', 'cfg', 'b1', 'b2'].map(id => positions.get(id));
  for (let i = 0; i < ring1.length; i++) for (let j = i + 1; j < ring1.length; j++)
    assert.ok(Math.hypot(ring1[i].x - ring1[j].x, ring1[i].y - ring1[j].y) > 10);
  assert.equal(JSON.stringify({ nodes, edges }), before, 'the snapshot is never changed');
});

test('a child sits near the parent it connects to and the layout is deterministic', () => {
  const a = radialLayout(nodes, edges, 'round'), b = radialLayout([...nodes].reverse(), [...edges].reverse(), 'round');
  for (const id of nodes.map(n => n.node_id)) assert.deepEqual(a.positions.get(id), b.positions.get(id));
  const angle = id => Math.atan2(a.positions.get(id).y, a.positions.get(id).x);
  const gap = Math.abs(Math.atan2(Math.sin(angle('lot') - angle('b1')), Math.cos(angle('lot') - angle('b1'))));
  assert.ok(gap < 0.01, 'a single child aligns with its only parent');
});

test('a large ring grows its radius so objects keep their spacing', () => {
  const many = [node('c', 'DeliveryRound'), ...Array.from({ length: 300 }, (_, i) => node(`n${i}`, 'BoardUnit'))];
  const links = many.slice(1).map((n, i) => edge(`l${i}`, 'c', n.node_id));
  const { radius } = radialLayout(many, links, 'c', { spacing: 15 });
  assert.ok(2 * Math.PI * radius[1] / 300 >= 15 - 1e-9);
});

test('an unknown centre puts everything on the unreachable ring instead of failing', () => {
  const { positions, outer } = radialLayout(nodes, edges, 'missing');
  assert.equal(positions.size, nodes.length);
  assert.ok([...positions.values()].every(p => p.hop === null && Number.isFinite(p.x)));
  assert.ok(outer > 0);
});
