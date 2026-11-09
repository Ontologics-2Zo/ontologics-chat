import assert from 'node:assert/strict';
import test from 'node:test';
import { layoutGraph, routeEdges, graphBounds, planEvidenceReplay, replayStepTiming, fitGraphBounds, interpolateView } from '../web/mks2/graph-layout.js';
import { mountGraph } from '../web/mks2/graph.js';

const node = (id, type) => ({ node_id: id, node_type: ['item', 'configuration'].includes(type) ? 'design' : 'doc', entity_ref: { entity_type: type }, label: id });
const edge = (id, source, target) => ({ edge_id: id, source_node_id: source, target_node_id: target, relation_type: id });
const rows = Array.from({ length: 90 }, (_, i) => [node(`source-${i}`, 'SourceRow'), node(`line-${i}`, 'BOMLine')]).flat();
const many = [node('customer', 'customer'), node('order', 'contract_line'), node('job', 'production_job'), node('bom', 'bom_snapshot'), ...rows,
  ...Array.from({ length: 60 }, (_, i) => node(`item-${i}`, 'item'))];
const links = [edge('customer-order', 'customer', 'order'), edge('order-job', 'order', 'job'), edge('job-bom', 'job', 'bom'),
  ...Array.from({ length: 90 }, (_, i) => [edge(`pair-${i}`, `line-${i}`, `source-${i}`), edge(`parent-${i}`, 'bom', `line-${i}`)]).flat()];
const points = nodes => new Map(nodes.map(n => [n.node_id, [n.x, n.y]]));

test('connected source cloud preserves every historical object and relation without shelves or coincident glyphs', () => {
  const before = JSON.stringify({ many, links }), result = layoutGraph(many, { edges: links });
  assert.deepEqual(result.nodes.map(n => n.node_id), many.map(n => n.node_id));
  assert.equal(new Set(result.nodes.map(n => `${n.x},${n.y}`)).size, many.length);
  assert.ok(result.nodes.every(n => Number.isFinite(n.x) && Number.isFinite(n.y)));
  assert.equal(result.layers.length, 0);
  assert.ok(result.nodes.filter(n => rows.some(row => row.node_id === n.node_id)).every(n => n.layoutCluster === 'bom'));
  assert.equal(routeEdges(links, result.nodes).size, links.length);
  for (let i = 0; i < result.nodes.length; i++) for (let j = i + 1; j < result.nodes.length; j++) {
    const a = result.nodes[i], b = result.nodes[j]; assert.ok(Math.hypot(a.x - b.x, a.y - b.y) > 44);
  }
  assert.equal(JSON.stringify({ many, links }), before);
});

test('question evidence and input ordering never move the fixed network', () => {
  const first = layoutGraph(many, { edges: links });
  assert.deepEqual(points(first.nodes), points(layoutGraph([...many].reverse(), { edges: [...links].reverse(), evidenceIds: ['order', 'source-2'] }).nodes));
  assert.deepEqual(points(first.nodes), points(layoutGraph(many, { edges: links, evidenceIds: rows.map(n => n.node_id) }).nodes));
});

test('actual source-to-BOM row links keep partners nearby without merging equal display labels', () => {
  const input = many.map(n => ({ ...n, display_id: 'repeated-source-label' }));
  const result = layoutGraph(input, { edges: links }), byId = new Map(result.nodes.map(n => [n.node_id, n]));
  for (let i = 0; i < 90; i++) {
    const a = byId.get(`source-${i}`), b = byId.get(`line-${i}`);
    assert.equal(a.rowGroup, b.rowGroup); assert.notEqual(a.node_id, b.node_id);
    assert.ok(Math.hypot(a.x - b.x, a.y - b.y) < 450);
  }
  assert.equal(result.nodes.length, input.length);
});

test('changing real adjacency changes network geometry and unknown types stay selectable', () => {
  const input = [node('a', 'FutureType'), node('b', 'FutureType'), node('c', 'FutureType')];
  const first = layoutGraph(input, { edges: [edge('link', 'a', 'b')] });
  const second = layoutGraph(input, { edges: [edge('link', 'a', 'c')] });
  assert.notDeepEqual(points(first.nodes), points(second.nodes));
  assert.deepEqual(first.nodes.map(n => n.node_id), ['a', 'b', 'c']);
});

test('full schema keeps every class and relation with no business-row grouping', () => {
  const result = layoutGraph(many, { edges: links, schema: true, evidenceIds: ['order'] });
  assert.equal(result.focusing, false); assert.equal(result.layers.length, 0);
  assert.equal(result.bomIds.length, 0); assert.equal(result.nodes.length, many.length);
  assert.equal(routeEdges(links, result.nodes).size, links.length);
});

