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

test('an explicitly missing BOM offers only the existing registration entry after a click', () => {
  const { message, restore } = setup();
  try {
    const data = response(); data.intent = ['delivery'];
    data.calculation = { actual_delivery_feasibility: 'UNKNOWN',
      material_requirements: { state: 'BLOCKED_BOM', bom_id: null } };
    const before = JSON.stringify(data); let opens = 0;
    renderQueryAnswer(message, data, { onOpenBom: () => { opens++; } });
    const controls = descendants(message).filter(item => item.dataset.queryAction === 'bom-register');
    assert.equal(controls.length, 1); assert.equal(controls[0].textContent, 'BOM 원본 등록');
    assert.equal(controls[0].className, 'btn pri sm'); assert.equal(controls[0].type, 'button');
    assert.equal(opens, 0); controls[0].click(); assert.equal(opens, 1);
    assert.equal(JSON.stringify(data), before);
  } finally { restore(); }
});

test('registered, partial, unknown and absent BOM calculations do not offer registration from an answer', () => {
  const { message, restore } = setup();
  try {
    for (const requirements of [undefined, {}, { state: 'UNKNOWN', bom_id: null },
      { state: 'PARTIAL', bom_id: 'synthetic-bom' }, { state: 'CALCULATED', bom_id: 'synthetic-bom' },
      { state: 'BLOCKED_BOM', bom_id: 'synthetic-bom' }]) {
      const data = response(); data.calculation = { material_requirements: requirements };
      renderQueryAnswer(message, data);
      assert.equal(descendants(message).filter(item => item.dataset.queryAction === 'bom-register').length, 0);
    }
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

function deliveryResponse() {
  return { ...response(), intent: ['delivery', 'fulfillment', 'production'], intent_label: '도착기한 확인',
    conclusion: '확인 조건 부족 · 도착 판단 보류', answer: '합성 테스트의 현재 잔량과 계획만 확인했습니다.',
    calculation: { actual_delivery_feasibility: 'UNKNOWN', estimated_arrival: null, delivery_probability: null },
    sections: [
      { id: 'ledger', title: '현재 원장 수량', origin: 'synthetic_opening_balance', items: [
        { label: '남은 고객 도착 약속', value: '17대', detail: '현재 약속과 도착 기록의 차이입니다.' },
        { label: '추가 양품 목표', value: '9대', detail: '이미 생산한 수량과 구분합니다.' },
        { label: '기존 미도착', value: '8대', detail: '생산 목표에 다시 더하지 않습니다.' },
      ] },
      { id: 'production', title: '명시된 계획 가정', origin: 'user_assumption', status: 'PARTIAL', items: [
        { label: '계획 투입', value: '12대', detail: '계획이며 완료 실적이 아닙니다.' },
      ] },
      { id: 'source', title: '원본 등록 근거', origin: 'actual_bom', items: [
        { label: '원본 전체 행', value: '35행' }, { label: '적용 판단 / 미확인', value: '0행 / 35행' },
      ] },
      { id: 'materials', title: '조회 범위 · 0개 품목', origin: 'unknown', status: 'PARTIAL', items: [
        { label: '선택 품목 소요·부족', value: '미확인', detail: '현재 원본에 적용 판단이 없습니다.' },
      ] },
      { id: 'material_unknowns', title: '자재 판단의 남은 확인', origin: 'unknown', status: 'UNKNOWN', items: [
        { label: '부족량 미확인', value: '35행', detail: '미확인은 부족 0을 뜻하지 않습니다.' },
      ] },
      { id: 'delivery', title: '납기 가능 여부 · 판단 보류', origin: 'unknown', status: 'UNKNOWN', items: [
        { label: '능력과 작업 달력', value: '미확인', detail: '실제 시작 근거가 필요합니다.' },
      ] },
      { id: 'next', title: '다음 확인', origin: 'unknown', status: 'UNKNOWN', items: [
        { label: '실제 판단', value: '근거 확인 후 재조회' },
      ] },
    ],
  };
}

test('delivery answer leads with server conclusion and distinct remaining-promise and production metrics', () => {
  const { message, restore } = setup();
  try {
    const data = deliveryResponse(), before = JSON.stringify(data);
    renderQueryAnswer(message, data);
    assert.equal(message.dataset.queryPresentation, 'delivery');
    assert.deepEqual(withClass(message, 'query-metric-value').map(value => value.textContent), ['17대', '9대']);
    assert.equal(withClass(message, 'query-flow-step').length, 4);
    assert.deepEqual(withClass(message, 'query-flow-number').map(value => value.textContent), ['1', '2', '3', '4']);
    assert.equal(withClass(message, 'query-section').length, data.sections.length);
    assert.ok(message.children.indexOf(withClass(message, 'query-verdict')[0]) < message.children.indexOf(withClass(message, 'query-delivery-metrics')[0]));
    assert.ok(message.textContent.includes('납기 판단 보류'));
    assert.ok(message.textContent.includes('합성 초기 원장') && message.textContent.includes('질문에서 지정한 가정'));
    assert.equal(JSON.stringify(data), before);
  } finally { restore(); }
});

test('an unmapped active BOM keeps its current counts and unknown shortage without reusing old example results', () => {
  const { message, restore } = setup();
  try {
    renderQueryAnswer(message, deliveryResponse());
    for (const value of ['0개 품목', '0행 / 35행', '부족량 미확인', '35행', '미확인은 부족 0을 뜻하지 않습니다.']) {
      assert.ok(message.textContent.includes(value), value);
    }
    for (const absent of ['68대', '40대', '43대', '94%', '5개 품목', '69행', '도착 가능합니다']) {
      assert.ok(!message.textContent.includes(absent), absent);
    }
    assert.equal(withClass(message, 'query-flow-conditions').length, 1);
    assert.equal(withClass(message, 'query-origin-source').length, 1);
  } finally { restore(); }
});

test('missing or ambiguous labelled metrics do not invent defaults or reuse a prior answer', () => {
  const { message, restore } = setup();
  try {
    renderQueryAnswer(message, deliveryResponse());
    const data = deliveryResponse();
    data.sections[0].items = [{ label: '추가 양품 목표', value: '7대' }, { label: '추가 양품 목표', value: '8대' }];
    renderQueryAnswer(message, data);
    assert.equal(withClass(message, 'query-delivery-metrics').length, 0);
    assert.equal(withClass(message, 'query-metric-value').length, 0);
    assert.ok(!message.textContent.includes('17대') && !message.textContent.includes('9대'));
    assert.ok(message.textContent.includes('7대') && message.textContent.includes('8대'));
  } finally { restore(); }
});

test('delivery relation preview preserves actual edge IDs, direction and parallel predicates with all others accessible', () => {
  const { message, restore } = setup();
  try {
    const data = deliveryResponse(), edges = Array.from({ length: 9 }, (_, index) => ({
      id: `synthetic-edge-${index}`, source: `synthetic-node-${index}`, target: `synthetic-node-${index + 1}`,
      predicate: `syntheticRelation${index}`, label: `합성 관계 ${index}`,
    }));
    edges[1] = { ...edges[1], source: edges[0].source, target: edges[0].target };
    data.evidence_edge_ids = edges.map(edge => edge.id);
    data.graph_path = edges.map(edge => [edge.source, edge.target]);
    const selected = [], replayed = [];
    renderQueryAnswer(message, data, { evidenceEdges: [...edges, edges[0]],
      labelFor: id => `표시 ${id}`, onEvidence: id => selected.push(id), onReplay: () => replayed.push(true) });
    const hops = withClass(message, 'query-path-hop');
    assert.equal(hops.length, 9);
    assert.deepEqual(hops.map(hop => hop.dataset.edgeId), edges.map(edge => edge.id));
    assert.equal(withClass(message, 'query-more-paths').length, 1);
    assert.equal(withClass(message, 'query-path-list')[0].children.length, 4);
    assert.equal(withClass(message, 'query-path-list')[1].children.length, 5);
    assert.match(hops[0].textContent, /표시 synthetic-node-0— 합성 관계 0 →표시 synthetic-node-1/);
    assert.match(hops[1].attributes.title, /syntheticRelation1 · synthetic-edge-1/);
    withClass(hops[0], 'chip')[1].click();
    assert.deepEqual(selected, [edges[0].target]);
    descendants(message).find(element => element.tagName === 'BUTTON' && element.textContent === '그래프에서 보기').click();
    assert.deepEqual(replayed, [true]);
  } finally { restore(); }
});

test('mismatched or unsupported relation metadata cannot fabricate a query path', () => {
  const { message, restore } = setup();
  try {
    const data = deliveryResponse();
    data.evidence_edge_ids = ['allowed-edge']; data.graph_path = [['source-node', 'target-node']];
    renderQueryAnswer(message, data, { evidenceIds: ['source-node', 'target-node'], evidenceEdges: [
      { id: 'allowed-edge', source: 'target-node', target: 'source-node', predicate: 'reversed' },
      { id: 'other-edge', source: 'source-node', target: 'target-node', predicate: 'unrelated' },
      { id: 'allowed-edge', source: 'source-node', target: 'target-node' },
    ] });
    assert.equal(withClass(message, 'query-evidence-flow').length, 0);
    assert.equal(withClass(message, 'chip').length, 2);
    assert.ok(!message.textContent.includes('reversed') && !message.textContent.includes('unrelated'));
  } finally { restore(); }
});

test('delivery metrics and relation labels remain literal untrusted data', () => {
  const { message, restore } = setup();
  try {
    const attack = '<img src=x onerror="attack()"><script>attack()</script>', data = deliveryResponse();
    data.sections[0].items[0].value = attack; data.sections[0].items[0].origin = '__proto__';
    data.status = 'constructor';
    data.graph_path = [['synthetic-left', 'synthetic-right']]; data.evidence_edge_ids = ['synthetic-edge'];
    renderQueryAnswer(message, data, { evidenceEdges: [{ edge_id: 'synthetic-edge', source_node_id: 'synthetic-left',
      target_node_id: 'synthetic-right', relation_type: attack }], labelFor: () => attack });
    assert.equal(withClass(message, 'query-metric-value')[0].textContent, attack);
    assert.equal(withClass(message, 'query-path-predicate')[0].textContent, `— ${attack} →`);
    assert.equal(descendants(message).filter(element => ['IMG', 'SCRIPT'].includes(element.tagName)).length, 0);
    assert.ok(descendants(message).every(element => !element.className?.includes('onerror')));
  } finally { restore(); }
});

test('delivery remains visible before replay and cancellation cannot be overwritten by a late successful animation', async () => {
  const { message, restore } = setup();
  try {
    const pending = deferred(), data = deliveryResponse();
    const view = renderQueryAnswer(message, data, { evidenceIds: ['synthetic-node'] });
    const replay = view.play(() => pending.promise);
    assert.equal(withClass(message, 'query-verdict').length, 1);
    assert.equal(withClass(message, 'query-delivery-metric').length, 2);
    descendants(message).find(element => element.tagName === 'BUTTON' && element.textContent === '재생 중단').click();
    pending.resolve({ completed: true }); await replay;
    assert.ok(message.textContent.includes('근거 재생 중단'));
    assert.ok(!message.textContent.includes('근거 경로 재생 완료'));
    assert.ok(message.textContent.includes(data.conclusion));
  } finally { restore(); }
});

test('delivery clarification does not pretend a numbered calculation or receipt of evidence occurred', () => {
  const { message, restore } = setup();
  try {
    renderQueryAnswer(message, { intent: ['delivery'], status: 'NEEDS_CLARIFICATION',
      answer: '합성 테스트 회차를 지정해 주세요.', meta: response().meta });
    assert.equal(withClass(message, 'query-flow-step').length, 0);
    assert.equal(withClass(message, 'query-delivery-metric').length, 0);
    assert.equal(withClass(message, 'query-evidence-flow').length, 0);
    assert.equal(message.dataset.queryPresentation, 'standard');
  } finally { restore(); }
});
