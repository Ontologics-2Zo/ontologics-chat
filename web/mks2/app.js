import { mountDashboard } from './dashboard.js';
import { mountGraph } from './graph.js';

// Presentation only. This module cannot approve, post, mutate inventory, or call a model.
const $ = (id) => document.getElementById(id);
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function button(text, callback, className = 'btn sm') {
  const node = element('button', className, text);
  node.type = 'button'; node.addEventListener('click', callback); return node;
}
let bundle, graph, dashboard, contextId, noticeTimer;
let replayGeneration = 0;
let pendingReplayMessage = null;
const panels = {};
const currentScreen = () => location.hash === '#dashboard' ? 'dashboard' : 'graph';
const session = () => bundle.session.data;
function notify(text) {
  $('notice').textContent = text; $('notice').hidden = false;
  clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $('notice').hidden = true; }, 4500);
}
function route() {
  if (!graph) return;
  const current = currentScreen();
  for (const name of ['graph','dashboard']) {
    $(`${name}-view`).hidden = current !== name;
    $(`tab-${name}`).setAttribute('aria-current', current === name ? 'page' : 'false');
  }
  if (current === 'graph') requestAnimationFrame(() => graph.resize());
}
function setContext(value) {
  const id = typeof value === 'string' ? value : value?.node_id ?? value?.delivery_round_id ?? value?.id;
  if (!id) return;
  contextId = id;
  const area = panels.graph?.context;
  if (area) area.replaceChildren(document.createTextNode('맥락 '), button(id, () => graph.focus(id), 'chip'), document.createTextNode(` 그래프 v${session().projection_version}`));
}
function fillQuestion(text, name = currentScreen()) {
  const panel = panels[name];
  panel.input.value = text; panel.input.focus();
}
function createPanel(name) {
  const root = $(`${name}-panel`);
  root.replaceChildren();
  const heading = element('div','panel-heading');
  heading.append(element('h2','',name === 'graph' ? '업무 AI 질의' : '업무 에이전트'),element('span','tag','시연용 응답'));
  const context = element('div','panel-context');
  const messages = element('div','messages');
  messages.setAttribute('aria-live','polite'); messages.setAttribute('aria-relevant','additions');
  const suggestions = element('div','suggestions');
  const form = element('form','question-form');
  const input = element('input'); input.autocomplete = 'off';
  input.setAttribute('aria-label',name === 'graph' ? '질문' : '에이전트에게 질문');
  input.placeholder = name === 'graph' ? '질문하면 근거를 그래프에서 따라갑니다' : `예: ${bundle.ai.before_bom.data.question}`;
  const submit = element('button','btn pri',name === 'graph' ? '보내기' : '실행'); submit.type = 'submit';
  form.append(input,submit);
  form.addEventListener('submit', event => { event.preventDefault(); const question=input.value.trim(); if (!question) return; input.value=''; runPreview(name,question); });
  const disclosure = element('div','disclosure',name === 'graph'
    ? '합성 시연 · 서버 원장·실제 AI 미연결. 근거 재생은 화면 조회이며 승인·발주를 실행하지 않습니다.'
    : '합성 시연 · 서버 원장·실제 AI 미연결. 선택·승인·확정은 서비스 연결 후 사람이 수행합니다.');
  root.append(heading,context); context.hidden = name !== 'graph';
  root.append(messages,suggestions,form,disclosure);
  panels[name] = {root,context,messages,suggestions,input};
  const greeting = element('div','message assistant');
  greeting.append(element('p','',name === 'graph'
    ? '질문하면 답의 근거를 관계 그래프에서 한 칸씩 따라가며 보여드립니다.'
    : '회차 이행 가능 여부를 물으면 계약을 찾고, 불량률과 BOM으로 부품 부족을 예측하고, 공급 후보를 비교합니다.'));
  greeting.append(element('p','note',name === 'graph' ? '노드를 눌러 속성과 연결을 볼 수 있습니다.' : '선택·승인·확정은 사람이 합니다.'));
  messages.append(greeting);
  if (name === 'graph') {
    const intents = bundle.graphQueries.intents;
    for (const intent of ['QTY_DECOMPOSITION','BOM_STATUS','SHORTAGE_REASON']) {
      const item = intents.find(i => i.intent === intent);
      if (item) suggestions.append(button(item.sample, () => fillQuestion(item.sample,name), 'suggestion'));
    }
    const missing = intents.find(i => i.by_state?.some(s => s.answer?.kind === 'NOT_FOUND') && !['SHORTAGE_REASON'].includes(i.intent));
    if (missing) suggestions.append(button(missing.sample, () => fillQuestion(missing.sample,name), 'suggestion'));
  } else {
    const question = bundle.ai.before_bom.data.question;
    suggestions.append(button(question, () => fillQuestion(question,name), 'suggestion'));
  }
}
function scrollMessages(name) { const area=panels[name].messages; area.scrollTop=area.scrollHeight; }
function unavailable(name, text) {
  const message = element('div','message assistant');
  message.append(element('p','',text),element('p','note','실제 모델 호출 0회 · 업무 원장 변경 없음'));
  panels[name].messages.append(message); scrollMessages(name);
}
function cancelPendingReplay() {
  replayGeneration++;
  graph?.cancelReplay();
  if (pendingReplayMessage) {
    pendingReplayMessage.append(element('p','note','다른 화면 조작으로 근거 재생을 중단했습니다.'));
    pendingReplayMessage = null;
  }
}
async function runPreview(name,question) {
  const area=panels[name].messages;
  area.append(element('div','message user',question));
  if (name === 'dashboard') {
    if (question !== bundle.ai.before_bom.data.question) return unavailable(name,'현재는 등록된 화면 예시만 재생할 수 있습니다. 자유 질의는 실제 서비스 연결 후 사용할 수 있습니다.');
    showAgentPreview(bundle.ai.before_bom, false); return;
  }
  cancelPendingReplay();
  const intent = bundle.graphQueries.intents.find(i => i.sample === question);
  if (!intent) return unavailable(name,'이 질문에 연결된 시연 근거가 없습니다. 추천 질문을 선택하거나 실제 질의 서비스 연결 후 다시 실행해 주세요.');
  const data = intent.by_state.find(s => s.ledger_version === session().ledger_version && s.projection_version === session().projection_version);
  if (!data || session().ledger_version !== session().projection_version) return unavailable(name,'같은 버전의 근거가 준비되지 않아 답변을 보류합니다.');
  const message = element('div','message assistant');
  const steps = element('ol','trace-steps');
  for (const label of data.steps ?? []) steps.append(element('li','',label));
  message.append(steps); area.append(message);
  pendingReplayMessage = message;
  const hops=data.graph_path ?? [];
  const run=++replayGeneration;
  const result = await graph.replay({hops,nodeIds:[...new Set(hops.flat())]});
  if (run !== replayGeneration) return;
  if (!result?.completed) { cancelPendingReplay(); return; }
  pendingReplayMessage = null;
  for (const step of steps.children) step.className='done';
  message.append(element('p','answer',data.answer.text));
  const evidence=element('div','evidence-path');
  for (const id of [...new Set([...hops.flat(),...(data.evidence_node_ids ?? [])])]) evidence.append(button(id,()=>graph.focus(id),'chip'));
  const footer=element('div','answer-footer',`시연 원장 v${data.ledger_version} · 그래프 v${data.projection_version} · 실제 모델 호출 0회`);
  footer.append(button('근거 다시 보기',()=>{cancelPendingReplay();graph.replay({hops,nodeIds:[...new Set(hops.flat())]});},'btn sm'));
  message.append(evidence,footer); scrollMessages(name);
}
function showAgentPreview(envelope, separateSnapshot) {
  const data=envelope.data, area=panels.dashboard.messages;
  if (separateSnapshot) {
    panels.dashboard.context.hidden=false;
    panels.dashboard.context.textContent=`견적 화면 예시 · 별도 시연 v${envelope.meta.ledger_version} · 현재 원장과 별도`;
    area.replaceChildren(element('div','message assistant',`견적 화면 예시 · 별도 시연 v${envelope.meta.ledger_version}. 현재 원장에 반영된 결과가 아닙니다.`));
  }
  for (const step of data.steps) {
    const card=element('div','agent-step');
    const title=element('h3'); title.append(element('span','tag',step.n),document.createTextNode(step.label),element('code','',step.tool)); card.append(title);
    const result=step.result;
    if (result.promise_remaining_qty != null) {
      card.append(element('p','',`남은 약속 ${result.promise_remaining_qty}대 중 이미 만든 ${result.already_produced_qty}대가 있습니다. 입고 대기 ${result.good_not_received_qty} · 창고 ${result.warehouse_remaining_qty} · 운송 중 ${result.transit_qty}. 더 생산할 양품은 ${result.production_remaining_qty}대입니다.`));
    }
    if (result.defect_rate != null) card.append(element('p','',`불량 ${result.defect_qty} ÷ 판정 ${result.judged_qty} = ${(result.defect_rate*100).toFixed(2)}%. 판정 대기 ${result.pending_excluded}대는 제외합니다. 이 비율이 유지된다는 가정의 투입 ${result.input_needed_qty}대이며, 재고 수량이 아닙니다.`));
    if (result.stopped) card.append(element('p','',`${result.configuration_id} · 구성 미등록으로 자재 소요 계산 보류`));
    if (result.lines) {
      const table=element('table','agent-table');const head=element('tr');
      for (const name of ['품목','필요','가용','부족']) head.append(element('th','',name));
      const thead=element('thead');thead.append(head);table.append(thead);const body=element('tbody');
      for (const line of result.lines) {
        const row=element('tr');
        for(const field of ['item_id','required_qty','available_qty','shortage_qty']) row.append(element('td','',line[field]??'미확인'));
        body.append(row);
      }
      table.append(body);card.append(table,element('p','note','원본 시연의 일부 품목 예시 · 전체 적용 BOM 검증은 별도입니다.'));
    }
    if (result.parts_need_by_date) card.append(element('p','',`도착 ${result.arrival_due_date} ← 운송 ${result.transit_days}일 ← 출하 준비 ${result.ship_prep_days}일 ← 생산 ${result.production_days}일 ← 수입검사 ${result.inspection_days}일 → 부품 입하 필요일 ${result.parts_need_by_date}.`));
    if (step.tool === 'procurement.compare' && data.proposals?.length && dashboard.renderQuotes) {
      card.append(element('p','',`연결된 유효 견적 ${data.proposals.length}건입니다. 가장 싼 곳을 자동으로 고르지 않습니다. 아래에서 비교합니다.`));
      const target=element('div');card.append(target);dashboard.renderQuotes(target);
    }
    area.append(card);
  }
  const verdict=element('div','verdict'); verdict.append(element('h3','',data.verdict.title),element('p','',data.verdict.body));
  verdict.append(element('p','note','시연 자료 재생 · 실제 도구·모델 호출 및 원장 변경 없음'));
  area.append(verdict);
  if (!separateSnapshot) {
    const actions=element('div','answer-footer');
    actions.append(button('BOM 등록 화면',()=>dashboard.openBom()),button('등록 후 견적 화면 예시',()=>showAgentPreview(bundle.ai.after_bom,true)));
    area.append(actions);
  } else area.append(button('현재 조회 기준으로 돌아가기',()=>{createPanel('dashboard');}));
  scrollMessages('dashboard');
}
async function start() {
  try {
    const response=await fetch('/api/preview',{cache:'no-store',credentials:'same-origin'});
    if (!response.ok) throw new Error('PREVIEW_UNAVAILABLE');
    const payload=await response.json();
    if (payload.mode !== 'reference-preview' || payload.serviceConnected !== false || !payload.bundle) throw new Error('PREVIEW_CONTRACT');
    bundle=payload.bundle;
    const graphMeta = bundle.graph?.meta;
    if (!graphMeta || graphMeta.ledger_version !== session().ledger_version || graphMeta.projection_version !== session().projection_version || graphMeta.read_snapshot_id !== session().snapshot_id) throw new Error('SNAPSHOT_MISMATCH');
    $('versions').replaceChildren(document.createTextNode('시연 원장 '),element('b','',`v${session().ledger_version}`),document.createTextNode(' · 그래프 '),element('b','',`v${session().projection_version}`),element('span','pill mute','원장 미연결'));
    $('actor').replaceChildren(...session().selectable_actors.map(actor=>{const option=element('option','',`${actor.actor_id} · ${actor.label}`); option.value=actor.actor_id; return option;}));
    $('actor').value=session().current_actor.actor_id;
    createPanel('graph'); createPanel('dashboard');
    dashboard=mountDashboard($('dashboard-stage'),{bundle,onSelect:setContext,onAsk:text=>fillQuestion(text,'dashboard'),onGraph:id=>{location.hash='#graph';setContext(id);requestAnimationFrame(()=>graph.focus(typeof id==='string'?id:id.node_id));}});
    graph=mountGraph($('graph-stage'),{bundle,onSelect:setContext,onAsk:text=>fillQuestion(text,'graph'),onBom:()=>dashboard.openBom()});
    setContext(bundle.ai.before_bom.data.context.delivery_round_id);
    $('load-state').hidden=true; $('workspace').setAttribute('aria-busy','false'); route();
  } catch (error) {
    $('load-state').textContent=error.message === 'SNAPSHOT_MISMATCH'
      ? '원장과 그래프의 조회 버전이 달라 표시를 보류합니다. 같은 snapshot의 자료를 연결해 주세요.'
      : '화면 자료를 읽을 수 없습니다. PRIVATE_INPUT_ROOT와 비공개 시연 실행 설정을 확인해 주세요. 임의의 수치나 정상 상태로 대체하지 않습니다.';
    $('workspace').setAttribute('aria-busy','false'); $('versions').textContent='자료 미연결';
  }
}
window.addEventListener('hashchange',route);
$('theme').addEventListener('click',()=>{const dark=document.documentElement.dataset.theme!=='dark';document.documentElement.dataset.theme=dark?'dark':'light';$('theme').setAttribute('aria-label',dark?'밝은 테마로 전환':'어두운 테마로 전환');});
$('reset').addEventListener('click',()=>{if(!graph)return;cancelPendingReplay();graph.reset();dashboard.select?.(null);createPanel('graph');createPanel('dashboard');setContext(bundle.ai.before_bom.data.context.delivery_round_id);notify('화면 선택과 시연 대화를 초기화했습니다. 원장 변경은 없습니다.');});
start();