test('replay keeps exact directed identities, deduplicates repeats, and batches all large row evidence once', () => {
  const request = { nodeIds: many.map(n => n.node_id), edgeIds: [...links.map(e => e.edge_id), links[0].edge_id], hops: links.map(e => [e.source_node_id, e.target_node_id]) };
  const before = JSON.stringify(request), plan = planEvidenceReplay(many, links, request);
  assert.deepEqual(new Set(plan.nodeIds), new Set(many.map(n => n.node_id)));
  assert.deepEqual(new Set(plan.edgeIds), new Set(links.map(e => e.edge_id)));
  assert.equal(plan.steps.filter(s => s.kind === 'relation').length, 3);
  const bulk = plan.steps.filter(s => s.kind === 'bom'); assert.equal(bulk.length, 1);
  assert.equal(bulk[0].edgeIds.length, 180);
  assert.ok(rows.every(n => bulk[0].nodeIds.includes(n.node_id)));
  assert.equal(plan.steps.at(-1).kind, 'bom'); assert.equal(JSON.stringify(request), before);
  assert.deepEqual(plan.steps.filter(s => s.kind === 'relation').map(s => s.edgeIds[0]), ['customer-order', 'order-job', 'job-bom']);
});

test('a parallel or reversed hop cannot fabricate an evidence edge', () => {
  const input = [node('a', 'item'), node('b', 'lot')], relations = [edge('one', 'a', 'b'), edge('two', 'a', 'b'), edge('reverse', 'b', 'a')];
  const plan = planEvidenceReplay(input, relations, { nodeIds: ['a', 'b'], hops: [['a', 'b']], edgeIds: ['missing'] });
  assert.deepEqual(plan.edgeIds, []);
  const exact = planEvidenceReplay(input, relations, { edgeIds: ['two'], hops: [['b', 'a']] });
  assert.deepEqual(new Set(exact.edgeIds), new Set(['two', 'reverse']));
});

test('large evidence remains complete while its readable playback has a finite duration', () => {
  const nodes = [...Array.from({ length: 90 }, (_, i) => node(`core-${i}`, 'item')), ...rows];
  const edges = [...Array.from({ length: 89 }, (_, i) => edge(`core-link-${i}`, `core-${i}`, `core-${i + 1}`)),
    ...Array.from({ length: 90 }, (_, i) => edge(`row-link-${i}`, `source-${i}`, `line-${i}`))];
  const plan = planEvidenceReplay(nodes, edges, { edgeIds: edges.map(e => e.edge_id) });
  assert.deepEqual(new Set(plan.steps.flatMap(s => s.edgeIds)), new Set(edges.map(e => e.edge_id)));
  assert.equal(plan.steps.flatMap(s => s.edgeIds).length, edges.length);
  assert.ok(plan.steps.filter(s => s.kind === 'relation').length <= 14);
  assert.equal(plan.steps.filter(s => s.kind === 'bom').length, 1);
  assert.ok(plan.steps.reduce((sum, step) => sum + replayStepTiming(step.kind).duration, 0) < 27000);
  for (const step of plan.steps.filter(s => s.kind === 'relation')) assert.ok(replayStepTiming(step.kind).duration >= 1200);
  const bulk = replayStepTiming('bom');
  assert.ok(bulk.drawDuration >= 1200 && bulk.duration > bulk.drawDelay + bulk.drawDuration);
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

class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.dataset = {}; this.events = {}; this.className = ''; this._text = ''; this.viewHistory = []; this.style = { setProperty(name, value) { this[name] = value; }, getPropertyValue(name) { return this[name]; } }; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'class') this.className = String(value); if (name === 'viewBox') this.viewHistory.push(String(value)); }
  addEventListener(name, callback) { this.events[name] = callback; }
  get classList() {
    return { add: value => { this.className = [...new Set([...this.className.split(' ').filter(Boolean), value])].join(' '); },
      remove: value => { this.className = this.className.split(' ').filter(item => item !== value).join(' '); },
      toggle: (value, active) => active ? this.classList.add(value) : this.classList.remove(value) };
  }
  getBoundingClientRect() { return { width: 1400, height: 840 }; }
  querySelectorAll(selector) { return descendants(this).filter(item => selector === 'button' && item.tagName === 'BUTTON'); }
  remove() { this.removed = true; if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this); }
  click() { this.events.click?.({ preventDefault() {}, stopPropagation() {}, target: this }); }
}
const descendants = root => [root, ...root.children.flatMap(descendants)];
const classed = (root, name) => descendants(root).filter(item => item.className.split(' ').includes(name));
function graphHarness({ reduced = false } = {}) {
  const names = ['document', 'window', 'ResizeObserver', 'requestAnimationFrame', 'cancelAnimationFrame', 'performance', 'setTimeout', 'clearTimeout'];
  const previous = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const frames = new Map(), timers = new Map(), listeners = new Map(); let serial = 0, now = 0, resizeCallback;
  const globals = {
    document: { createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), createDocumentFragment: () => new Element('#fragment'), createTextNode: text => { const el = new Element('#text'); el.textContent = text; return el; } },
    window: { matchMedia: () => ({ matches: reduced }), addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) },
    ResizeObserver: class { constructor(fn) { resizeCallback = fn; } observe() {} disconnect() { resizeCallback = null; } },
    requestAnimationFrame: fn => { frames.set(++serial, fn); return serial; }, cancelAnimationFrame: id => frames.delete(id),
    setTimeout: (fn, delay) => { timers.set(++serial, { fn, at: now + delay }); return serial; }, clearTimeout: id => timers.delete(id), performance: { now: () => now },
  };
  for (const [name, value] of Object.entries(globals)) Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  const container = new Element('div');
  const inputNodes = [node('a', 'customer'), node('b', 'production_job'), node('bom', 'bom_snapshot'), node('line', 'BOMLine'), node('source', 'SourceRow')];
  const inputEdges = [edge('ab', 'a', 'b'), edge('bbom', 'b', 'bom'), edge('line-parent', 'bom', 'line'), edge('row-pair', 'line', 'source')];
  const bundle = { graph: { meta: { ledger_version: 7, projection_version: 7 }, data: { nodes: inputNodes, edges: inputEdges } } };
  const view = mountGraph(container, { bundle });
  const svg = classed(container, 'mks-graph-svg')[0];
  const evidence = { nodeIds: inputNodes.map(n => n.node_id), edgeIds: inputEdges.map(e => e.edge_id), hops: inputEdges.map(e => [e.source_node_id, e.target_node_id]) };
  function frame(delta = 520) { now += delta; const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn(now)); }
  return { container, svg, view, frames, timers, listeners, evidence, frame,
    resize: () => resizeCallback?.(),
    async step(delta = 3000) { frame(delta); const pending = [...timers.entries()].filter(([, timer]) => timer.at <= now); pending.forEach(([id, timer]) => { timers.delete(id); timer.fn(); }); await Promise.resolve(); },
    restore() { view.destroy(); for (const name of names) { const descriptor = previous.get(name); if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; } },
  };
}

