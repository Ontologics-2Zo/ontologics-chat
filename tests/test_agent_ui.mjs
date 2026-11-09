import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

// Execute the application itself with only its three presentation imports
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
async function setup() {
  const elements = new Map(), requests = [];
  let data = snapshotAt(), handler = null, serial = 0;
  const get = id => { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); };
  const context = vm.createContext({
    document: { getElementById: get, createElement: tag => new Element(tag),
      createTextNode: text => { const value = new Element('#text'); value.textContent = text; return value; },
      documentElement: new Element('html') },
    window: { addEventListener() {}, frameElement: null }, location: { hash: '#dashboard' },
    AbortController, crypto: { randomUUID: () => `synthetic-key-${++serial}` },
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    requestAnimationFrame: callback => callback(),
    mountDashboard: () => ({ destroy() {}, select() {}, openBom() {} }),
    mountGraph: () => ({ destroy() {}, cancelReplay() {}, resize() {}, reset() {} }),
    renderQueryAnswer: (message, answer) => { message.textContent = answer.answer; return { play() {} }; },
    fetch: async (path, options = {}) => {
      const request = { path, method: options.method || 'GET', body: options.body && JSON.parse(options.body) };
      requests.push(request);
      let result;
      if (path === '/health') result = { agent: { mode: 'fake' } };
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
    createPanel('graph'); createPanel('dashboard');
    globalThis.app = { ask: question => runQuery('dashboard', question), refresh: refreshSnapshot,
      panel: () => panels.dashboard, generation: () => generation };`, context);
  return { requests, app: context.app, setHandler(value) { handler = value; },
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
