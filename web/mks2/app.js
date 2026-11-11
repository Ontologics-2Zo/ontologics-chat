import { mountDashboard } from './dashboard.js';
import { mountGraph } from './graph.js';
import { mountLaneGraph } from './lanegraph.js';
import { renderQueryAnswer, updateAiSummary } from './query.js';
import { mountTwin } from './twin.js';

// UI state is ephemeral. Identity, quantities, versions and answers come from the server.
const $ = id => document.getElementById(id);
const element = (tag, className = '', text) => {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
};
function button(text, action, className = 'btn sm') {
  const node = element('button', className, text);
  node.type = 'button'; node.addEventListener('click', action); return node;
}
// graph = project ontology graph tab (#network); lane = MK-S2 relationship graph (#graph).
let snapshot, ontology, graph, lane, dashboard, twin, identity, csrfToken;
let liveEnabled = true;
const handledEvents = new Set();
let contextId, twinContextId, noticeTimer, generation = 0, replayGeneration = 0, schemaMode = false;
let requests = new AbortController();
let polling = false, latestVersion = null;
let agentMode = 'disabled';
const refreshButton = button('최신 원장 조회', refreshSnapshot);
refreshButton.id = 'refresh-ledger'; refreshButton.hidden = true;
$('reset').before(refreshButton);
const panels = {};
const SCREENS = ['graph', 'dashboard', 'network', 'twin'];
const currentScreen = () => SCREENS.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'graph';
const labelFor = id => snapshot?.graph?.data?.nodes?.find(node => (node.node_id || node.id) === id)?.display_id || id;
function notify(text) {
  $('notice').textContent = text; $('notice').hidden = false;
  clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $('notice').hidden = true; }, 4500);
}
function cancelPendingReplay() { replayGeneration++; graph?.cancelReplay(); lane?.cancelReplay(); }
// A completed server answer has its own presentation lifetime. Graph playback
// must never cancel its text, and old answers keep their original full content.
function settleAnswer(panel, discard = false) {
  const presentation = panel?.presentation;
  if (!presentation) return;
  panel.presentation = null;
  if (discard) presentation.dispose?.();
  else presentation.finish?.();
}
function answerVisible(name) {
  return currentScreen() === name && !document.hidden
    && (!window.frameElement || window.frameElement.getBoundingClientRect().height > 0);
}
function settleHiddenAnswers() {
  for (const [name, panel] of Object.entries(panels)) if (!answerVisible(name)) settleAnswer(panel);
}
async function api(path, body, method, { binary = false } = {}) {
  const headers = { 'X-Requested-With': 'MK-S2' };
  if (identity?.identity_revision != null) headers['X-Identity-Revision'] = String(identity.identity_revision);
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
  }
  const response = await fetch(path, { method: method || (body === undefined ? 'GET' : 'POST'), headers,
    body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin',
    cache: 'no-store', signal: requests.signal });
  const data = response.ok && binary ? await response.blob() : await response.json();
  if (!response.ok) {
    const error = new Error(data.error?.code || data.code || 'SERVICE_UNAVAILABLE');
    error.status = response.status;
    if (response.status === 401 || ['IDENTITY_CHANGED', 'IDENTITY_INACTIVE', 'IDENTITY_REQUIRED'].includes(error.message)) {
      identity = null; csrfToken = null; invalidateView();
      $('load-state').textContent = '계정 권한이 변경되었거나 세션이 종료되었습니다. 계정을 다시 선택해 주세요.';
    }
    throw error;
  }
  return data;
}
function errorText(error) {
  if (['IDENTITY_CHANGED', 'IDENTITY_INACTIVE', 'IDENTITY_REQUIRED'].includes(error.message)) return '계정 권한이 변경되었거나 세션이 종료되었습니다. 계정을 다시 선택해 주세요.';
  if (error.status === 401) return '세션이 만료되었거나 계정 권한이 바뀌었습니다. 계정을 다시 선택해 주세요.';
  if (error.status === 403) return '현재 계정으로 이 업무를 조회할 수 없습니다.';
  if (error.message === 'PROJECTION_PENDING') return '원장은 준비되었지만 그래프 검증·투영이 아직 완료되지 않았습니다. 같은 버전의 근거가 준비된 뒤 다시 조회해 주세요.';
  if (error.status === 409) return '조회 기준이 바뀌었거나 같은 버전의 근거가 준비되지 않았습니다. 최신 원장을 다시 조회해 주세요.';
  return '서버 자료를 조회하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.';
}
function route() {
  if (!snapshot) return;
  settleHiddenAnswers();
  for (const name of SCREENS) {
    $(`${name}-view`).hidden = currentScreen() !== name;
    $(`tab-${name}`).setAttribute('aria-current', currentScreen() === name ? 'page' : 'false');
  }
  if (currentScreen() === 'graph') requestAnimationFrame(() => lane?.resize());
  if (currentScreen() === 'network') requestAnimationFrame(() => graph?.resize());
  updateTwinActivity();
}
function updateTwinActivity() {
  settleHiddenAnswers();
  twin?.setActive(currentScreen() === 'twin' && !document.hidden
    && (!window.frameElement || window.frameElement.getBoundingClientRect().height > 0));
}
function setContext(value) {
  contextId = typeof value === 'string' ? value : value?.node_id ?? value?.id;
  if (!contextId) return;
  for (const name of ['graph', 'dashboard', 'network']) {
    const panel = panels[name];
    panel.context.replaceChildren(document.createTextNode('맥락 '), button(labelFor(contextId), () => focusEvidence(contextId), 'chip'),
      document.createTextNode(` 원장 v${snapshot.meta.ledger_version}`));
  }
}
function setTwinContext(value) {
  twinContextId = typeof value === 'string' ? value : value?.node_id ?? value?.id;
  if (!twinContextId || !panels.twin) return;
  const selected = twinContextId;
  panels.twin.context.replaceChildren(document.createTextNode('맥락 '), button(labelFor(selected), () => focusEvidence(selected), 'chip'),
    document.createTextNode(` 원장 v${snapshot.meta.ledger_version}`));
}
function focusEvidence(id) {
  cancelPendingReplay();
  location.hash = '#graph';
  lane?.focus(id);
}
// Visible progress while a question runs: spinner, elapsed seconds and the
// server's own completed steps. It describes server events, not a fake bar.
const PROGRESS_STEPS = [['route', '질문 분석 · AI'], ['read', '원장 조회·계산'], ['validate', '결과 확인'], ['retrieve', '근거 검색']];
const PROGRESS_STAGE = { route: 'route', fulfillment_read: 'read', material_analysis: 'read', product_review: 'read',
  procurement_review: 'read', validate: 'validate', retrieve: 'retrieve' };
