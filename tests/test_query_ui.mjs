import assert from 'node:assert/strict';
import test from 'node:test';
import { renderQueryAnswer } from '../web/mks2/query.js';

class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attributes = {}; this.events = {}; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw Error('Server content must never be interpreted as HTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { this.events[name] = callback; }
  click() { this.events.click?.(); }
}
const descendants = node => [node, ...node.children.flatMap(descendants)];
const withClass = (node, value) => descendants(node).filter(item => item.className?.split(' ').includes(value));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function setup() {
  const previous = globalThis.document;
  globalThis.document = { createElement: tag => new Element(tag) };
  return { message: new Element('div'), restore: () => {
    if (previous === undefined) delete globalThis.document;
    else globalThis.document = previous;
  } };
}
function response() {
  return {
    answer: '일부 근거만 확인되었습니다.', status: 'PARTIAL',
    intent_label: '합성 검사 질의', conclusion: '조건부 계산', model_calls: 0,
    meta: { ledger_version: 7, projection_version: 7, snapshot_id: 'synthetic-snapshot-seven',
      effective_at: '2001-01-01T00:00:00+09:00', known_as_of: '2001-01-02T00:00:00+09:00' },
    sections: [
      { id: 'ledger', title: '원장 확인값', origin: 'synthetic_opening_balance',
        items: [{ label: '검사 잔액', value: '9', detail: '독립 합성 자료' }] },
      { id: 'source', title: '실제 원문', origin: 'actual_source',
        items: [{ label: '출처 상태', value: '접수됨', detail: '이 테스트의 내용은 합성입니다.' }] },
      { id: 'assumptions', title: '시연 가정', origin: 'conditional_demo',
        items: [{ label: '검사 계획', value: '가정값', origin: 'user_assumption' }] },
      { id: 'unknowns', title: '미확인 / 다음 확인', origin: 'unknown', status: 'UNKNOWN',
        items: [{ label: '후속 판단', value: '미확인', detail: '확인된 사실로 승격하지 않음', origin: 'proposed_demo_assumption' }] },
    ],
    steps: ['원장 조회', '근거 확인'],
  };
}

test('sections distinguish source, synthetic ledger, assumptions and unknowns', () => {
  const { message, restore } = setup();
  try {
    const data = response(), before = JSON.stringify(data);
    renderQueryAnswer(message, data);
    assert.equal(withClass(message, 'query-section').length, 4);
    for (const label of ['합성 초기 원장', '실제 원문', '시연 가정', '미확인', '미적용 시연 제안', '질문에서 지정한 가정', '일부 확인']) {
      assert.ok(message.textContent.includes(label), label);
    }
    assert.ok(message.textContent.includes('원장 v7 · 그래프 v7 · 모델 호출 0회'));
    assert.ok(message.textContent.includes('기준 시점 · 2001-01-01T00:00:00+09:00'));
    assert.ok(message.textContent.includes('확인 시점 · 2001-01-02T00:00:00+09:00'));
    assert.ok(message.textContent.includes('synthetic-snapshot-seven'));
    assert.equal(JSON.stringify(data), before);
  } finally { restore(); }
});

test('untrusted server strings stay literal text without HTML or unsafe classes', () => {
  const { message, restore } = setup();
  try {
    const attack = '<img src=x onerror="alert(1)"><script>bad()</script>';
    const data = response();
    data.answer = attack; data.conclusion = attack;
    data.sections = [{ id: attack, title: attack, origin: attack, status: attack,
      items: [{ label: attack, value: attack, detail: attack, origin: attack }] }];
    renderQueryAnswer(message, data);
    assert.equal(withClass(message, 'answer')[0].textContent, attack);
    assert.equal(descendants(message).filter(item => ['IMG', 'SCRIPT'].includes(item.tagName)).length, 0);
    assert.ok(withClass(message, 'query-origin-other').length);
    assert.ok(descendants(message).every(item => !item.className?.includes('onerror')));
  } finally { restore(); }
});

test('reviewed application and mixed sources do not inherit a demo or source badge', () => {
  const { message, restore } = setup();
  try {
    const data = response();
    data.sections = [{ id: 'mixed', title: '적용과 재고 근거', origin: 'mixed_sources', items: [
      { label: '검사 적용', value: '검토됨', origin: 'reviewed_application' },
      { label: '검사 재고', value: '합성값', origin: 'synthetic_opening_balance' },
    ] }];
    renderQueryAnswer(message, data);
    assert.deepEqual(withClass(message, 'query-origin').map(badge => badge.textContent),
      ['출처별 근거', '검토된 적용 판단', '합성 초기 원장']);
    assert.equal(withClass(message, 'query-origin-assumption').length, 0);
    assert.equal(withClass(message, 'query-origin-source').length, 0);
  } finally { restore(); }
});

