import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

// Execute the application itself with only its presentation imports
// stubbed. Every request goes to this in-process fake; no service/model is used.
const source = (await readFile(new URL('../web/mks2/app.js', import.meta.url), 'utf8'))
  .replace(/^import .*;\n/gm, '');
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attributes = {};
    this.events = {}; this.disabled = false; this.value = ''; this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { this.events[name] = callback; }
  removeEventListener(name, callback) { if (this.events[name] === callback) delete this.events[name]; }
  before() {}
  focus() {}
  scrollIntoView() {}
  // Deliberately bypass native disabled handling to verify the callback guard.
  dispatchClick() { return this.events.click?.(); }
  querySelector() { return this.slot ||= new Element('div'); }
}
const principal = { identity_revision: 'synthetic-session', company_id: 'synthetic-company' };
function snapshotAt(version = 7) {
  const meta = { ...principal, ledger_version: version, projection_version: version,
    projection_state: 'READY', snapshot_id: `synthetic-snapshot-${version}` };
  return { meta, graph: { meta: { ...meta }, data: { nodes: [], edges: [] } },
    summary: {}, suggested_questions: [] };
}
function stateAt(snapshot, { execution_id = 'synthetic-execution', revision = 2, status = 'WAITING' } = {}) {
  return { execution_id, revision, status,
    snapshot: { company_id: principal.company_id, context_revision: principal.identity_revision,
      read_snapshot_id: snapshot.meta.snapshot_id, ledger_version: snapshot.meta.ledger_version,
      projection_version: snapshot.meta.projection_version },
    interrupt: status === 'WAITING' ? { message: '합성 질문을 확인해 주세요.',
      allowed_capabilities: ['fulfillment_read', 'material_analysis'] } : null,
    result: status === 'COMPLETED' ? { answer: '합성 조회 결과', status: 'ANSWERED', meta: { ...snapshot.meta } } : null };
}
async function setup({ animated = false, reduced = false, agent = 'fake' } = {}) {
  const elements = new Map(), requests = [], twins = [], answers = [], bomOpens = [], bomOpenArguments = [], documentEvents = {};
  let data = snapshotAt(), handler = null, serial = 0;
  const get = id => { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); };
  const context = vm.createContext({
    document: { getElementById: get, createElement: tag => new Element(tag),
      createTextNode: text => { const value = new Element('#text'); value.textContent = text; return value; },
      documentElement: new Element('html'), addEventListener: (name, callback) => { documentEvents[name] = callback; },
      querySelector: () => null },
    window: { addEventListener() {}, frameElement: null }, location: { hash: '#dashboard' },
    AbortController, crypto: { randomUUID: () => `synthetic-key-${++serial}` },
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    requestAnimationFrame: callback => callback(),
    ...(animated ? { cancelAnimationFrame() {}, matchMedia: () => ({ matches: reduced }) } : {}),
    mountDashboard: () => ({ destroy() {}, select() {}, openBom(step, id, reference) { bomOpens.push(step); bomOpenArguments.push({ step, id, reference }); } }),
    mountGraph: () => ({ destroy() {}, cancelReplay() {}, resize() {}, reset() {}, focus() {}, replay() {} }),
    // DEC-H47 relationship graph (#graph); the existing project graph is mountGraph (#network).
    mountLaneGraph: () => ({ destroy() {}, cancelReplay() {}, resize() {}, reset() {}, focus() {}, replay() {}, setLag() {} }),
    // The digital twin (#twin2, DEC-H65).
    mountTwin2: (stage, options) => {
      const instance = { options, highlights: [], active: [], destroyed: false, resets: 0,
        destroy() { this.destroyed = true; }, setActive(value) { this.active.push(value); },
        highlight(value) { this.highlights.push(value); }, reset() { this.resets++; } };
      twins.push(instance); return instance;
    },
    updateAiSummary: () => true,
    renderQueryAnswer: (message, answer, options) => {
      let resolve;
      const finished = new Promise(done => { resolve = done; });
      const record = { answer, options, finishCalls: 0, disposeCalls: 0, playCalls: 0, resolve };
      record.presentation = { finished,
        finish() { record.finishCalls++; resolve(); },
        dispose() { record.disposeCalls++; resolve(); },
        play() { record.playCalls++; } };
      answers.push(record); message.textContent = answer.answer; return record.presentation;
    },
    fetch: async (path, options = {}) => {
      const request = { path, method: options.method || 'GET', body: options.body && JSON.parse(options.body) };
      requests.push(request);
      let result;
      if (path === '/health') result = { agent: { mode: agent } };
      else if (path === '/api/mks2/session') result = { authenticated: false, profiles: [] };
      else if (path === '/api/mks2/snapshot') result = data;
      else if (path === '/api/mks2/ontology') result = { meta: { ...data.meta }, graph: { data: { nodes: [], edges: [] } } };
      else result = await handler?.(request);
      assert.notEqual(result, undefined, `Unexpected fake request: ${path}`);
      const status = result.httpStatus || 200;
      return { ok: status < 400, status, json: async () => result.httpStatus ? { error: { code: result.code } } : result };
    },
  });
  vm.runInContext(source, context, { filename: 'app.js' });
  await new Promise(setImmediate);
  context.fixture = { principal, snapshot: data };
  vm.runInContext(`identity = fixture.principal; snapshot = fixture.snapshot;
    for (const name of SCREENS) createPanel(name);
    globalThis.app = { ask: question => runQuery('dashboard', question), refresh: refreshSnapshot,
      askTwin: question => runQuery('twin2', question), askGraph: question => runQuery('graph', question), panel: () => panels.dashboard,
      twinPanel: () => panels.twin2, graphPanel: () => panels.graph, generation: () => generation,
      route: name => { location.hash = '#' + name; route(); }, invalidate: invalidateView,
      load: loadSnapshot, setContext, setTwinContext, twinActive: updateTwinActivity,
      poll: pollChanges, panels: () => panels, current: () => snapshot, setLive: value => { liveEnabled = value; } };`, context);
  return { requests, app: context.app, twins, answers, bomOpens, bomOpenArguments, document: context.document, window: context.window, location: context.location,
    documentEvents, element: get, setHandler(value) { handler = value; },
    snapshot: () => data, setNextSnapshot(value) { data = value; } };
}
const choices = h => h.app.panel().waiting.controls;
const apiCalls = h => h.requests.filter(request => request.path.startsWith('/api/mks2/agent/'));
function normalFlow(h, resumeResult) {
  h.setHandler(request => request.path.endsWith('/resume') ? resumeResult(request)
    : request.path.endsWith('/run') ? stateAt(h.snapshot())
      : stateAt(h.snapshot(), { status: 'READY', revision: 1 }));
}