test('mounted replay refocuses each core step and one BOM batch while retaining every node and edge', async () => {
  const h = graphHarness();
  try {
    h.frame();
    const originalCoordinates = classed(h.container, 'mks-node').map(n => n.attributes.transform);
    const beforeViews = h.svg.viewHistory.length, playback = h.view.replay(h.evidence);
    for (let i = 0; i < 3; i++) {
      assert.equal(classed(h.container, 'mks-node').length, 5); assert.equal(classed(h.container, 'mks-edge').length, 4);
      if (h.svg.dataset.replayKind === 'relation') assert.equal(classed(h.container, 'mks-edge').filter(n => n.className.includes('mks-active-step')).length, 1);
      await h.step();
    }
    assert.deepEqual(await playback, { completed: true }); h.frame();
    assert.ok(new Set(h.svg.viewHistory.slice(beforeViews)).size >= 3);
    assert.deepEqual(classed(h.container, 'mks-node').map(n => n.attributes.transform), originalCoordinates);
    assert.equal(classed(h.container, 'mks-node').filter(n => n.className.includes('mks-hit')).length, 5);
    assert.equal(classed(h.container, 'mks-edge').filter(n => n.className.includes('mks-hit')).length, 4);
    assert.equal(h.timers.size, 0); assert.equal(classed(h.container, 'mks-active-step').length, 0);
    descendants(h.container).find(n => n.tagName === 'BUTTON' && n.dataset.nodeType === 'doc').click();
    assert.equal(classed(h.container, 'mks-node').length, 5); assert.equal(classed(h.container, 'mks-edge').length, 4);
    assert.ok(classed(h.container, 'mks-type-muted').length > 0);
    for (const label of ['핵심 흐름', '전체 근거', '전체 BOM', '전체 보기']) {
      descendants(h.container).find(n => n.tagName === 'BUTTON' && n.textContent === label).click(); h.frame();
      assert.equal(classed(h.container, 'mks-node').length, 5); assert.equal(classed(h.container, 'mks-edge').length, 4);
    }
  } finally { h.restore(); }
});

