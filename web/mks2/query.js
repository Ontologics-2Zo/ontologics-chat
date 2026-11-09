// Server query presentation only. No facts, quantities or answers originate here.
const text = value => typeof value === 'string' ? value : '';
const node = (tag, className = '', value) => {
  const result = document.createElement(tag);
  result.className = className;
  if (value !== undefined) result.textContent = String(value);
  return result;
};
const action = (label, callback, className = 'btn sm') => {
  const result = node('button', className, label);
  result.type = 'button'; result.addEventListener('click', callback); return result;
};
const statuses = {
  ANSWERED: ['확인된 범위의 답변', 'ok'], PARTIAL: ['일부 확인', 'warn'],
  UNKNOWN: ['판단 보류', 'mute'], NEEDS_CLARIFICATION: ['질문 확인 필요', 'warn'],
};
const origins = {
  synthetic_opening_balance: ['합성 초기 원장', 'synthetic'], synthetic: ['합성 원장', 'synthetic'],
  actual_bom: ['실제 원문', 'source'], actual_source: ['실제 원문', 'source'],
  conditional_demo: ['시연 가정', 'assumption'], demo_assumption: ['시연 가정', 'assumption'],
  user_assumption: ['질문에서 지정한 가정', 'assumption'],
  proposed_demo_assumption: ['미적용 시연 제안', 'unknown'],
  unreviewed: ['미검토', 'unknown'], unknown: ['미확인', 'unknown'],
  development_workflow: ['개발 원장 기록', 'ledger'],
  reviewed_application: ['검토된 적용 판단', 'ledger'],
  mixed_sources: ['출처별 근거', 'mixed'],
};
function originBadge(value) {
  const raw = text(value);
  if (!raw) return null;
  const [label, kind] = Object.hasOwn(origins, raw.toLowerCase()) ? origins[raw.toLowerCase()] : [raw, 'other'];
  return node('span', `query-origin query-origin-${kind}`, label);
}
function appendOrigin(container, value) {
  const badge = originBadge(value);
  if (badge) container.append(badge);
}
function statusBadge(value) {
  const raw = text(value);
  if (!raw) return null;
  const [label, kind] = Object.hasOwn(statuses, raw) ? statuses[raw] : [raw, 'mute'];
  return node('span', `pill ${kind}`, label);
}
function sectionCard(section) {
  const card = node('section', 'query-section');
  card.dataset.sectionId = text(section.id);
  const heading = node('div', 'query-section-heading');
  heading.append(node('h3', '', text(section.title) || '확인 내용'));
  appendOrigin(heading, section.origin);
  const status = statusBadge(section.status);
  if (status) heading.append(status);
  card.append(heading);
  const list = node('dl', 'query-items');
  for (const item of Array.isArray(section.items) ? section.items : []) {
    if (!item || typeof item !== 'object') continue;
    const row = node('div', 'query-item');
    if (text(item.label)) row.append(node('dt', '', item.label));
    const value = node('dd', 'query-value', text(item.value));
    appendOrigin(value, item.origin);
    if (text(item.detail)) value.append(node('p', 'query-detail', item.detail));
    row.append(value); list.append(row);
  }
  card.append(list); return card;
}

function deliveryMetrics(sections) {
  const ledger = sections.find(section => section.id === 'ledger');
  if (!ledger) return null;
  // These are already labelled server facts, not values parsed or calculated
  // from the user's question. Missing facts do not get reference-demo defaults.
  const labels = ['남은 고객 도착 약속', '추가 양품 목표'];
  const metrics = node('div', 'query-delivery-metrics');
  for (const label of labels) {
    const matches = (Array.isArray(ledger.items) ? ledger.items : []).filter(item => item?.label === label && text(item.value));
    if (matches.length !== 1) continue;
    const item = matches[0], card = node('section', 'query-delivery-metric');
    card.append(node('h3', '', label), node('strong', 'query-metric-value', item.value));
    appendOrigin(card, item.origin || ledger.origin);
    if (text(item.detail)) card.append(node('p', 'query-detail', item.detail));
    metrics.append(card);
  }
  return metrics.children.length ? metrics : null;
}