test('completed clarification disables its historical choices and the next question starts a new execution', async () => {
  const h = await setup(); normalFlow(h, () => stateAt(h.snapshot(), { status: 'COMPLETED', revision: 4 }));
  await h.app.ask('합성 질문');
  const oldChoices = [...choices(h)], historical = h.app.panel().messages.children.at(-1), originalText = historical.textContent;
  oldChoices[0].dispatchClick();
  await h.app.ask('합성 보완 질문');
  assert.equal(h.app.panel().pending, null); assert.ok(oldChoices.every(control => control.disabled));
  assert.equal(historical.textContent, originalText);
  oldChoices[1].dispatchClick(); assert.equal(h.app.panel().pending, null);
  await h.app.ask('다음 합성 질문');
  assert.equal(apiCalls(h).filter(request => request.path.endsWith('/resume')).length, 1);
  assert.equal(apiCalls(h).filter(request => request.path.endsWith('/executions')).length, 2);
});

test('latest snapshot refresh clears a selected wait while keeping the old answer text', async () => {
  const h = await setup(); normalFlow(h, () => { throw Error('Old wait must not resume'); });
  await h.app.ask('합성 질문');
  const old = choices(h)[0], historical = h.app.panel().messages.children.at(-1), originalText = historical.textContent;
  old.dispatchClick(); h.setNextSnapshot(snapshotAt(8)); await h.app.refresh();
  assert.equal(h.app.panel().pending, null); assert.equal(h.app.panel().waiting, null); assert.equal(old.disabled, true);
  assert.equal(historical.textContent, originalText); old.dispatchClick(); assert.equal(h.app.panel().pending, null);
  await h.app.ask('최신 합성 질문');
  assert.equal(apiCalls(h).at(-2).body.ledger_version, 8);
  assert.equal(apiCalls(h).filter(request => request.path.endsWith('/resume')).length, 0);
});

for (const [status, code] of [[409, 'AGENT_REVISION_CHANGED'], [409, 'AGENT_SNAPSHOT_CHANGED'], [408, 'AGENT_DEADLINE_EXCEEDED']]) {
  test(`${code} releases the stale selection so another question can start`, async () => {
    const h = await setup(); normalFlow(h, () => ({ httpStatus: status, code }));
    await h.app.ask('합성 질문'); const old = choices(h)[0]; old.dispatchClick();
    await h.app.ask('합성 보완 질문');
    assert.equal(h.app.panel().pending, null); assert.equal(old.disabled, true);
    old.dispatchClick(); await h.app.ask('새 합성 질문');
    assert.equal(apiCalls(h).filter(request => request.path.endsWith('/executions')).length, 2);
    assert.equal(apiCalls(h).filter(request => request.path.endsWith('/resume')).length, 1);
  });
}

test('a busy execution retains its current exact revision for an explicit retry', async () => {
  const h = await setup(); let attempts = 0;
  normalFlow(h, () => ++attempts === 1 ? { httpStatus: 409, code: 'AGENT_EXECUTION_BUSY' }
    : stateAt(h.snapshot(), { status: 'COMPLETED', revision: 4 }));
  await h.app.ask('합성 질문'); const old = choices(h)[0]; old.dispatchClick();
  await h.app.ask('합성 보완 질문');
  assert.equal(h.app.panel().pending.revision, 2); assert.equal(old.disabled, false);
  await h.app.ask('명시적 재시도');
  assert.deepEqual(apiCalls(h).filter(request => request.path.endsWith('/resume')).map(request => request.body.expected_revision), [2, 2]);
  assert.equal(h.app.panel().pending, null); assert.equal(old.disabled, true);
});

test('a second clarification uses only its newest revision and ignores older buttons', async () => {
  const h = await setup(); let attempts = 0;
  normalFlow(h, () => stateAt(h.snapshot(), { revision: ++attempts === 1 ? 4 : 6, status: attempts === 1 ? 'WAITING' : 'COMPLETED' }));
  await h.app.ask('합성 질문'); const previous = choices(h)[0]; previous.dispatchClick();
  await h.app.ask('첫 보완'); const current = choices(h)[1];
  previous.dispatchClick(); assert.equal(h.app.panel().pending, null); assert.equal(previous.disabled, true);
  current.dispatchClick(); await h.app.ask('두 번째 보완');
  assert.deepEqual(apiCalls(h).filter(request => request.path.endsWith('/resume')).map(request => request.body.expected_revision), [2, 4]);
});