function startProgress(message, agent) {
  const root = element('div', 'mk-progress'), head = element('div', 'mk-progress-head');
  const time = element('span', 'mk-progress-time', '0초');
  head.append(element('span', 'mk-spinner'), element('span', 'mk-progress-title', agent ? '답변을 만들고 있습니다' : '원장을 조회하고 있습니다'), time);
  root.append(head);
  const items = new Map();
  if (agent) {
    const list = element('ol', 'mk-progress-steps');
    for (const [key, label] of PROGRESS_STEPS) { const item = element('li', 'pending', label); items.set(key, item); list.append(item); }
    items.get('route').className = 'active';
    root.append(list);
  }
  root.append(element('p', 'note', '원장 변경 없음 · 보통 10초 안팎 걸립니다.'));
  message.replaceChildren(root);
  const started = Date.now();
  const timer = setInterval(() => { time.textContent = `${Math.floor((Date.now() - started) / 1000)}초`; }, 1000);
  return {
    mark(stage, status) {
      const key = PROGRESS_STAGE[stage], item = items.get(key);
      if (!item) return;
      if (status === 'COMPLETED') {
        item.className = 'done';
        const next = PROGRESS_STEPS[PROGRESS_STEPS.findIndex(([name]) => name === key) + 1];
        if (next && items.get(next[0]).className === 'pending') items.get(next[0]).className = 'active';
      } else if (item.className !== 'done') item.className = 'active';
    },
    stop() { clearInterval(timer); },
  };
}
// Sends a question at once in its own panel (used by "같은 질문 다시 하기").
function askNow(name, question) {
  const panel = panels[name];
  if (!panel || !question || panel.submit.disabled) return false;
  if (panel.input.value.trim() === question) panel.input.value = '';
  runQuery(name, question);
  return true;
}
function fillQuestion(text, name = currentScreen()) { panels[name].input.value = text; panels[name].input.focus(); }
// After the basis changes, put the panel's last question back in its input so
// it can be asked again on the new ledger. Never overwrites what is typed.
function offerSameQuestion(name, question = panels[name]?.lastQuestion) {
  const panel = panels[name];
  if (!panel || !question || panel.input.value.trim()) return false;
  panel.input.value = question;
  return true;
}
// After an explicit BOM post, offer to resend the last question; it only fills the draft.
function offerRerun(kind) {
  for (const name of ['dashboard', 'graph']) {
    const panel = panels[name], question = panel?.lastQuestion;
    if (!question) continue;
    const message = element('div', 'message assistant');
    message.append(element('p', '', `${kind}이 원장에 반영됐습니다. 그래프 준비 후 같은 질문을 최신 기준으로 다시 보낼 수 있습니다. 이전 답변은 당시 버전으로 남습니다.`),
      button('같은 질문 다시 실행', () => fillQuestion(question, name), 'btn sm'));
    panel.messages.append(message);
  }
}
async function showBomGraph() {
  // Open the graph only after the posted version's projection is ready; never
  // show the newer ledger with an older graph.
  location.hash = '#graph';
  for (let attempt = 0; attempt < 90 && identity; attempt++) {
    try {
      const changes = await api(`/api/mks2/events?after=${encodeURIComponent(snapshot.meta.ledger_version)}`);
      if (changes.current_ledger_version === snapshot.meta.ledger_version) break;
      if (changes.projection_state === 'READY' && changes.snapshot_ledger_version === changes.current_ledger_version) { await refreshSnapshot(); break; }
      lane?.setLag(`원장 v${changes.current_ledger_version} 반영 · 그래프 갱신 중 (표시 v${snapshot.meta.projection_version})`);
    } catch (error) { if (error.status === 401) return; }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  lane?.setLag(null);
  const configuration = (snapshot?.entities || []).find(entity => entity.type === 'ProductConfiguration' && entity.display_id === snapshot.summary?.configuration_id);
  location.hash = '#graph';
  if (configuration) requestAnimationFrame(() => lane?.focus(configuration.id));
}
function updateSuggestions(container, name) {
  container.replaceChildren();
  const questions = snapshot.suggested_questions || snapshot.suggestions || [];
  for (const question of questions) if (typeof question === 'string') container.append(button(question, () => fillQuestion(question, name), 'suggestion'));
}
function clearAgentWaiting(panel) {
  if (!panel) return;
  for (const control of panel.waiting?.controls || []) control.disabled = true;
  panel.waiting = null;
  panel.pending = null;
  panel.input.placeholder = '질문하면 같은 버전의 원장 근거를 조회합니다';
}
function agentSnapshotMatches(state) {
  const basis = state?.snapshot, meta = snapshot?.meta;
  return !!basis && !!meta && !!identity
    && basis.context_revision === identity.identity_revision
    && basis.company_id === identity.company_id && basis.company_id === meta.company_id
    && basis.read_snapshot_id === meta.snapshot_id
    && basis.ledger_version === meta.ledger_version && basis.projection_version === meta.projection_version;
}
function currentAgentWaiting(panel, waiting) {
  return !!waiting && panel.waiting === waiting && waiting.generation === generation
    && waiting.state.status === 'WAITING' && agentSnapshotMatches(waiting.state);
}
function createPanel(name) {
  settleAnswer(panels[name], true);
  clearAgentWaiting(panels[name]);
  const root = $(`${name}-panel`); root.replaceChildren();
  const heading = element('div', 'panel-heading');
  heading.append(element('h2', '', name === 'dashboard' ? '업무 에이전트' : name === 'network' ? '프로젝트 그래프 질의' : '업무 AI 질의'), element('span', 'tag', '원장 근거 조회'));
  const context = element('div', 'panel-context', `원장 v${snapshot.meta.ledger_version}`);
  const messages = element('div', 'messages');
  messages.setAttribute('aria-live', 'polite'); messages.setAttribute('aria-relevant', 'additions');
  const greeting = element('div', 'message assistant');
  greeting.append(element('p', '', '질문하면 현재 원장의 수량과 확인된 관계를 조회하고 근거 경로를 보여드립니다.'),
    element('p', 'note', '확인되지 않은 장착·생산·승인·입하 정보는 미확인으로 남깁니다.'));
  messages.append(greeting);
  const suggestions = element('div', 'suggestions');
  updateSuggestions(suggestions, name);
  const form = element('form', 'question-form'), input = element('input');
  input.autocomplete = 'off'; input.setAttribute('aria-label', name === 'dashboard' ? '에이전트에게 질문' : '질문');
  input.placeholder = '질문하면 같은 버전의 원장 근거를 조회합니다';
  const submit = element('button', 'btn pri', name === 'dashboard' ? '실행' : '보내기'); submit.type = 'submit';
  form.append(input, submit);
  form.addEventListener('submit', event => {
    event.preventDefault(); const question = input.value.trim();
    if (!question || submit.disabled) return;
    input.value = ''; runQuery(name, question);
  });
  const status = element('div', 'panel-status'); status.setAttribute('role', 'status');
  root.append(heading, context, status, messages, suggestions, form,
    element('div', 'disclosure', `${snapshot.material_requirements?.bom_id ? '실제 BOM 원문 · 합성 초기 재고' : '합성 초기 이관 원장'}${snapshot.meta.data_origins?.inventory === 'mixed_sources' ? ' · 개발 입하·검사 반영' : ''} · 결정적 업무 조회. 실제 모델 호출·구매·승인 실행은 구분합니다.`));
  panels[name] = { root, context, messages, suggestions, input, submit, status, topics: new Set(), pending: null, waiting: null, presentation: null };
}
function productInput() {
  const bom = snapshot.bom;
  return bom ? { ledger_version: snapshot.meta.ledger_version, bom_id: bom.bom_id,
    bom_version: bom.bom_version, payload_hash: bom.payload_hash } : undefined;
}
async function agentQuery(panel, question, message, requestGeneration, contextEntityId) {
  let state;
  const key = crypto.randomUUID();
  const waiting = panel.waiting;
  if (panel.pending && (!currentAgentWaiting(panel, waiting)
      || panel.pending.execution_id !== waiting.state.execution_id
      || panel.pending.revision !== waiting.state.revision)) clearAgentWaiting(panel);
  const pending = panel.pending;
  if (pending?.capability) {
    state = pending;
    waiting.inflight = true;
    for (const control of waiting.controls) control.disabled = true;
  } else {
    // A new question supersedes unselected clarification buttons, while their
    // original answer text remains in the conversation.
    clearAgentWaiting(panel);
    state = await api('/api/mks2/agent/executions', { question,
      ledger_version: snapshot.meta.ledger_version, context_entity_id: contextEntityId || undefined,
      idempotency_key: key });
  }
  if (generation !== requestGeneration) return null;
  const base = `/api/mks2/agent/executions/${encodeURIComponent(state.execution_id)}`;
  let cursor = 0, reading = false, finished = false;
  const labels = { created: '실행 준비', route: '조회 기능 선택', validate: '권한과 기준 확인',
    fulfillment_read: '이행 수량 조회', material_analysis: '소요·부족 조회',
    product_review: 'BOM 품목 후보 검토', procurement_review: '부족량·견적 비교',
    tool: '원장 근거 조회', validation: '결과 확인', completed: '조회 완료' };
  const progress = async () => {
    if (reading || finished || generation !== requestGeneration) return;
    reading = true;
    try {
      const response = await fetch(`${base}/events?after=${cursor}`, { credentials: 'same-origin',
        cache: 'no-store', signal: requests.signal,
        headers: { 'X-Identity-Revision': identity?.identity_revision || '' } });
      if (!response.ok) return;
      const stream = await response.text();
      for (const line of stream.split('\n')) if (line.startsWith('data: ')) {
        const event = JSON.parse(line.slice(6));
        cursor = Math.max(cursor, event.sequence || 0);
        if (!finished && generation === requestGeneration) message.progress?.mark(event.stage, event.status);
      }
    } catch { /* The authoritative run response handles connection/auth errors. */ }
    finally { reading = false; }
  };
  const timer = setInterval(progress, 750);
  try {
    state = pending?.capability
      ? await api(`${base}/resume`, { question, capability: pending.capability,
        product: pending.capability === 'product_review' ? productInput() : undefined,
        expected_revision: pending.revision, idempotency_key: key })
      : await api(`${base}/run`, {});
  } catch (error) {
    if (pending && panel.waiting === waiting) {
      // Busy/projection errors can be retried. An obsolete revision, policy,
      // snapshot, terminal execution or expired deadline needs a new question.
      const retryableConflict = ['AGENT_EXECUTION_BUSY', 'PROJECTION_NOT_READY'].includes(error.message);
      if ((error.status === 409 && !retryableConflict) || [403, 404, 408].includes(error.status)) clearAgentWaiting(panel);
      else if (currentAgentWaiting(panel, waiting)) {
        waiting.inflight = false;
        for (const control of waiting.controls) control.disabled = false;
      }
    }
    throw error;
  } finally { finished = true; clearInterval(timer); }
  if (generation !== requestGeneration) return null;
  clearAgentWaiting(panel);
  if (state.status === 'WAITING') {
    if (!agentSnapshotMatches(state)) { const error = new Error('SNAPSHOT_MISMATCH'); error.status = 409; throw error; }
    const nextWaiting = { state, generation: requestGeneration, controls: [], inflight: false };
    panel.waiting = nextWaiting;
    message.replaceChildren(element('p', '', state.interrupt?.message || '조회할 업무를 확인해 주세요.'),
      element('p', 'note', '업무를 선택하고 질문을 보완하면 같은 실행을 이어갑니다. 원장 반영은 없습니다.'));
    for (const capability of state.interrupt?.allowed_capabilities || []) {
      const capabilityLabels = { fulfillment_read: '이행·생산 수량', material_analysis: '소요·부족 수량',
        product_review: 'BOM 품목 후보 검토', procurement_review: '부족량·견적 비교' };
      if (!capabilityLabels[capability]) continue;
      const control = button(capabilityLabels[capability], () => {
        if (!currentAgentWaiting(panel, nextWaiting) || nextWaiting.inflight || panel.submit.disabled) return;
        panel.pending = { ...state, capability };
        panel.input.placeholder = '선택한 업무에 맞게 질문을 보완해 주세요'; panel.input.focus();
      });
      nextWaiting.controls.push(control);
      message.append(control);
    }
    return null;
  }
  if (!state.result) {
    message.replaceChildren(element('p', '', state.status === 'CANCELLED' ? '실행이 취소되었습니다.' : '업무 조회를 완료하지 못했습니다.'),
      element('p', 'note', `상태 · ${state.error_code || state.status} · 원장 변경 없음`));
    return null;
  }
  return { ...state.result, agent_execution: { execution_id: state.execution_id,
    status: state.status, mode: state.model?.mode, live_calls: state.model?.live_calls || 0,
    fake_calls: state.model?.fake_calls || 0, tool_calls: state.tool_calls } };
}
async function runQuery(name, question) {
  const panel = panels[name], requestGeneration = generation;
  settleAnswer(panel);
  panel.messages.append(element('div', 'message user', question)); panel.submit.disabled = true; panel.lastQuestion = question;
  const message = element('div', 'message assistant'); panel.messages.append(message);
  const agentPath = agentMode !== 'disabled' && name !== 'twin';
  message.progress = startProgress(message, agentPath);
  let followAnswer = true, presentationOwnsScroll = false;
  const trackScroll = () => {
    if (Number.isFinite(panel.messages.scrollHeight)) {
      followAnswer = panel.messages.scrollHeight - panel.messages.scrollTop - panel.messages.clientHeight <= 40;
    }
  };
  const scrollAnswer = () => {
    if (followAnswer && answerVisible(name)) panel.messages.scrollTop = panel.messages.scrollHeight;
  };
  const releaseScroll = () => panel.messages.removeEventListener?.('scroll', trackScroll);
  scrollAnswer(); panel.messages.addEventListener('scroll', trackScroll, { passive: true });
  cancelPendingReplay();
  try {
    // With a model connected the graph, dashboard and project-graph assistants
    // take the agent path. The digital twin keeps the read API its evidence
    // highlight is built on; every screen uses it when the model is off.
    const context = name === 'twin' ? twinContextId : contextId;
    const answer = agentPath
      ? await agentQuery(panel, question, message, requestGeneration, context)
      : await api('/api/mks2/query', { question, ledger_version: snapshot.meta.ledger_version,
        context_entity_id: context || undefined });
    if (!answer) return;
    if (generation !== requestGeneration) return;
    if (answer.meta?.identity_revision !== identity?.identity_revision || answer.meta?.company_id !== identity?.company_id) {
      identity = null; csrfToken = null; invalidateView(); throw new Error('IDENTITY_CHANGED');
    }
    if (!answerSnapshotMatches(answer)) {
      const error = new Error('SNAPSHOT_MISMATCH'); error.status = 409; throw error;
    }
    const hops = answer.graph_path || [], ids = [...new Set([...hops.flat(), ...(answer.evidence_node_ids || [])])];
    // Dependency of this conversation on calculated items; used only to decide
    // whether a later committed event is relevant here (no model call).
    if ((answer.intent || []).some(intent => ['materials', 'delivery', 'change', 'sourcing', 'inventory'].includes(intent))) {
      const scoped = answer.calculation?.scope_item_ids?.length ? answer.calculation.scope_item_ids
        : (answer.calculation?.material_requirements?.items || snapshot.material_requirements?.items || []).map(item => item.item_id);
      for (const item of scoped) panel.topics.add(item);
    }
    if ((answer.intent || []).some(intent => ['delivery', 'fulfillment', 'trace'].includes(intent)) && snapshot.summary?.delivery_round_id) {
      panel.topics.add(`round:${snapshot.summary.delivery_round_id}`);
    }
    twin?.highlight({ nodeIds: ids, edgeIds: answer.evidence_edge_ids || [],
      ledgerVersion: answer.meta.ledger_version, projectionVersion: answer.meta.projection_version,
      snapshotId: answer.meta.snapshot_id ?? answer.meta.read_snapshot_id,
      companyId: answer.meta.company_id, identityRevision: answer.meta.identity_revision });
    const evidenceAtVersion = action => {
      if (!answerSnapshotMatches(answer)) {
        // A button on an old answer never sends a question by itself (no
        // side-effect model call); the question waits in the input instead.
        const filled = offerSameQuestion(name, question);
        notify(filled ? '과거 원장 버전의 답변입니다. 같은 질문을 아래 입력창에 넣었습니다. 보내면 최신 원장으로 다시 답합니다.'
          : '과거 원장 버전의 답변입니다. 최신 근거가 필요하면 질문을 다시 실행해 주세요.');
        return;
      }
      return action();
    };
    let activeReplay = null;
    const replayEvidence = (navigate = false) => evidenceAtVersion(() => {
      cancelPendingReplay();
      const network = name === 'network';
      if (network && schemaMode) { schemaMode = false; mountCurrentGraph(); }
      if (navigate) location.hash = network ? '#network' : '#graph';
      const replay = ++replayGeneration, playGeneration = generation;
      activeReplay = replay;
      const target = network ? graph : lane;
      return presentation.play(() => target.replay({ hops, nodeIds: ids, edgeIds: answer.evidence_edge_ids || [] }),
        () => generation === playGeneration && replay === replayGeneration);
    });
    const animateAnswer = answerVisible(name);
    const waitForGraph = (name === 'graph' || name === 'network') && ids.length > 0 && animateAnswer
      && !globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      && typeof Intl.Segmenter === 'function' && typeof requestAnimationFrame === 'function'
      && typeof cancelAnimationFrame === 'function';
    message.progress?.stop();
    const presentation = renderQueryAnswer(message, answer, {
      animate: animateAnswer, mode: name === 'dashboard' ? 'workflow' : 'graph',
      beforeBody: waitForGraph ? () => replayEvidence() : undefined,
      onProgress: scrollAnswer,
      evidenceIds: ids, labelFor, evidenceEdges: snapshot.graph.data.edges,
      evidenceNodes: snapshot.graph.data.nodes,
      onEvidence: id => evidenceAtVersion(() => focusEvidence(id)),
      onOpenBom: () => evidenceAtVersion(() => { settleAnswer(panel); cancelPendingReplay(); dashboard.openBom(0); }),
      onReviewBom: reference => evidenceAtVersion(() => {
        const expected = answer.prerequisite?.bom, requirements = answer.calculation?.material_requirements;
        if (!expected || !reference || expected.document_id !== reference.document_id
            || expected.version !== reference.version || expected.payload_hash !== reference.payload_hash
            || requirements?.bom_id !== reference.document_id || requirements.bom_version !== reference.version
            || answer.prerequisite.configuration_id !== snapshot.summary.configuration_id) return;
        settleAnswer(panel); cancelPendingReplay();
        dashboard.openBom(4, reference.document_id, { ...reference });
      }),
      onChooseQuote: quote => evidenceAtVersion(() => {
        settleAnswer(panel); cancelPendingReplay();
        dashboard.startPurchaseRequest(quote).then(opened => {
          if (opened === 'APPROVER_CANNOT_AUTHOR') setStatus([name], ['승인 권한이 있는 계정은 구매 요청을 작성하지 않습니다. 작성하면 승인할 다른 계정이 없습니다. 상단에서 작성자 계정으로 바꿔 주세요.']);
          else if (!opened) setStatus([name], ['이 견적으로 구매 요청 초안을 열 수 없습니다. 권한·견적 상태를 확인해 주세요.']);
          else location.hash = '#dashboard';
        });
      }),
      onReplay: () => replayEvidence(true),
      onCancel: () => { if (activeReplay === replayGeneration) cancelPendingReplay(); },
    });
    panel.presentation = presentation;
    presentationOwnsScroll = true;
    // Text display is independent of the request and graph animation. A late
    // completion can release only its own view, never a newer answer's view.
    presentation.finished?.then(() => {
      releaseScroll();
      if (panel.presentation === presentation) panel.presentation = null;
    }, () => {
      releaseScroll();
      presentation.finish?.();
      if (panel.presentation === presentation) panel.presentation = null;
    });
    if (ids.length && name !== 'dashboard' && !waitForGraph) replayEvidence();
    if (answer.ai_summary?.status === 'PENDING' && answer.agent_execution?.execution_id) {
      // The ledger answer is already shown; the AI summary card fills in when written.
      const revision = identity?.identity_revision;
      api(`/api/mks2/agent/executions/${encodeURIComponent(answer.agent_execution.execution_id)}/summary`, {})
        .then(result => { if (identity?.identity_revision === revision) updateAiSummary(message, result.ai_summary); })
        .catch(() => { if (identity?.identity_revision === revision) updateAiSummary(message, { status: 'UNAVAILABLE' }); });
    }
  } catch (error) {
    if (error.name === 'AbortError' || generation !== requestGeneration) return;
    message.replaceChildren(element('p', '', errorText(error)));
    if (error.status === 401) invalidateView();
  } finally {
    message.progress?.stop();
    if (!presentationOwnsScroll) releaseScroll();
    if (generation === requestGeneration) {
      panel.submit.disabled = false;
      scrollAnswer();
    }
  }
}
function answerSnapshotMatches(answer) {
  const meta = answer?.meta, current = snapshot?.meta;
  return !!meta && !!current && !!identity
    && meta.identity_revision === identity.identity_revision && meta.company_id === identity.company_id
    && (meta.snapshot_id ?? meta.read_snapshot_id) === current.snapshot_id
    && meta.ledger_version === current.ledger_version && meta.projection_version === current.projection_version
    && meta.ontology_version === current.ontology_version;
}
function mountCurrentGraph() {
  cancelPendingReplay(); graph?.destroy();
  const selected = schemaMode ? ontology.graph : snapshot.graph;
  graph = mountGraph($('network-stage'), { bundle: { graph: selected }, onSelect: setContext,
    onAsk: fillQuestion, onBom: () => dashboard.openBom() });
  const controls = $('network-stage').querySelector('.mks-graph-tools');
  const switcher = button(schemaMode ? '업무 원장 보기' : '전체 온톨로지', () => { schemaMode = !schemaMode; mountCurrentGraph(); }, 'mks-graph-button');
  switcher.dataset.action = 'schema'; switcher.setAttribute('aria-pressed', String(schemaMode));
  controls.prepend(switcher);
}
function invalidateView() {
  for (const panel of Object.values(panels)) { settleAnswer(panel, true); clearAgentWaiting(panel); }
  generation++; requests.abort(); requests = new AbortController(); cancelPendingReplay();
  graph?.destroy(); lane?.destroy(); dashboard?.destroy(); twin?.destroy(); graph = null; lane = null; dashboard = null; twin = null; snapshot = null; ontology = null; twinContextId = null;
  handledEvents.clear();
  for (const name of SCREENS) { $(`${name}-view`).hidden = true; $(`${name}-panel`).replaceChildren(); }
  $('load-state').hidden = false; $('load-state').textContent = '서버의 개발 계정을 선택해 원장을 조회하세요.';
  $('versions').textContent = '계정 확인 필요'; latestVersion = null; refreshButton.hidden = true;
}
async function loadSnapshot({ preserveMessages = false } = {}) {
  const requestGeneration = generation;
  $('load-state').hidden = false; $('load-state').textContent = '같은 버전의 원장과 전체 온톨로지를 조회하고 있습니다.';
  $('workspace').setAttribute('aria-busy', 'true');
  const [data, schema] = await Promise.all([api('/api/mks2/snapshot'), api('/api/mks2/ontology')]);
  if (generation !== requestGeneration) return;
  if (data.meta?.identity_revision !== identity?.identity_revision || schema.meta?.identity_revision !== identity?.identity_revision
    || data.meta?.company_id !== identity?.company_id || schema.meta?.company_id !== identity?.company_id) {
    identity = null; csrfToken = null; invalidateView();
    throw new Error('IDENTITY_CHANGED');
  }
  if (data.meta?.projection_state !== 'READY') throw new Error('PROJECTION_PENDING');
  if (!data.meta || !data.graph?.data || data.meta.ledger_version !== data.meta.projection_version
    || data.graph.meta?.ledger_version !== data.meta.ledger_version || data.graph.meta?.projection_version !== data.meta.projection_version) {
    const error = new Error('SNAPSHOT_MISMATCH'); error.status = 409; throw error;
  }
  if (!schema.graph?.data?.nodes || !schema.graph?.data?.edges
    || schema.meta?.ontology_version !== data.meta.ontology_version) throw new Error('ONTOLOGY_UNAVAILABLE');
  snapshot = data; ontology = schema; schemaMode = false;
  $('versions').replaceChildren(document.createTextNode('원장 '), element('b', '', `v${data.meta.ledger_version}`),
    document.createTextNode(' · 그래프 '), element('b', '', `v${data.meta.projection_version}`), element('span', 'pill ok', '서버 연결'));
  $('versions').title = `원천 버전 ${data.meta.source_ledger_version ?? '미확인'} · ${data.meta.snapshot_id ?? ''}`;
  if (!preserveMessages) { for (const name of SCREENS) createPanel(name); }
  else for (const [name, panel] of Object.entries(panels)) {
    clearAgentWaiting(panel);
    panel.context.textContent = `현재 원장 v${data.meta.ledger_version} · 이전 답변은 각 답변의 기준 버전을 유지합니다.`;
    panel.submit.disabled = false;
    updateSuggestions(panel.suggestions, name);
  }
  refreshButton.hidden = false; refreshButton.disabled = false; refreshButton.textContent = '최신 원장 조회'; latestVersion = data.meta.ledger_version;
  dashboard?.destroy();
  dashboard = mountDashboard($('dashboard-stage'), { snapshot, onSelect: setContext,
    onAsk: text => fillQuestion(text, 'dashboard'), onGraph: focusEvidence, api, getIdentity: () => identity,
    onShowBomGraph: showBomGraph,
    onPosted: posted => { if (posted?.status === 'POSTED' && posted?.content?.rows) offerRerun('BOM'); refreshButton.textContent = '업무 반영 · 최신 원장 조회'; notify(liveEnabled ? '업무 사건이 원장에 반영됐습니다. 그래프 투영 후 화면이 자동으로 갱신됩니다.' : '업무 사건이 반영됐습니다. 그래프 준비 후 최신 원장을 조회해 주세요.'); pollChanges(); },
    onRefresh: refreshSnapshot, live: { get: () => liveEnabled, set: value => { liveEnabled = !!value; notify(liveEnabled ? '확정 원장 사건을 자동으로 반영합니다.' : '자동 반영을 끕니다. 새 원장은 버튼으로 조회합니다.'); } } });
  lane?.destroy();
  lane = mountLaneGraph($('graph-stage'), { data: { meta: data.meta, nodes: data.entities || [], edges: data.relationships || [] },
    summary: data.summary || {}, itemOrder: (data.material_requirements?.items || []).map(item => item.item_id),
    activeBom: data.bom ? { bom_id: data.bom.bom_id, bom_version: data.bom.bom_version } : null,
    onSelect: setContext, onBom: () => dashboard.openBom() });
  mountCurrentGraph();
  twin?.destroy();
  twin = mountTwin($('twin-stage'), { snapshot, ontology, labelFor, onSelect: setTwinContext,
    onAsk: text => fillQuestion(text, 'twin'), onGraph: focusEvidence });
  const initialNode = data.graph.data.nodes.find(node => node.entity_ref?.entity_type === 'delivery_round' || node.type === 'DeliveryRound');
  const initialContext = initialNode?.node_id || initialNode?.id;
  if (initialContext) setContext(initialContext);
  if (!data.graph.data.nodes.some(node => (node.node_id || node.id) === twinContextId)) twinContextId = initialContext;
  if (twinContextId) setTwinContext(twinContextId);
  $('load-state').hidden = true; $('workspace').setAttribute('aria-busy', 'false'); route();
}
async function refreshSnapshot() {
  if (!identity || refreshButton.disabled) return;
  for (const panel of Object.values(panels)) { settleAnswer(panel); clearAgentWaiting(panel); }
  generation++; requests.abort(); requests = new AbortController(); cancelPendingReplay();
  refreshButton.disabled = true;
  try { await loadSnapshot({ preserveMessages: true }); }
  catch (error) { if (error.name !== 'AbortError') { $('load-state').textContent = errorText(error); $('load-state').hidden = false; $('workspace').setAttribute('aria-busy', 'false'); } }
  finally { refreshButton.disabled = false; }
}
// Realtime reflection of committed ledger events (DEC-H47). Quantities come
// only from a newly read server snapshot of the same version as its graph;
// relevance uses each conversation's calculated items. No model call is made.
function canAutoApply() {
  const active = document.activeElement;
  if (document.querySelector('.mk-drawer-wrap') || document.querySelector('[data-purchase-form]')) return false;
  if (active?.closest?.('#dashboard-stage') && active.matches?.('input,select,textarea')) return false;
  // A finished answer still being typed out is not user input; refreshSnapshot
  // completes its text first. Only an in-flight question defers the refresh.
  return !Object.values(panels).some(panel => panel.submit?.disabled);
}
function relevantPanels(items) {
  return Object.entries(panels).filter(([, panel]) => items.some(item => panel.topics.has(item))).map(([name]) => name);
}
function setStatus(names, parts) {
  for (const name of names) {
    const panel = panels[name];
    if (!panel) continue;
    const line = element('div', 'mk-status-line');
    for (const part of parts) line.append(typeof part === 'string' ? document.createTextNode(part) : part);
    panel.status.replaceChildren(line);
  }
}
function reservationEvents(events) {
  return (events || []).filter(event => event.document_kind === 'RESERVATION' && event.reservation && typeof event.event_id === 'string');
}
function announceApplied(events, startedAt) {
  const impacts = snapshot.reservation_impacts || [];
  const byReservation = new Map(impacts.map(row => [row.reservation_id, row]));
  for (const event of events) {
    const impact = byReservation.get(event.reservation.reservation_id);
    if (!impact) continue;
    const names = relevantPanels([impact.item_id]);
    const unit = impact.unit === 'EA' ? '개' : ` ${impact.unit}`;
    const question = `왜 부족이 ${impact.planned_shortage_after}개로 늘었어?`;
    const grew = Number(impact.planned_shortage_after) > Number(impact.planned_shortage_before);
    const latency = Date.parse(event.recorded_at) ? Date.now() - Date.parse(event.recorded_at) : null;
    setStatus(names, [element('b', '', '근거 변경'),
      ` 관련 재고 변경 · 다른 주문 ${impact.demand_ref} 예약 +${impact.quantity}${unit} · 가용 ${impact.available_before}→${impact.available_after} · `
        + (grew ? `계획 부족 ${impact.planned_shortage_before}→${impact.planned_shortage_after}${unit} · 기존 확보안 재검토 필요` : `계획 부족 ${impact.planned_shortage_after}${unit} 유지`)
        + ` · 원장 v${impact.ledger_version} `,
      button('변경 관계 보기', () => focusEvidence(impact.entity_id), 'btn sm'),
      ...(grew ? names.flatMap(name => [
        panels[name].lastQuestion ? button('같은 질문 다시 하기', () => askNow(name, panels[name].lastQuestion), 'suggestion') : null,
        button(question, () => fillQuestion(question, name), 'suggestion')]) : [])].filter(Boolean));
    if (grew) {
      for (const name of names) offerSameQuestion(name);
      markEarlierAnswers(names, impact.ledger_version);
    }
    for (const name of names) {
      panels[name].status.dataset.eventId = event.event_id;
      if (latency !== null) panels[name].status.dataset.latencyMs = String(latency);
      panels[name].status.dataset.applyMs = String(Date.now() - startedAt);
    }
  }
}
// Earlier answers stay exactly as answered; only a version tag says they need a re-check.
function markEarlierAnswers(names, version) {
  for (const name of names) {
    for (const message of panels[name].messages.querySelectorAll('[data-ledger-version]')) {
      if (Number(message.dataset.ledgerVersion) >= version || message.querySelector('.mk-answer-stale')) continue;
      message.prepend(element('div', 'mk-answer-stale', `원장 v${message.dataset.ledgerVersion} 기준 답변 · 재검토 필요`));
    }
  }
}
// Arrival: the round's own quantities changed; quote server summaries only.
function announceArrival(before, topic, event) {
  const after = snapshot.summary || {};
  const names = relevantPanels([topic]);
  if (!names.length || after.arrived_qty === before.arrived_qty) return;
  const n = after.arrived_qty - before.arrived_qty;
  setStatus(names, [element('b', '', '현재 대화의 근거 변경'),
    ` 고객 도착 ${n}대 반영 · 누적 도착 ${before.arrived_qty}→${after.arrived_qty}대 · 남은 약속 ${before.promise_remaining_qty}→${after.promise_remaining_qty}대 · 원장 v${snapshot.meta.ledger_version} `,
    button('원장 사건 보기', () => { location.hash = '#dashboard'; document.querySelector(`[data-ledger-event-id="${event.event_id}"] details`)?.setAttribute('open', ''); }, 'btn sm'),
    ...names.map(name => button('최신 기준으로 재분석', () => fillQuestion(panels[name].lastQuestion || `남은 ${after.promise_remaining_qty}대, 납기 안에 고객에게 도착할 수 있어?`, name), 'suggestion'))]);
}
async function pollChanges() {
  updateTwinActivity();
  if (polling || !snapshot || !identity || document.hidden || (window.frameElement && !window.frameElement.getBoundingClientRect().height)) return;
  polling = true; const requestGeneration = generation;
  try {
    const changes = await api(`/api/mks2/events?after=${encodeURIComponent(snapshot.meta.ledger_version)}`);
    if (generation !== requestGeneration) return;
    if (changes.identity_revision !== identity.identity_revision || changes.company_id !== identity.company_id) {
      identity = null; csrfToken = null; invalidateView();
      $('load-state').textContent = '계정 권한이 변경되었습니다. 계정을 다시 선택해 주세요.'; return;
    }
    if (!(changes.current_ledger_version > snapshot.meta.ledger_version)) return;
    latestVersion = changes.current_ledger_version; refreshButton.textContent = `새 원장 v${latestVersion} 조회`; refreshButton.hidden = false;
    const reserved = reservationEvents(changes.events).filter(event => !handledEvents.has(event.event_id));
    const arrivals = (changes.events || []).filter(event => event.document_kind === 'ARRIVAL' && !handledEvents.has(event.event_id));
    const roundTopic = `round:${snapshot.summary?.delivery_round_id}`;
    const calculated = new Set((snapshot.material_requirements?.items || []).map(item => item.item_id));
    const relevant = reserved.filter(event => calculated.has(event.reservation.item_id));
    const ready = changes.projection_state === 'READY' && changes.snapshot_ledger_version === changes.current_ledger_version;
    if (!ready) {
      // Do not mix the newer ledger quantity with the older graph.
      lane?.setLag(`원장 v${changes.current_ledger_version} 반영 · 그래프 갱신 중 (표시 v${snapshot.meta.projection_version})`);
      for (const event of relevant) setStatus(relevantPanels([event.reservation.item_id]), [element('b', '', '원장 사건 확인'),
        ` ${event.summary} · 수량·그래프 갱신 중 · 현재 표시 원장 v${snapshot.meta.ledger_version}`]);
      for (const event of arrivals) setStatus(relevantPanels([roundTopic]), [element('b', '', '원장 사건 확인'),
        ` ${event.summary} · 수량 갱신 완료 전 · 그래프 갱신 중`]);
      return;
    }
    if (!liveEnabled || !canAutoApply()) {
      for (const event of arrivals) setStatus(relevantPanels([roundTopic]), [element('b', '', '근거 변경 대기'),
        ` ${event.summary} · 작성·답변 중이라 자동 갱신을 미뤘습니다 `, button('최신 기준으로 보기', refreshSnapshot, 'btn sm')]);
      for (const event of relevant) setStatus(relevantPanels([event.reservation.item_id]), [element('b', '', '근거 변경 대기'),
        ` ${event.summary} · 작성·답변 중이라 자동 갱신을 미뤘습니다 `, button('최신 기준으로 보기', refreshSnapshot, 'btn sm')]);
      return;
    }
    const startedAt = Date.now(), before = { ...snapshot.summary };
    await refreshSnapshot();
    if (generation === requestGeneration + 1 && snapshot?.meta.ledger_version === changes.current_ledger_version) {
      lane?.setLag(null);
      for (const event of [...reserved, ...arrivals]) handledEvents.add(event.event_id);
      announceApplied(relevant, startedAt);
      if (arrivals.length) announceArrival(before, roundTopic, arrivals.at(-1));
    }
  } catch (error) {
    if (error.status === 401) { identity = null; csrfToken = null; invalidateView(); }
  } finally { polling = false; }
}
setInterval(pollChanges, 3000);
async function selectIdentity(profileId) {
  invalidateView(); $('actor').disabled = true;
  try {
    const auth = await api('/api/mks2/session', { profile_id: profileId });
    identity = auth.principal; csrfToken = auth.csrf_token;
    await loadSnapshot();
  } catch (error) {
    if (error.name !== 'AbortError') { $('load-state').textContent = errorText(error); $('workspace').setAttribute('aria-busy', 'false'); }
  } finally { $('actor').disabled = false; }
}
async function signOut() {
  invalidateView(); $('actor').disabled = true;
  try {
    await api('/api/mks2/session', {}, 'DELETE');
    identity = null; csrfToken = null; $('actor').value = '';
    $('load-state').textContent = '세션을 종료했습니다. 개발 계정을 선택하면 다시 조회할 수 있습니다.';
  } catch (error) {
    if (error.status === 401) { identity = null; csrfToken = null; $('load-state').textContent = '세션이 종료되었습니다.'; }
    else { $('load-state').replaceChildren(element('p', '', '서버에서 세션 종료를 확인하지 못했습니다.'), button('세션 종료 다시 시도', signOut)); }
  } finally { $('actor').disabled = false; }
}
async function start() {
  try {
    const health = await api('/health'); agentMode = health.agent?.mode || 'disabled';
    const state = await api('/api/mks2/session'); identity = state.principal; csrfToken = state.csrf_token;
    const prompt = element('option', '', '계정 선택 / 세션 종료'); prompt.value = '';
    $('actor').replaceChildren(prompt, ...(state.profiles || []).map(profile => {
      const option = element('option', '', profile.label || profile.id); option.value = profile.id; return option;
    }));
    $('actor').disabled = false;
    $('actor').value = state.profile_id || identity?.profile_id || '';
    if (state.authenticated && identity) await loadSnapshot();
    else { $('load-state').textContent = '위의 개발 계정을 선택하면 해당 회사와 권한으로 원장을 조회합니다.'; $('workspace').setAttribute('aria-busy', 'false'); $('versions').textContent = '계정 선택 대기'; }
  } catch (error) {
    $('load-state').textContent = errorText(error);
    $('workspace').setAttribute('aria-busy', 'false'); $('versions').textContent = '서비스 미연결';
  }
}
$('actor').addEventListener('change', () => {
  if ($('actor').value) selectIdentity($('actor').value);
  else if (identity) signOut();
});
window.addEventListener('hashchange', route);
window.addEventListener('resize', updateTwinActivity);
document.addEventListener('visibilitychange', updateTwinActivity);
if (typeof ResizeObserver === 'function') new ResizeObserver(updateTwinActivity).observe($('workspace'));
$('theme').addEventListener('click', () => {
  const dark = document.documentElement.dataset.theme !== 'dark'; document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  $('theme').setAttribute('aria-label', dark ? '밝은 테마로 전환' : '어두운 테마로 전환');
});
$('reset').addEventListener('click', () => {
  if (!snapshot) return;
  generation++; requests.abort(); requests = new AbortController();
  cancelPendingReplay(); graph.reset(); lane?.reset(); dashboard.select(null); twin?.reset(); twinContextId = null;
  for (const name of SCREENS) createPanel(name);
  notify('화면 선택과 대화를 초기화했습니다. 원장은 유지됩니다.');
});
start();