function deliverySections(sections) {
  const result = node('div', 'query-sections query-delivery-flow');
  const groups = [
    ['현재 원장 수량', ['ledger']],
    ['양품 목표와 계획 투입', ['production']],
    ['BOM·재고 확인', ['source', 'materials', 'material_unknowns', 'inventory']],
    ['도착기한 판단에 남은 조건', ['delivery', 'next']],
  ];
  const included = new Set(groups.flatMap(([, ids]) => ids));
  const additional = sections.filter(section => !included.has(section.id));
  if (additional.length) groups.push(['추가 확인', additional.map(section => section.id)]);
  let index = 0;
  for (const [title, ids] of groups) {
    const selected = sections.filter(section => ids.includes(section.id));
    if (!selected.length) continue;
    const step = node('section', 'query-flow-step');
    if (ids.includes('delivery')) step.className += ' query-flow-conditions';
    const heading = node('div', 'query-flow-heading');
    heading.append(node('span', 'query-flow-number', String(++index)), node('h3', '', title));
    step.append(heading);
    for (const section of selected) step.append(sectionCard(section));
    result.append(step);
  }
  return result;
}

function evidenceFlow(answer, suppliedEdges, labelFor, onEvidence, onReplay) {
  const allowed = new Set(Array.isArray(answer.evidence_edge_ids) ? answer.evidence_edge_ids : []);
  const hops = new Set((Array.isArray(answer.graph_path) ? answer.graph_path : [])
    .filter(hop => Array.isArray(hop) && hop.length === 2 && hop.every(id => typeof id === 'string')).map(hop => JSON.stringify(hop)));
  const edges = [], seen = new Set();
  for (const raw of Array.isArray(suppliedEdges) ? suppliedEdges : []) {
    const id = text(raw?.edge_id) || text(raw?.id), source = text(raw?.source_node_id) || text(raw?.source);
    const target = text(raw?.target_node_id) || text(raw?.target), predicate = text(raw?.relation_type) || text(raw?.predicate);
    if (!id || !source || !target || !predicate || !allowed.has(id) || seen.has(id)
        || (hops.size && !hops.has(JSON.stringify([source, target])))) continue;
    seen.add(id); edges.push({ id, source, target, predicate, label: text(raw.label) || predicate });
  }
  if (!edges.length) return null;
  const result = node('section', 'query-evidence-flow');
  const heading = node('div', 'query-section-heading');
  heading.append(node('h3', '', '조회한 근거 관계'), action('그래프에서 보기', onReplay));
  result.append(heading, node('p', 'query-detail', '현재 원장에 기록된 관계입니다. 업무 완료 순서나 미래 일정으로 해석하지 않습니다.'));
  const visible = node('ol', 'query-path-list'), remaining = node('ol', 'query-path-list');
  edges.forEach((edge, index) => {
    const row = node('li', 'query-path-hop'); row.dataset.edgeId = edge.id;
    row.setAttribute('title', `${edge.predicate} · ${edge.id}`);
    const arrow = node('span', 'query-path-predicate', `— ${edge.label} →`);
    row.append(action(labelFor(edge.source), () => onEvidence(edge.source), 'chip'), arrow,
      action(labelFor(edge.target), () => onEvidence(edge.target), 'chip'));
    (index < 4 ? visible : remaining).append(row);
  });
  result.append(visible);
  if (edges.length > 4) {
    const more = node('details', 'query-more-paths');
    more.append(node('summary', '', `전체 관계 ${edges.length}개 · 나머지 ${edges.length - 4}개 보기`), remaining);
    result.append(more);
  }
  return result;
}