test('a new question supersedes an unselected clarification without reusing the old execution', async () => {
  const h = await setup(); normalFlow(h, () => { throw Error('Unselected wait must not resume'); });
  await h.app.ask('합성 질문'); const old = choices(h)[0];
  await h.app.ask('별도 합성 질문'); old.dispatchClick();
  assert.equal(old.disabled, true); assert.equal(h.app.panel().pending, null);
  assert.equal(apiCalls(h).filter(request => request.path.endsWith('/executions')).length, 2);
});

test('a response pinned to another snapshot cannot enable clarification controls', async () => {
  const h = await setup();
  h.setHandler(request => stateAt(snapshotAt(6), { status: request.path.endsWith('/run') ? 'WAITING' : 'READY' }));
  await h.app.ask('합성 질문');
  assert.equal(h.app.panel().waiting, null); assert.equal(h.app.panel().pending, null);
  assert.match(h.app.panel().messages.textContent, /조회 기준이 바뀌었거나/);
});

function twinAnswer(h, meta = h.snapshot().meta) {
  return { answer: '합성 트윈 조회 결과', status: 'PARTIAL', meta: { ...meta },
    evidence_node_ids: ['synthetic-node'], evidence_edge_ids: ['synthetic-edge'], graph_path: [] };
}

test('digital twin mounts the same authorized snapshot and preserves separate drafts and context', async () => {
  const h = await setup(); await h.app.load();
  const twin = h.twins.at(-1), original = JSON.stringify(h.snapshot());
  assert.equal(twin.options.snapshot, h.snapshot());
  assert.deepEqual(twin.options.ontology.meta, h.snapshot().meta);
  h.app.graphPanel().input.value = '합성 그래프 초안'; h.app.panel().input.value = '합성 에이전트 초안';
  const count = h.requests.length;
  twin.options.onAsk('합성 트윈 초안'); twin.options.onSelect('synthetic-twin-context');
  h.app.setContext('synthetic-graph-context');
  h.app.route('twin2'); h.app.route('graph'); h.app.route('twin2');
  assert.equal(h.app.twinPanel().input.value, '합성 트윈 초안');
  assert.equal(h.app.graphPanel().input.value, '합성 그래프 초안');
  assert.equal(h.app.panel().input.value, '합성 에이전트 초안');
  assert.match(h.app.twinPanel().context.textContent, /synthetic-twin-context/);
  assert.match(h.app.graphPanel().context.textContent, /synthetic-graph-context/);
  assert.equal(h.requests.length, count); assert.equal(JSON.stringify(h.snapshot()), original);
});

test('digital twin queries the read API and forwards exact answer evidence and identity to highlight', async () => {
  const h = await setup(); await h.app.load();
  h.app.setTwinContext('synthetic-twin-context'); h.app.setContext('synthetic-graph-context');
  h.setHandler(request => { assert.equal(request.path, '/api/mks2/query'); return twinAnswer(h); });
  await h.app.askTwin('합성 원장 질문');
  const request = h.requests.at(-1), highlight = h.twins.at(-1).highlights[0];
  assert.equal(request.body.context_entity_id, 'synthetic-twin-context');
  assert.equal(request.body.ledger_version, h.snapshot().meta.ledger_version);
  assert.equal(apiCalls(h).length, 0);
  assert.deepEqual(JSON.parse(JSON.stringify(highlight)), {
    nodeIds: ['synthetic-node'], edgeIds: ['synthetic-edge'], ledgerVersion: 7, projectionVersion: 7,
    snapshotId: 'synthetic-snapshot-7', companyId: principal.company_id, identityRevision: principal.identity_revision,
  });
  assert.match(h.app.twinPanel().messages.textContent, /합성 트윈 조회 결과/);
  assert.equal(h.answers[0].options.evidenceNodes, h.snapshot().graph.data.nodes);
});

test('same ledger number with a different snapshot cannot highlight the digital twin', async () => {
  const h = await setup(); await h.app.load();
  h.setHandler(() => twinAnswer(h, { ...h.snapshot().meta, snapshot_id: 'synthetic-other-snapshot' }));
  await h.app.askTwin('합성 질문');
  assert.equal(h.twins.at(-1).highlights.length, 0);
  assert.match(h.app.twinPanel().messages.textContent, /조회 기준이 바뀌었거나/);
});

test('refresh remounts the twin but retains historical answers and blocks their old evidence actions', async () => {
  const h = await setup(); await h.app.load(); h.setHandler(() => twinAnswer(h));
  await h.app.askTwin('합성 이전 질문');
  const oldTwin = h.twins.at(-1), panel = h.app.twinPanel(), original = panel.messages.textContent;
  h.setNextSnapshot(snapshotAt(8)); await h.app.refresh();
  assert.equal(oldTwin.destroyed, true); assert.equal(h.app.twinPanel(), panel);
  assert.equal(panel.messages.textContent, original); assert.equal(h.twins.at(-1).options.snapshot.meta.ledger_version, 8);
  h.answers[0].options.onReplay(); h.answers[0].options.onEvidence('synthetic-node');
  assert.match(h.element('notice').textContent, /과거 원장 버전/);
  assert.equal(h.twins.at(-1).highlights.length, 0);
});