test('core playback lingers and BOM lines draw concurrently on one clock without replacing any base relation', async () => {
  const h = graphHarness();
  try {
    h.frame(); const playback = h.view.replay(h.evidence);
    await h.step(250);
    assert.equal(h.svg.dataset.replayKind, 'relation');
    assert.equal(h.svg.dataset.replayProgress, '0');
    await h.step(400);
    assert.equal(h.svg.dataset.replayKind, 'relation');
    assert.ok(Number(h.svg.dataset.replayProgress) > 0 && Number(h.svg.dataset.replayProgress) < 1);
    assert.equal(classed(h.container, 'mks-edge-progress').length, 1);
    await h.step(800); // First core relation gets the entire 1.45s.
    assert.equal(h.svg.dataset.replayKind, 'relation');
    await h.step(1450);
    assert.equal(h.svg.dataset.replayKind, 'bom');
    const overlays = classed(h.container, 'mks-edge-progress');
    assert.equal(overlays.length, 2);
    assert.equal(classed(h.container, 'mks-edge-line').length, 4);
    assert.equal(classed(h.container, 'mks-node').length, 5);
    assert.ok(overlays.every(path => path.attributes.pathLength === '1' && path.attributes['aria-hidden'] === 'true'));
    const basePaths = classed(h.container, 'mks-edge-line').map(path => path.attributes.d);
    assert.ok(overlays.every(path => basePaths.includes(path.attributes.d)));
    await h.step(1350);
    assert.equal(h.svg.dataset.replayState, 'playing');
    assert.equal(Number(h.svg.style.getPropertyValue('--mks-reveal-offset')), .5);
    assert.ok(h.frames.size <= 1, 'all BOM paths share one progress frame after camera settles');
    await h.step(1050);
    assert.deepEqual(await playback, { completed: true });
    assert.equal(h.svg.dataset.replayState, 'complete');
    assert.equal(classed(h.container, 'mks-edge-progress').length, 0);
    assert.equal(classed(h.container, 'mks-edge-line').length, 4);
    assert.equal(h.frames.size, 0); assert.equal(h.timers.size, 0);
  } finally { h.restore(); }
});

test('cancelling a partly drawn BOM batch clears shared motion and late frames cannot revive it', async () => {
  const h = graphHarness();
  try {
    h.frame(); const playback = h.view.replay(h.evidence);
    await h.step(1450); await h.step(1450); await h.step(1350);
    assert.equal(h.svg.dataset.replayKind, 'bom');
    const late = [...h.frames.values()];
    h.view.cancelReplay();
    assert.deepEqual(await playback, { completed: false });
    const before = { view: h.svg.attributes.viewBox, progress: h.svg.dataset.replayProgress };
    late.forEach(fn => fn(1000000));
    assert.deepEqual({ view: h.svg.attributes.viewBox, progress: h.svg.dataset.replayProgress }, before);
    assert.equal(classed(h.container, 'mks-edge-progress').length, 0);
    assert.equal(classed(h.container, 'mks-edge-line').length, 4);
    assert.equal(h.frames.size, 0); assert.equal(h.timers.size, 0);
  } finally { h.restore(); }
});

test('cancel, selection, wheel, reset and destroy stop camera and replay work including late frames', async () => {
  for (const stop of ['cancel', 'select', 'wheel', 'reset', 'destroy']) {
    const h = graphHarness();
    try {
      h.frame(); const playback = h.view.replay(h.evidence), late = [...h.frames.values()][0];
      if (stop === 'cancel') h.view.cancelReplay();
      if (stop === 'select') classed(h.container, 'mks-node')[0].click();
      if (stop === 'wheel') h.svg.events.wheel({ deltaY: 1, preventDefault() {} });
      if (stop === 'reset') h.view.reset();
      if (stop === 'destroy') h.view.destroy();
      assert.equal(h.timers.size, 0); assert.deepEqual(await playback, { completed: false });
      h.frame(); const viewBox = h.svg.attributes.viewBox; late(1000000); assert.equal(h.svg.attributes.viewBox, viewBox);
      if (stop === 'destroy') { assert.equal(h.frames.size, 0); assert.equal(h.listeners.size, 0); }
      else assert.ok(!h.svg.className.includes('mks-replaying'));
    } finally { h.restore(); }
  }
});

test('a new replay supersedes the old one and reduced motion reveals the same full evidence without timers', async () => {
  const h = graphHarness();
  try {
    const old = h.view.replay(h.evidence), latest = h.view.replay({ nodeIds: ['a', 'b'], edgeIds: ['ab'] });
    assert.deepEqual(await old, { completed: false }); await h.step(); assert.deepEqual(await latest, { completed: true });
    assert.deepEqual(classed(h.container, 'mks-edge').filter(n => n.className.includes('mks-hit')).map(n => n.attributes['data-edge-id']), ['ab']);
    h.resize(); h.frame(); assert.ok(!/NaN|Infinity/.test(h.svg.attributes.viewBox));
  } finally { h.restore(); }
  const reduced = graphHarness({ reduced: true });
  try {
    assert.deepEqual(await reduced.view.replay(reduced.evidence), { completed: true }); reduced.frame();
    assert.equal(reduced.timers.size, 0); assert.equal(classed(reduced.container, 'mks-hit').length, 9);
  } finally { reduced.restore(); }
});