test('answer is visible while replay is pending and survives cancellation', async () => {
  const { message, restore } = setup();
  try {
    const wait = deferred(); let cancelled = false;
    const view = renderQueryAnswer(message, response(), { evidenceIds: ['synthetic-node'], onCancel: () => { cancelled = true; } });
    const playback = view.play(() => wait.promise);
    assert.equal(withClass(message, 'answer')[0].textContent, '일부 근거만 확인되었습니다.');
    assert.equal(withClass(message, 'query-section').length, 4);
    assert.match(message.textContent, /답변 표시 완료.*재생 중/);
    const stop = descendants(message).find(item => item.tagName === 'BUTTON' && item.textContent === '재생 중단');
    assert.equal(stop.hidden, false); stop.click();
    wait.resolve({ completed: false }); await playback;
    assert.equal(cancelled, true);
    assert.match(message.textContent, /근거 재생 중단/);
    assert.equal(withClass(message, 'answer').length, 1);
    assert.ok(message.textContent.includes('synthetic-snapshot-seven'));
  } finally { restore(); }
});

test('replay failure or superseded playback never replaces the answer', async () => {
  const { message, restore } = setup();
  try {
    const view = renderQueryAnswer(message, response());
    await view.play(() => Promise.reject(Error('synthetic animation failure')));
    assert.match(message.textContent, /근거를 재생하지 못했습니다/);
    assert.equal(withClass(message, 'answer').length, 1);
    const first = deferred(), second = deferred();
    const older = view.play(() => first.promise);
    const latest = view.play(() => second.promise);
    first.resolve({ completed: false }); await older;
    assert.match(message.textContent, /답변 표시 완료.*재생 중/);
    second.resolve({ completed: true }); await latest;
    assert.match(message.textContent, /근거 경로 재생 완료/);
    assert.equal(withClass(message, 'done').length, 2);
  } finally { restore(); }
});

test('all evidence stays accessible and past answer metadata does not change', () => {
  const { message, restore } = setup();
  try {
    const data = response(), selected = [];
    const ids = Array.from({ length: 10 }, (_, index) => `synthetic-evidence-${index}`);
    renderQueryAnswer(message, data, { evidenceIds: [...ids, ids[0]], onEvidence: id => selected.push(id) });
    assert.equal(withClass(message, 'chip').length, 10);
    assert.equal(withClass(message, 'query-more-evidence').length, 1);
    withClass(message, 'chip')[9].click();
    assert.deepEqual(selected, [ids[9]]);
    const original = message.textContent;
    data.meta.ledger_version = 8; data.meta.snapshot_id = 'synthetic-snapshot-eight';
    renderQueryAnswer(new Element('div'), data);
    assert.equal(message.textContent, original);
    assert.ok(message.textContent.includes('원장 v7'));
  } finally { restore(); }
});

test('clarification status is explicit without inventing sections or evidence', () => {
  const { message, restore } = setup();
  try {
    renderQueryAnswer(message, { answer: '검사 대상을 지정해 주세요.', status: 'NEEDS_CLARIFICATION',
      intent_label: '대상 확인', conclusion: '질문 확인 필요', meta: response().meta, model_calls: 0 });
    assert.match(message.textContent, /질문 확인 필요/);
    assert.equal(withClass(message, 'query-section').length, 0);
    assert.equal(withClass(message, 'chip').length, 0);
  } finally { restore(); }
});

test('Agent router live calls are visible independently of deterministic query metadata', () => {
  const fixture = setup();
  try {
    const answer = { ...response(), model_calls: 0, agent_execution: {
      execution_id: 'execution-test', live_calls: 1, fake_calls: 0, mode: 'live' } };
    renderQueryAnswer(fixture.message, answer);
    assert.match(fixture.message.textContent, /실제 모델 기능 선택 1회/);
    assert.match(withClass(fixture.message, 'answer-footer')[0].textContent, /모델 호출 1회/);
    answer.agent_execution = { ...answer.agent_execution, live_calls: 0, fake_calls: 1, mode: 'fake' };
    renderQueryAnswer(fixture.message, answer);
    assert.match(fixture.message.textContent, /Fake 모델 기능 선택 1회/);
    assert.match(withClass(fixture.message, 'answer-footer')[0].textContent, /모델 호출 0회/);
  } finally { fixture.restore(); }
});