test('session invalidation destroys the twin and clears its visible answer surface', async () => {
  const h = await setup(); await h.app.load(); const oldTwin = h.twins.at(-1);
  h.setHandler(() => ({ httpStatus: 409, code: 'IDENTITY_CHANGED' }));
  await h.app.askTwin('합성 권한 질문');
  assert.equal(oldTwin.destroyed, true); assert.equal(h.element('twin2-view').hidden, true);
  assert.equal(h.element('twin-panel').textContent, '');
});

test('digital twin pauses outside its tab, hidden document, or hidden shell frame', async () => {
  const h = await setup(); await h.app.load(); const twin = h.twins.at(-1);
  h.app.route('twin2'); assert.equal(twin.active.at(-1), true);
  h.app.route('dashboard'); assert.equal(twin.active.at(-1), false);
  h.app.route('twin2'); h.document.hidden = true; h.documentEvents.visibilitychange();
  assert.equal(twin.active.at(-1), false);
  h.document.hidden = false; h.documentEvents.visibilitychange(); assert.equal(twin.active.at(-1), true);
  h.window.frameElement = { getBoundingClientRect: () => ({ height: 0 }) }; h.app.twinActive();
  assert.equal(twin.active.at(-1), false);
});

test('screen reset clears twin presentation state without requests or ledger changes', async () => {
  const h = await setup(); await h.app.load(); const twin = h.twins.at(-1);
  h.app.twinPanel().input.value = '합성 초안';
  const before = JSON.stringify(h.snapshot()), count = h.requests.length;
  h.element('reset').dispatchClick();
  assert.equal(twin.resets, 1); assert.equal(h.app.twinPanel().input.value, '');
  assert.equal(h.requests.length, count); assert.equal(JSON.stringify(h.snapshot()), before);
});

test('the current answer BOM action opens source reception without changing tabs or issuing a command', async () => {
  const h = await setup(); await h.app.load(); h.app.route('twin2');
  h.setHandler(() => ({ ...twinAnswer(h), calculation: {
    material_requirements: { state: 'BLOCKED_BOM', bom_id: null } } }));
  await h.app.askTwin('합성 미등록 질문');
  assert.equal(h.bomOpens.length, 0);
  const count = h.requests.length, before = JSON.stringify(h.snapshot());
  h.answers[0].options.onOpenBom();
  assert.deepEqual(h.bomOpens, [0]); assert.equal(h.requests.length, count);
  assert.equal(h.location.hash, '#twin2'); assert.equal(JSON.stringify(h.snapshot()), before);
});

test('an old answer cannot reopen BOM registration after refresh or identity invalidation', async () => {
  const h = await setup(); await h.app.load(); h.setHandler(() => twinAnswer(h));
  await h.app.askTwin('합성 이전 질문');
  h.setNextSnapshot(snapshotAt(8)); await h.app.refresh();
  const count = h.requests.length;
  h.answers[0].options.onOpenBom();
  assert.deepEqual(h.bomOpens, []); assert.equal(h.requests.length, count);
  assert.match(h.element('notice').textContent, /과거 원장 버전/);
  h.app.invalidate(); h.answers[0].options.onOpenBom();
  assert.deepEqual(h.bomOpens, []); assert.equal(h.requests.length, count);
});

test('graph playback and request completion do not prematurely finish the active text presentation', async () => {
  const h = await setup(); await h.app.load(); h.app.route('twin2');
  h.setHandler(() => twinAnswer(h)); await h.app.askTwin('합성 표시 질문');
  const shown = h.answers[0];
  assert.equal(shown.options.animate, true); assert.equal(shown.playCalls, 1);
  assert.equal(shown.finishCalls, 0); assert.equal(shown.disposeCalls, 0);
  assert.equal(h.app.twinPanel().presentation, shown.presentation);
  assert.equal(h.app.twinPanel().submit.disabled, false);
  assert.equal(apiCalls(h).length, 0);
});

test('workflow uses server cards without automatic graph replay and the manual action still navigates', async () => {
  const h = await setup({ animated: true }); await h.app.load();
  h.setHandler(request => {
    const state = stateAt(h.snapshot(), { status: request.path.endsWith('/run') ? 'COMPLETED' : 'READY' });
    if (state.result) Object.assign(state.result, { evidence_node_ids: ['synthetic-node'] });
    return state;
  });
  await h.app.ask('합성 업무 질문'); const shown = h.answers[0];
  assert.equal(shown.options.mode, 'workflow'); assert.equal(shown.options.beforeBody, undefined);
  assert.equal(shown.playCalls, 0);
  const count = h.requests.length; shown.options.onReplay();
  assert.equal(shown.playCalls, 1); assert.equal(h.location.hash, '#graph'); assert.equal(h.requests.length, count);
});

test('animated graph hands one replay hook to the renderer while twin and reduced motion never wait for it', async () => {
  for (const [surface, reduced] of [['graph', false], ['graph', true], ['twin2', false]]) {
    // Graph presentation on the read API, as when the model is off.
    const h = await setup({ animated: true, reduced, agent: 'disabled' }); await h.app.load(); h.app.route(surface);
    h.setHandler(() => twinAnswer(h)); await (surface === 'graph' ? h.app.askGraph('합성 그래프 질문') : h.app.askTwin('합성 트윈 질문'));
    const shown = h.answers[0]; assert.equal(shown.options.mode, 'graph');
    if (surface === 'graph' && !reduced) {
      assert.equal(typeof shown.options.beforeBody, 'function'); assert.equal(shown.playCalls, 0);
      const count = h.requests.length; await shown.options.beforeBody();
      assert.equal(shown.playCalls, 1); assert.equal(h.requests.length, count);
    } else {
      assert.equal(shown.options.beforeBody, undefined); assert.equal(shown.playCalls, 1);
    }
  }
});

