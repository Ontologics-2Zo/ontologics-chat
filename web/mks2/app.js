import { mountDashboard } from './dashboard.js';
import { mountGraph } from './graph.js';
import { renderQueryAnswer } from './query.js';

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
let snapshot, ontology, graph, dashboard, identity, csrfToken;
let contextId, noticeTimer, generation = 0, replayGeneration = 0, schemaMode = false;
let requests = new AbortController();
let polling = false, latestVersion = null;
let agentMode = 'disabled';
const refreshButton = button('최신 원장 조회', refreshSnapshot);
refreshButton.id = 'refresh-ledger'; refreshButton.hidden = true;
$('reset').before(refreshButton);
const panels = {};
const currentScreen = () => location.hash === '#dashboard' ? 'dashboard' : 'graph';
const labelFor = id => snapshot?.graph?.data?.nodes?.find(node => (node.node_id || node.id) === id)?.display_id || id;
function notify(text) {
  $('notice').textContent = text; $('notice').hidden = false;
  clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $('notice').hidden = true; }, 4500);
}
function cancelPendingReplay() { replayGeneration++; graph?.cancelReplay(); }
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
  for (const name of ['graph', 'dashboard']) {
    $(`${name}-view`).hidden = currentScreen() !== name;
    $(`tab-${name}`).setAttribute('aria-current', currentScreen() === name ? 'page' : 'false');
  }
  if (currentScreen() === 'graph') requestAnimationFrame(() => graph?.resize());
}
function setContext(value) {
  contextId = typeof value === 'string' ? value : value?.node_id ?? value?.id;
  if (!contextId) return;
  for (const panel of Object.values(panels)) {
    panel.context.replaceChildren(document.createTextNode('맥락 '), button(labelFor(contextId), () => focusEvidence(contextId), 'chip'),
      document.createTextNode(` 원장 v${snapshot.meta.ledger_version}`));
  }
}
function focusEvidence(id) {
  cancelPendingReplay();
  if (schemaMode) { schemaMode = false; mountCurrentGraph(); }
  location.hash = '#graph';
  graph?.focus(id);
}
function fillQuestion(text, name = currentScreen()) { panels[name].input.value = text; panels[name].input.focus(); }
function updateSuggestions(container, name) {
  container.replaceChildren();
  const questions = snapshot.suggested_questions || snapshot.suggestions || [];
  for (const question of questions) if (typeof question === 'string') container.append(button(question, () => fillQuestion(question, name), 'suggestion'));
}
function createPanel(name) {
  const root = $(`${name}-panel`); root.replaceChildren();
  const heading = element('div', 'panel-heading');
  heading.append(element('h2', '', name === 'graph' ? '업무 AI 질의' : '업무 에이전트'), element('span', 'tag', '원장 근거 조회'));
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
  input.autocomplete = 'off'; input.setAttribute('aria-label', name === 'graph' ? '질문' : '에이전트에게 질문');
  input.placeholder = '질문하면 같은 버전의 원장 근거를 조회합니다';
  const submit = element('button', 'btn pri', name === 'graph' ? '보내기' : '실행'); submit.type = 'submit';
  form.append(input, submit);
  form.addEventListener('submit', event => {
    event.preventDefault(); const question = input.value.trim();
    if (!question || submit.disabled) return;
    input.value = ''; runQuery(name, question);
  });
  root.append(heading, context, messages, suggestions, form,
    element('div', 'disclosure', `${snapshot.material_requirements?.bom_id ? '실제 BOM 원문 · 합성 초기 재고' : '합성 초기 이관 원장'}${snapshot.meta.data_origins?.inventory === 'mixed_sources' ? ' · 개발 입하·검사 반영' : ''} · 결정적 업무 조회. 실제 모델 호출·구매·승인 실행은 구분합니다.`));
  panels[name] = { root, context, messages, suggestions, input, submit, pending: null };
}
function productInput() {
  const bom = snapshot.bom;
  return bom ? { ledger_version: snapshot.meta.ledger_version, bom_id: bom.bom_id,
    bom_version: bom.bom_version, payload_hash: bom.payload_hash } : undefined;
}
async function agentQuery(panel, question, message, requestGeneration) {
  let state;
  const key = crypto.randomUUID();
  if (panel.pending?.capability) {
    state = panel.pending;
  } else {
    state = await api('/api/mks2/agent/executions', { question,
      ledger_version: snapshot.meta.ledger_version, context_entity_id: contextId || undefined,
      idempotency_key: key });
  }
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
        if (!finished && generation === requestGeneration) message.textContent =
          `업무 조회 진행 · ${labels[event.stage] || '서버 처리 중'} · 원장 변경 없음`;
      }
    } catch { /* The authoritative run response handles connection/auth errors. */ }
    finally { reading = false; }
  };
  const timer = setInterval(progress, 750);
  try {
    const pending = panel.pending;
    state = pending?.capability
      ? await api(`${base}/resume`, { question, capability: pending.capability,
        product: pending.capability === 'product_review' ? productInput() : undefined,
        expected_revision: pending.revision, idempotency_key: key })
      : await api(`${base}/run`, {});
  } finally { finished = true; clearInterval(timer); }
  if (generation !== requestGeneration) return null;
  panel.pending = null;
  if (state.status === 'WAITING') {
    message.replaceChildren(element('p', '', state.interrupt?.message || '조회할 업무를 확인해 주세요.'),
      element('p', 'note', '업무를 선택하고 질문을 보완하면 같은 실행을 이어갑니다. 원장 반영은 없습니다.'));
    for (const capability of state.interrupt?.allowed_capabilities || []) {
      const capabilityLabels = { fulfillment_read: '이행·생산 수량', material_analysis: '소요·부족 수량',
        product_review: 'BOM 품목 후보 검토', procurement_review: '부족량·견적 비교' };
      if (!capabilityLabels[capability]) continue;
      message.append(button(capabilityLabels[capability], () => {
        panel.pending = { ...state, capability };
        panel.input.placeholder = '선택한 업무에 맞게 질문을 보완해 주세요'; panel.input.focus();
      }));
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
  panel.messages.append(element('div', 'message user', question)); panel.submit.disabled = true;
  const message = element('div', 'message assistant', '원장과 근거를 조회하고 있습니다.'); panel.messages.append(message);
  cancelPendingReplay();
  try {
    const answer = name === 'dashboard' && agentMode !== 'disabled'
      ? await agentQuery(panel, question, message, requestGeneration)
      : await api('/api/mks2/query', { question, ledger_version: snapshot.meta.ledger_version,
        context_entity_id: contextId || undefined });
    if (!answer) return;
    if (generation !== requestGeneration) return;
    if (answer.meta?.identity_revision !== identity?.identity_revision || answer.meta?.company_id !== identity?.company_id) {
      identity = null; csrfToken = null; invalidateView(); throw new Error('IDENTITY_CHANGED');
    }
    if (answer.meta?.ledger_version !== snapshot.meta.ledger_version || answer.meta?.projection_version !== snapshot.meta.projection_version) {
      const error = new Error('SNAPSHOT_MISMATCH'); error.status = 409; throw error;
    }
    const hops = answer.graph_path || [], ids = [...new Set([...hops.flat(), ...(answer.evidence_node_ids || [])])];
    const evidenceAtVersion = action => {
      if (snapshot.meta.ledger_version !== answer.meta.ledger_version || snapshot.meta.projection_version !== answer.meta.projection_version) { notify('과거 원장 버전의 답변입니다. 최신 근거가 필요하면 질문을 다시 실행해 주세요.'); return; }
      action();
    };
    let activeReplay = null;
    const replayEvidence = (navigate = false) => evidenceAtVersion(() => {
      cancelPendingReplay();
      if (schemaMode) { schemaMode = false; mountCurrentGraph(); }
      if (navigate) location.hash = '#graph';
      const replay = ++replayGeneration, playGeneration = generation;
      activeReplay = replay;
      void presentation.play(() => graph.replay({ hops, nodeIds: ids, edgeIds: answer.evidence_edge_ids || [] }),
        () => generation === playGeneration && replay === replayGeneration);
    });
    const presentation = renderQueryAnswer(message, answer, {
      evidenceIds: ids, labelFor,
      onEvidence: id => evidenceAtVersion(() => focusEvidence(id)),
      onReplay: () => replayEvidence(true),
      onCancel: () => { if (activeReplay === replayGeneration) cancelPendingReplay(); },
    });
    // Answers and their immutable basis are visible before animation begins.
    // Sending another question can cancel playback without hiding the answer.
    if (ids.length) replayEvidence();
  } catch (error) {
    if (error.name === 'AbortError' || generation !== requestGeneration) return;
    message.replaceChildren(element('p', '', errorText(error)));
    if (error.status === 401) invalidateView();
  } finally {
    if (generation === requestGeneration) {
      panel.submit.disabled = false;
      message.scrollIntoView({ block: 'start', behavior: 'instant' });
    }
  }
}
function mountCurrentGraph() {
  cancelPendingReplay(); graph?.destroy();
  const selected = schemaMode ? ontology.graph : snapshot.graph;
  graph = mountGraph($('graph-stage'), { bundle: { graph: selected }, onSelect: setContext,
    onAsk: fillQuestion, onBom: () => dashboard.openBom() });
  const controls = $('graph-stage').querySelector('.mks-graph-tools');
  const switcher = button(schemaMode ? '업무 원장 보기' : '전체 온톨로지', () => { schemaMode = !schemaMode; mountCurrentGraph(); }, 'mks-graph-button');
  switcher.dataset.action = 'schema'; switcher.setAttribute('aria-pressed', String(schemaMode));
  controls.prepend(switcher);
}
function invalidateView() {
  generation++; requests.abort(); requests = new AbortController(); cancelPendingReplay();
  graph?.destroy(); dashboard?.destroy(); graph = null; dashboard = null; snapshot = null; ontology = null;
  for (const name of ['graph', 'dashboard']) { $(`${name}-view`).hidden = true; $(`${name}-panel`).replaceChildren(); }
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
  if (!schema.graph?.data?.nodes || !schema.graph?.data?.edges) throw new Error('ONTOLOGY_UNAVAILABLE');
  snapshot = data; ontology = schema; schemaMode = false;
  $('versions').replaceChildren(document.createTextNode('원장 '), element('b', '', `v${data.meta.ledger_version}`),
    document.createTextNode(' · 그래프 '), element('b', '', `v${data.meta.projection_version}`), element('span', 'pill ok', '서버 연결'));
  $('versions').title = `원천 버전 ${data.meta.source_ledger_version ?? '미확인'} · ${data.meta.snapshot_id ?? ''}`;
  if (!preserveMessages) { createPanel('graph'); createPanel('dashboard'); }
  else for (const [name, panel] of Object.entries(panels)) {
    panel.context.textContent = `현재 원장 v${data.meta.ledger_version} · 이전 답변은 각 답변의 기준 버전을 유지합니다.`;
    panel.submit.disabled = false;
    updateSuggestions(panel.suggestions, name);
  }
  refreshButton.hidden = false; refreshButton.disabled = false; refreshButton.textContent = '최신 원장 조회'; latestVersion = data.meta.ledger_version;
  dashboard?.destroy();
  dashboard = mountDashboard($('dashboard-stage'), { snapshot, onSelect: setContext,
    onAsk: text => fillQuestion(text, 'dashboard'), onGraph: focusEvidence, api, getIdentity: () => identity,
    onPosted: () => { refreshButton.textContent = '업무 반영 · 최신 원장 조회'; notify('업무 사건이 반영됐습니다. 그래프 준비 후 최신 원장을 조회해 주세요.'); }, onRefresh: refreshSnapshot });
  mountCurrentGraph();
  const initialNode = data.graph.data.nodes.find(node => node.entity_ref?.entity_type === 'delivery_round' || node.type === 'DeliveryRound');
  const initialContext = initialNode?.node_id || initialNode?.id;
  if (initialContext) setContext(initialContext);
  $('load-state').hidden = true; $('workspace').setAttribute('aria-busy', 'false'); route();
}
async function refreshSnapshot() {
  if (!identity || refreshButton.disabled) return;
  generation++; requests.abort(); requests = new AbortController(); cancelPendingReplay();
  refreshButton.disabled = true;
  try { await loadSnapshot({ preserveMessages: true }); }
  catch (error) { if (error.name !== 'AbortError') { $('load-state').textContent = errorText(error); $('load-state').hidden = false; $('workspace').setAttribute('aria-busy', 'false'); } }
  finally { refreshButton.disabled = false; }
}
async function pollChanges() {
  if (polling || !snapshot || !identity || document.hidden || (window.frameElement && !window.frameElement.getBoundingClientRect().height)) return;
  polling = true; const requestGeneration = generation;
  try {
    const changes = await api(`/api/mks2/events?after=${encodeURIComponent(snapshot.meta.ledger_version)}`);
    if (generation !== requestGeneration) return;
    if (changes.identity_revision !== identity.identity_revision || changes.company_id !== identity.company_id) {
      identity = null; csrfToken = null; invalidateView();
      $('load-state').textContent = '계정 권한이 변경되었습니다. 계정을 다시 선택해 주세요.'; return;
    }
    if (changes.current_ledger_version > snapshot.meta.ledger_version) {
      latestVersion = changes.current_ledger_version; refreshButton.textContent = `새 원장 v${latestVersion} 조회`; refreshButton.hidden = false;
    }
  } catch (error) {
    if (error.status === 401) { identity = null; csrfToken = null; invalidateView(); }
  } finally { polling = false; }
}
setInterval(pollChanges, 5000);
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
$('theme').addEventListener('click', () => {
  const dark = document.documentElement.dataset.theme !== 'dark'; document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  $('theme').setAttribute('aria-label', dark ? '밝은 테마로 전환' : '어두운 테마로 전환');
});
$('reset').addEventListener('click', () => {
  if (!snapshot) return;
  generation++; requests.abort(); requests = new AbortController();
  cancelPendingReplay(); graph.reset(); dashboard.select(null); createPanel('graph'); createPanel('dashboard');
  notify('화면 선택과 대화를 초기화했습니다. 원장은 유지됩니다.');
});
start();