export function renderQueryAnswer(message, answer, {
  evidenceIds = [], evidenceEdges = [], labelFor = id => id, onEvidence = () => {},
  onReplay = () => {}, onCancel = () => {},
} = {}) {
  message.replaceChildren();
  const sectionData = (Array.isArray(answer.sections) ? answer.sections : []).filter(section => section && typeof section === 'object');
  const delivery = Array.isArray(answer.intent) && answer.intent.includes('delivery') && answer.status !== 'NEEDS_CLARIFICATION';
  message.dataset.queryPresentation = delivery ? 'delivery' : 'standard';
  const verdict = delivery ? node('section', 'query-verdict') : message;
  const summary = node('div', 'query-summary');
  if (text(answer.intent_label)) summary.append(node('span', 'query-intent', answer.intent_label));
  const status = statusBadge(answer.status);
  if (status) summary.append(status);
  if (delivery && answer.calculation?.actual_delivery_feasibility === 'UNKNOWN') summary.append(node('span', 'query-delivery-status', '납기 판단 보류'));
  if (summary.children.length) verdict.append(summary);
  if (text(answer.conclusion)) verdict.append(node('p', 'query-conclusion', answer.conclusion));
  verdict.append(node('p', 'answer', text(answer.answer) || text(answer.answer?.text) || '확인된 답변이 없습니다.'));
  if (delivery) {
    message.append(verdict);
    const metrics = deliveryMetrics(sectionData);
    if (metrics) message.append(metrics);
    const flow = evidenceFlow(answer, evidenceEdges, labelFor, onEvidence, onReplay);
    if (flow) message.append(flow);
  }
  const sections = delivery ? deliverySections(sectionData) : node('div', 'query-sections');
  if (!delivery) for (const section of sectionData) sections.append(sectionCard(section));
  if (sections.children.length) message.append(sections);

  const meta = answer.meta || {};
  const basis = node('div', 'query-basis');
  if (answer.agent_execution) {
    const execution = answer.agent_execution;
    const mode = execution.live_calls > 0 ? `실제 모델 기능 선택 ${execution.live_calls}회`
      : execution.fake_calls > 0 ? `Fake 모델 기능 선택 ${execution.fake_calls}회`
      : '직접 기능 선택 · 모델 호출 없음';
    basis.append(node('p', '', `${mode} · 수량·부족 답변은 서버 원장 계산 · 원장 변경 없음`));
    basis.append(node('p', 'query-snapshot', `실행 · ${text(execution.execution_id)}`));
  }
  basis.append(node('p', '', `기준 시점 · ${text(meta.as_of) || text(meta.effective_at) || '미확인'}`));
  if (text(meta.known_as_of)) basis.append(node('p', '', `확인 시점 · ${meta.known_as_of}`));
  const snapshot = text(meta.snapshot_id) || text(meta.read_snapshot_id);
  if (snapshot) basis.append(node('p', 'query-snapshot', `스냅샷 · ${snapshot}`));
  message.append(basis);

  const trace = node('details', 'query-trace');
  trace.append(node('summary', '', '조회 과정과 근거'));
  const steps = node('ol', 'trace-steps');
  for (const step of Array.isArray(answer.steps) ? answer.steps : []) {
    if (text(step)) steps.append(node('li', '', step));
  }
  if (steps.children.length) trace.append(steps);
  message.append(trace);
  const evidence = node('div', 'evidence-path');
  const ids = [...new Set(evidenceIds.filter(id => typeof id === 'string'))];
  const extra = node('details', 'query-more-evidence');
  extra.append(node('summary', '', `근거 전체 ${ids.length}개 보기`));
  ids.forEach((id, index) => {
    const chip = action(labelFor(id), () => onEvidence(id), 'chip');
    (index < 6 ? evidence : extra).append(chip);
  });
  if (ids.length > 6) evidence.append(extra);
  message.append(evidence);

  const footer = node('div', 'answer-footer');
  const liveCalls = answer.agent_execution?.live_calls ?? answer.model_calls ?? 0;
  footer.append(node('span', '', `원장 v${meta.ledger_version ?? '미확인'} · 그래프 v${meta.projection_version ?? '미확인'} · 모델 호출 ${liveCalls}회`));
  const replayStatus = node('span', 'query-replay-status');
  replayStatus.setAttribute('aria-live', 'polite');
  const cancel = action('재생 중단', () => { playVersion++; onCancel(); setReplayState('cancelled'); });
  cancel.hidden = true;
  if (ids.length) footer.append(action('근거 다시 보기', onReplay));
  footer.append(cancel, replayStatus); message.append(footer);
  let playVersion = 0;

  function setReplayState(state) {
    cancel.hidden = state !== 'playing';
    replayStatus.textContent = {
      playing: '답변 표시 완료 · 근거 경로 재생 중', complete: '근거 경로 재생 완료',
      cancelled: '근거 재생 중단 · 답변과 기준 버전은 유지됩니다.',
      unavailable: '근거를 재생하지 못했습니다. 답변은 표시된 조회 기준을 유지합니다.',
    }[state] || '';
    for (const step of steps.children) step.className = state === 'complete' ? 'done' : '';
  }
  return {
    async play(replay, isCurrent = () => true) {
      const version = ++playVersion;
      setReplayState('playing');
      try {
        const result = await replay();
        if (version === playVersion) setReplayState(isCurrent() && result?.completed ? 'complete' : 'cancelled');
      } catch {
        if (version === playVersion) setReplayState(isCurrent() ? 'unavailable' : 'cancelled');
      }
    },
  };
}