test('progress follows the bottom only until the reader scrolls up and releases its own scroll listener', async () => {
  const h = await setup(); await h.app.load(); h.app.route('twin2'); h.setHandler(() => twinAnswer(h));
  const messages = h.app.twinPanel().messages;
  messages.scrollHeight = 1000; messages.clientHeight = 400; messages.scrollTop = 0;
  await h.app.askTwin('합성 스크롤 질문'); const shown = h.answers[0];
  assert.equal(messages.scrollTop, 1000); assert.equal(typeof messages.events.scroll, 'function');
  messages.scrollHeight = 1200; shown.options.onProgress(); assert.equal(messages.scrollTop, 1200);
  messages.scrollTop = 100; messages.events.scroll(); messages.scrollHeight = 1400;
  shown.options.onProgress(); assert.equal(messages.scrollTop, 100);
  messages.scrollTop = 1000; messages.events.scroll(); shown.options.onProgress(); assert.equal(messages.scrollTop, 1400);
  shown.presentation.finish(); await Promise.resolve(); assert.equal(messages.events.scroll, undefined);
  await h.app.askTwin('다음 합성 스크롤 질문'); const latest = messages.events.scroll;
  shown.resolve(); await Promise.resolve(); assert.equal(messages.events.scroll, latest);
  h.app.invalidate(); await Promise.resolve(); assert.equal(messages.events.scroll, undefined);
});

test('a new question finishes the old answer and late completion cannot release the newer presentation', async () => {
  const h = await setup(); await h.app.load(); h.app.route('twin2'); h.setHandler(() => twinAnswer(h));
  await h.app.askTwin('합성 이전 질문');
  const older = h.answers[0], previousMessage = h.app.twinPanel().messages.children.at(-1), text = previousMessage.textContent;
  await h.app.askTwin('합성 새 질문'); const newer = h.answers[1];
  older.resolve(); await Promise.resolve();
  assert.equal(older.finishCalls, 1); assert.equal(older.disposeCalls, 0);
  assert.equal(previousMessage.textContent, text);
  assert.equal(h.app.twinPanel().presentation, newer.presentation); assert.equal(newer.finishCalls, 0);
  assert.equal(h.requests.filter(r => r.path === '/api/mks2/query').length, 2);
});

test('tab and document visibility settle text without additional queries or changing the original answer', async () => {
  const h = await setup(); await h.app.load(); h.app.route('twin2'); h.setHandler(() => twinAnswer(h));
  await h.app.askTwin('합성 탭 질문'); const before = h.app.twinPanel().messages.textContent;
  let count = h.requests.length; h.app.route('graph');
  assert.equal(h.answers[0].finishCalls, 1); assert.equal(h.app.twinPanel().messages.textContent, before);
  assert.equal(h.requests.length, count);
  h.app.route('twin2'); await h.app.askTwin('합성 숨김 질문'); count = h.requests.length;
  h.document.hidden = true; h.documentEvents.visibilitychange();
  assert.equal(h.answers[1].finishCalls, 1); assert.equal(h.requests.length, count);
});

test('refresh finishes the exact old answer before changing the snapshot and keeps its version guards', async () => {
  const h = await setup(); await h.app.load(); h.app.route('twin2'); h.setHandler(() => twinAnswer(h));
  await h.app.askTwin('합성 과거 질문'); const before = h.app.twinPanel().messages.textContent;
  h.setNextSnapshot(snapshotAt(8)); await h.app.refresh();
  assert.equal(h.answers[0].finishCalls, 1); assert.equal(h.answers[0].disposeCalls, 0);
  assert.equal(h.app.twinPanel().messages.textContent, before);
  assert.equal(h.answers[0].answer.meta.ledger_version, 7);
  h.answers[0].options.onOpenBom(); assert.deepEqual(h.bomOpens, []);
});

test('reset and identity invalidation dispose presentations before removing their visible messages', async () => {
  const h = await setup(); await h.app.load(); h.app.route('twin2'); h.setHandler(() => twinAnswer(h));
  await h.app.askTwin('합성 초기화 질문'); let count = h.requests.length;
  h.element('reset').dispatchClick();
  assert.equal(h.answers[0].disposeCalls, 1); assert.equal(h.answers[0].finishCalls, 0);
  assert.equal(h.app.twinPanel().presentation, null); assert.equal(h.requests.length, count);
  await h.app.askTwin('합성 무효화 질문'); count = h.requests.length;
  h.app.invalidate(); h.answers[1].resolve(); await Promise.resolve();
  assert.equal(h.answers[1].disposeCalls, 1); assert.equal(h.answers[1].finishCalls, 0);
  assert.equal(h.element('twin-panel').textContent, ''); assert.equal(h.requests.length, count);
});

test('a response arriving on a hidden tab is complete without animation or mixing parallel panel answers', async () => {
  const h = await setup({ agent: 'disabled' }); await h.app.load(); h.app.route('twin2');
  let replyTwin, replyGraph;
  h.setHandler(request => new Promise(resolve => {
    if (request.body.question === '합성 트윈 요청') replyTwin = resolve; else replyGraph = resolve;
  }));
  const twinRequest = h.app.askTwin('합성 트윈 요청'); h.app.route('graph');
  const graphRequest = h.app.askGraph('합성 그래프 요청');
  replyGraph({ ...twinAnswer(h), answer: '합성 그래프 결과' }); await graphRequest;
  replyTwin({ ...twinAnswer(h), answer: '합성 트윈 결과' }); await twinRequest;
  assert.equal(h.answers[0].options.animate, true); assert.equal(h.answers[1].options.animate, false);
  assert.match(h.app.graphPanel().messages.textContent, /합성 그래프 결과/);
  assert.doesNotMatch(h.app.graphPanel().messages.textContent, /합성 트윈 결과/);
  assert.match(h.app.twinPanel().messages.textContent, /합성 트윈 결과/);
  assert.equal(h.app.graphPanel().presentation, h.answers[0].presentation);
});

test('the BOM entry finishes only its answer presentation and opens the existing drawer without a command', async () => {
  const h = await setup(); await h.app.load(); h.app.route('twin2'); h.setHandler(() => twinAnswer(h));
  await h.app.askTwin('합성 원본 질문'); const count = h.requests.length;
  h.answers[0].options.onOpenBom();
  assert.equal(h.answers[0].finishCalls, 1); assert.equal(h.answers[0].disposeCalls, 0);
  assert.deepEqual(h.bomOpens, [0]); assert.equal(h.requests.length, count);
});

test('BOM application review opens the exact current document and old or changed references cannot reopen it', async () => {
  const h = await setup(); h.snapshot().summary.configuration_id = 'synthetic-config'; await h.app.load(); h.app.route('twin2');
  const ref = { document_id: '00000000-0000-4000-8000-000000000123', version: 3, payload_hash: 'b'.repeat(64) };
  const answer = { ...twinAnswer(h), prerequisite: { configuration_id: 'synthetic-config', bom: { ...ref } },
    calculation: { material_requirements: { bom_id: ref.document_id, bom_version: ref.version } } };
  h.setHandler(() => answer); await h.app.askTwin('합성 적용 검토 질문');
  const count = h.requests.length, shown = h.answers[0];
  for (const changed of [{ ...ref, version: 4 }, { ...ref, payload_hash: 'a'.repeat(64) }, { ...ref, document_id: 'different' }]) shown.options.onReviewBom(changed);
  assert.equal(h.bomOpens.length, 0);
  shown.options.onReviewBom(ref);
  assert.deepEqual(JSON.parse(JSON.stringify(h.bomOpenArguments)), [{ step: 4, id: ref.document_id, reference: ref }]);
  assert.equal(shown.finishCalls, 1); assert.equal(shown.disposeCalls, 0);
  assert.equal(h.requests.length, count); assert.equal(h.location.hash, '#twin2');
  answer.prerequisite.configuration_id = 'another-config'; shown.options.onReviewBom(ref);
  assert.equal(h.bomOpens.length, 1);
  answer.prerequisite.configuration_id = 'synthetic-config';
  h.setNextSnapshot(snapshotAt(8)); await h.app.refresh(); shown.options.onReviewBom(ref);
  assert.equal(h.bomOpens.length, 1); assert.match(h.element('notice').textContent, /과거 원장 버전/);
  h.app.invalidate(); shown.options.onReviewBom(ref); assert.equal(h.bomOpens.length, 1);
});


// DEC-H47: a committed reservation refreshes quantities from a new server
// snapshot and marks only conversations that depend on the reserved item.
function reservationChanges(state = 'READY') {
  return { current_ledger_version: 8, snapshot_ledger_version: state === 'READY' ? 8 : 7, projection_state: state,
    identity_revision: principal.identity_revision, company_id: principal.company_id,
    events: [{ event_id: 'synthetic-event-8', ledger_version: 8, event_type: 'InventoryReserved', document_kind: 'RESERVATION',
      summary: '재고 예약 반영 · 다른 수요 합성-주문 · item-a 5개', recorded_at: '2026-01-01T00:00:00+00:00',
      reservation: { reservation_id: 'synthetic-reservation', item_id: 'item-a' } }] };
}
function reservedSnapshot() {
  const next = snapshotAt(8);
  next.material_requirements = { items: [{ item_id: 'item-a' }] };
  next.reservation_impacts = [{ reservation_id: 'synthetic-reservation', item_id: 'item-a', unit: 'EA', demand_ref: '합성-주문',
    quantity: 5, available_before: '20', available_after: '15', planned_shortage_before: '23', planned_shortage_after: '28',
    ledger_version: 8, entity_id: 'synthetic-reservation-node' }];
  return next;
}

test('a relevant committed reservation auto-refreshes and adds one status line without model calls', async () => {
  const h = await setup();
  h.snapshot().material_requirements = { items: [{ item_id: 'item-a' }] };
  h.app.panels().dashboard.topics.add('item-a');
  h.setHandler(request => request.path.startsWith('/api/mks2/events') ? reservationChanges('READY') : undefined);
  h.setNextSnapshot(reservedSnapshot());
  await h.app.poll();
  assert.equal(h.app.current().meta.ledger_version, 8);
  assert.match(h.app.panels().dashboard.status.textContent, /가용 20→15 · 계획 부족 23→28개 · 기존 확보안 재검토 필요/);
  assert.equal(h.app.panels().graph.status.textContent, '');
  assert.equal(h.requests.filter(request => request.path.startsWith('/api/mks2/agent/') || request.path === '/api/mks2/query').length, 0);
  await h.app.poll();
  assert.equal(h.app.panels().dashboard.status.children.length, 1);
});

test('a grown shortage puts the same question back in the empty input and offers it again', async () => {
  for (const typed of ['', '입력 중인 질문']) {
    const h = await setup();
    h.snapshot().material_requirements = { items: [{ item_id: 'item-a' }] };
    const panel = h.app.panels().dashboard;
    panel.topics.add('item-a'); panel.lastQuestion = '남은 68대, 납기 안에 고객에게 도착할 수 있어?'; panel.input.value = typed;
    h.setHandler(request => request.path.startsWith('/api/mks2/events') ? reservationChanges('READY') : undefined);
    h.setNextSnapshot(reservedSnapshot());
    await h.app.poll();
    assert.equal(panel.input.value, typed || panel.lastQuestion);
    assert.match(panel.status.textContent, /같은 질문 다시 하기/);
  }
});

test('the same-question button sends the question at once', async () => {
  const h = await setup();
  h.snapshot().material_requirements = { items: [{ item_id: 'item-a' }] };
  const panel = h.app.panels().dashboard;
  panel.topics.add('item-a'); panel.lastQuestion = '합성 재질문';
  h.setHandler(request => request.path.startsWith('/api/mks2/events') ? reservationChanges('READY')
    : request.path.endsWith('/run') ? stateAt(h.snapshot(), { status: 'COMPLETED', revision: 3 })
      : request.path === '/api/mks2/agent/executions' ? stateAt(h.snapshot(), { status: 'READY', revision: 1 }) : undefined);
  h.setNextSnapshot(reservedSnapshot());
  await h.app.poll();
  const again = (function find(node) { return node.textContent === '같은 질문 다시 하기' && node.events?.click ? node : node.children?.map(find).find(Boolean); })(panel.status);
  assert.ok(again);
  again.events.click();
  await new Promise(setImmediate);
  const created = h.requests.filter(r => r.path === '/api/mks2/agent/executions');
  assert.equal(created.length, 1);
  assert.equal(created[0].body.question, '합성 재질문');
});

test('a reservation whose graph projection is pending keeps the old snapshot and says the graph is updating', async () => {
  const h = await setup();
  h.snapshot().material_requirements = { items: [{ item_id: 'item-a' }] };
  h.app.panels().dashboard.topics.add('item-a');
  h.setHandler(request => request.path.startsWith('/api/mks2/events') ? reservationChanges('PENDING') : undefined);
  h.setNextSnapshot(reservedSnapshot());
  await h.app.poll();
  assert.equal(h.app.current().meta.ledger_version, 7);
  assert.match(h.app.panels().dashboard.status.textContent, /수량·그래프 갱신 중/);
  assert.equal(h.requests.filter(request => request.path === '/api/mks2/snapshot').length, 0);
});

test('a posted customer arrival marks only conversations about that round, quoting server quantities', async () => {
  const h = await setup();
  h.snapshot().summary = { delivery_round_id: 'R-1', arrived_qty: 32, promise_remaining_qty: 68 };
  h.app.panels().dashboard.topics.add('round:R-1');
  h.setHandler(request => request.path.startsWith('/api/mks2/events') ? {
    current_ledger_version: 8, snapshot_ledger_version: 8, projection_state: 'READY',
    identity_revision: principal.identity_revision, company_id: principal.company_id,
    events: [{ event_id: 'synthetic-arrival', ledger_version: 8, event_type: 'ProcurementPosted', document_kind: 'ARRIVAL',
      summary: '고객 도착 기록 반영', recorded_at: '2026-01-01T00:00:00+00:00' }] } : undefined);
  const next = snapshotAt(8); next.summary = { delivery_round_id: 'R-1', arrived_qty: 33, promise_remaining_qty: 67 };
  h.setNextSnapshot(next);
  await h.app.poll();
  assert.match(h.app.panels().dashboard.status.textContent, /고객 도착 1대 반영 · 누적 도착 32→33대 · 남은 약속 68→67대/);
  assert.equal(h.app.panels().graph.status.textContent, '');
  assert.equal(h.requests.filter(request => request.path === '/api/mks2/query').length, 0);
});

// T5: delivery edge cases of committed-event reflection (DEC-H47/H49).
function relevantSetup(h) {
  h.snapshot().material_requirements = { items: [{ item_id: 'item-a' }] };
  h.app.panels().dashboard.topics.add('item-a');
}

test('a failed poll changes nothing and the next poll recovers the missed event', async () => {
  const h = await setup(); relevantSetup(h);
  let calls = 0;
  h.setHandler(request => request.path.startsWith('/api/mks2/events')
    ? (++calls === 1 ? { httpStatus: 503, code: 'SERVICE_UNAVAILABLE' } : reservationChanges('READY')) : undefined);
  h.setNextSnapshot(reservedSnapshot());
  await h.app.poll();
  assert.equal(h.app.current().meta.ledger_version, 7);
  assert.equal(h.app.panels().dashboard.status.textContent, '');
  await h.app.poll();
  assert.equal(h.app.current().meta.ledger_version, 8);
  assert.match(h.app.panels().dashboard.status.textContent, /23→28/);
});

test('an older or equal ledger version never moves the screen backwards', async () => {
  const h = await setup(); relevantSetup(h);
  h.setHandler(request => request.path.startsWith('/api/mks2/events')
    ? { ...reservationChanges('READY'), current_ledger_version: 6, snapshot_ledger_version: 6 } : undefined);
  h.setNextSnapshot(snapshotAt(6));
  await h.app.poll();
  assert.equal(h.app.current().meta.ledger_version, 7);
  assert.equal(h.requests.filter(request => request.path === '/api/mks2/snapshot').length, 0);
});

test('the same event delivered twice adds one status line and one refresh', async () => {
  const h = await setup(); relevantSetup(h);
  h.setHandler(request => request.path.startsWith('/api/mks2/events') ? reservationChanges('READY') : undefined);
  h.setNextSnapshot(reservedSnapshot());
  await h.app.poll(); await h.app.poll(); await h.app.poll();
  assert.equal(h.requests.filter(request => request.path === '/api/mks2/snapshot').length, 1);
  assert.equal(h.app.panels().dashboard.status.children.length, 1);
});

test('a commit during an in-flight question is deferred, then applied after the answer', async () => {
  const h = await setup(); relevantSetup(h);
  h.setHandler(request => request.path.startsWith('/api/mks2/events') ? reservationChanges('READY') : undefined);
  h.setNextSnapshot(reservedSnapshot());
  h.app.panels().dashboard.submit.disabled = true;
  await h.app.poll();
  assert.equal(h.app.current().meta.ledger_version, 7);
  assert.match(h.app.panels().dashboard.status.textContent, /근거 변경 대기/);
  h.app.panels().dashboard.submit.disabled = false;
  await h.app.poll();
  assert.equal(h.app.current().meta.ledger_version, 8);
  assert.match(h.app.panels().dashboard.status.textContent, /가용 20→15/);
});

test('a changed identity revision in the event feed ends the session view instead of applying', async () => {
  const h = await setup(); relevantSetup(h);
  h.setHandler(request => request.path.startsWith('/api/mks2/events')
    ? { ...reservationChanges('READY'), identity_revision: 'another-session' } : undefined);
  h.setNextSnapshot(reservedSnapshot());
  await h.app.poll();
  assert.equal(h.app.current(), null);
  assert.equal(h.requests.filter(request => request.path === '/api/mks2/snapshot').length, 0);
});

test('turning live reflection off keeps the snapshot and offers an explicit refresh', async () => {
  const h = await setup(); relevantSetup(h);
  h.setHandler(request => request.path.startsWith('/api/mks2/events') ? reservationChanges('READY') : undefined);
  h.setNextSnapshot(reservedSnapshot());
  h.app.setLive(false);
  await h.app.poll();
  assert.equal(h.app.current().meta.ledger_version, 7);
  assert.match(h.app.panels().dashboard.status.textContent, /최신 기준으로 보기/);
});

test('an answer still typing out does not block applying a committed event', async () => {
  const h = await setup(); relevantSetup(h);
  h.app.panels().dashboard.presentation = { finish() {}, dispose() {} };
  h.setHandler(request => request.path.startsWith('/api/mks2/events') ? reservationChanges('READY') : undefined);
  h.setNextSnapshot(reservedSnapshot());
  await h.app.poll();
  assert.equal(h.app.current().meta.ledger_version, 8);
  assert.match(h.app.panels().dashboard.status.textContent, /23→28/);
});

test('with a model connected the graph assistant takes the agent path and the twin keeps the read API', async () => {
  const h = await setup(); await h.app.load(); h.app.route('graph');
  h.setHandler(request => request.path === '/api/mks2/query' ? twinAnswer(h)
    : request.path.endsWith('/run') ? stateAt(h.snapshot(), { status: 'COMPLETED', revision: 3 })
      : stateAt(h.snapshot(), { status: 'READY', revision: 1 }));
  await h.app.askGraph('합성 그래프 질문');
  assert.deepEqual(h.requests.filter(r => r.path.startsWith('/api/mks2/')).map(r => r.path).filter(p => /agent|query/.test(p)),
    ['/api/mks2/agent/executions', '/api/mks2/agent/executions/synthetic-execution/run']);
  assert.match(h.app.graphPanel().messages.textContent, /합성 조회 결과/);
  h.app.route('twin2'); await h.app.askTwin('합성 트윈 질문');
  assert.equal(h.requests.filter(r => r.path === '/api/mks2/query').length, 1);
});

test('suggestions say why they are shown and mark questions that just appeared', async () => {
  const h = await setup();
  h.snapshot().suggested_questions = ['합성 회차 질문'];
  h.snapshot().summary = { bom_status: 'POSTED_SOURCE', item_id: 'item-a' };
  h.snapshot().material_requirements = { items: [{ item_id: 'item-a' }] };
  h.app.panels().dashboard.topics.add('item-a');
  await h.app.load();
  const container = h.app.panels().dashboard.suggestions;
  const texts = () => container.children.map(child => child.textContent);
  assert.equal(texts()[0], '현재 원장 기준 추천 질문');
  assert.equal(container.children.filter(child => child.className.includes('suggestion-new')).length, 0);
  const next = reservedSnapshot();
  next.summary = { bom_status: 'POSTED_SOURCE', item_id: 'item-a' };
  next.suggested_questions = ['합성 후속 질문', '합성 회차 질문'];
  h.setHandler(request => request.path.startsWith('/api/mks2/events') ? reservationChanges('READY') : undefined);
  h.setNextSnapshot(next);
  await h.app.poll();
  assert.equal(texts()[0], '다른 주문 예약 반영 (v8) · 확인할 질문');
  const fresh = container.children.filter(child => child.className.includes('suggestion-new'));
  assert.deepEqual(fresh.map(child => child.textContent), ['합성 후속 질문새']);
});
