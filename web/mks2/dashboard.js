// Presentation only. Every displayed fact is supplied by the authenticated ledger read model.
// Explicit BOM commands are delegated to the authenticated server through the BOM drawer.
import { createBomDrawer } from './bom.js';
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export function formatQuantity(value) {
  if (typeof value === 'number') return Number.isFinite(value)
    ? value.toLocaleString('ko-KR', { maximumSignificantDigits: 21 }) : '미확인';
  if (typeof value !== 'string') return '미확인';
  // Material quantities arrive as exact decimal strings. Group their digits
  // without floating-point conversion or rounding a positive shortage to zero.
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return '미확인';
  const integer = match[2].replace(/^0+(?=\d)/, '');
  const fraction = (match[3] || '').replace(/0+$/, '');
  const sign = match[1] && (integer !== '0' || fraction) ? '-' : '';
  return `${sign}${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${fraction ? `.${fraction}` : ''}`;
}
const num = formatQuantity;
const pct = (value, total) => total > 0 ? Math.max(0, Math.min(100, Number(value || 0) / total * 100)) : 0;
const shortDate = value => value ? String(value).slice(5, 10) : '미확인';
const tone = value => ['ok','warn','bad','info','mute'].includes(value) ? value : 'mute';
const tag = (label, kind='mute') => `<span class="mk-pill ${tone(kind)}">${esc(label)}</span>`;
const button = (label, action, primary=false, extra='') => `<button type="button" class="mk-btn${primary ? ' primary' : ''}" data-action="${action}" ${extra}>${esc(label)}</button>`;
const originLabel = origin => ({ actual_source: '실제 BOM 원문', development_workflow: '개발 원장 등록·승인', synthetic_assumption: '시연 적용 가정', reviewed_application: '검토된 적용 판단', synthetic_operation: '합성 기초 이관' })[origin] || '출처 확인 필요';
const segmentKeys = ['arrived_qty','transit_qty','warehouse_remaining_qty','good_not_received_qty','production_remaining_qty'];
const segmentLabels = ['도착','운송 중','창고','입고 대기','남은 생산'];
const segmentClass = ['arrived','transit','warehouse','pending','remaining'];

function bars(row, total = row.promised_qty, compact=false) {
  return `<div class="mk-segments${compact ? ' compact' : ''}" aria-label="${segmentLabels.map((label,i) => `${label} ${num(row[segmentKeys[i]])}`).join(', ')}">${segmentKeys.map((key,i) => `<i class="${segmentClass[i]}" style="flex-basis:${pct(row[key],total)}%" title="${segmentLabels[i]} ${num(row[key])}"></i>`).join('')}</div>`;
}

function donut(delivery) {
  let angle = 0;
  const total = Number(delivery.promised_qty) || 1;
  const b = delivery.breakdown ?? {};
  const circles = segmentKeys.map((key,i) => {
    const length = pct(b[key],total);
    const result = `<circle class="mk-donut-${segmentClass[i]}" cx="56" cy="56" r="44" pathLength="100" stroke-dasharray="${Math.max(0,length-.4)} ${100-Math.max(0,length-.4)}" stroke-dashoffset="${-angle}" transform="rotate(-90 56 56)"/>`;
    angle += length;
    return result;
  }).join('');
  return `<svg class="mk-donut" viewBox="0 0 112 112" role="img" aria-label="남은 약속 ${num(delivery.promise_remaining_qty)}대"><circle class="mk-donut-track" cx="56" cy="56" r="44"/>${circles}<text x="56" y="58" class="mk-donut-number" text-anchor="middle">${esc(num(delivery.promise_remaining_qty))}</text><text x="56" y="75" class="mk-donut-label" text-anchor="middle">남은 약속</text></svg>`;
}

function headerCard(title, reference='') {
  return `<div class="mk-kpi-heading"><span>${esc(title)}</span>${reference ? `<span class="mk-reference">${esc(reference)}</span>` : ''}</div>`;
}

export function mountDashboard(container, { snapshot, onSelect = () => {}, onAsk = () => {}, onGraph = () => {}, api, getIdentity, onPosted, onRefresh }) {
  const summary = snapshot.summary || {}, meta = snapshot.meta || {};
  const entities = snapshot.entities || [];
  const rows = (snapshot.rounds || []).map(row => ({ ...row, id: row.id || row.node_id || entities.find(entity => entity.type === 'DeliveryRound' && entity.display_id === row.delivery_round_id)?.id }));
  const inventory = (snapshot.inventory || []).map(row => ({ ...row, reserved_qty: row.reserved_qty ?? row.reserved_other_qty, display_id: row.lot_id || row.display_id || row.item_id, id: row.id || row.node_id || entities.find(entity => entity.type === (row.scope === 'LOT' ? 'PartLot' : 'InternalItem') && entity.display_id === (row.lot_id || row.item_id))?.id }));
  const version = meta.ledger_version;
  const delivery = { ...summary, promised_qty: summary.promised_qty ?? summary.promise_qty,
    breakdown: Object.fromEntries(segmentKeys.map(key => [key, summary[key]])) };
  let selected = null, filter = 'all';
  const bom = createBomDrawer({ snapshot, api, getIdentity, onPosted, onRefresh });
  const requirements = snapshot.material_requirements || {};
  const requirementRows = requirements.rows || [], requirementItems = requirements.items || [];
  let basis = 'production_remaining';
  const abort = new AbortController(), { signal } = abort;
  container.classList.add('mk-dashboard');
  const blockedBom = ['unregistered', 'UNREGISTERED', 'NOT_REGISTERED', 'BLOCKED_NO_BOM', 'missing'].includes(summary.bom_status);
  const statusText = blockedBom ? 'BOM 미등록 · 소요 계산 보류' : summary.bom_status === 'POSTED_SOURCE' ? 'BOM 원본 등록 반영 · 기술 검수 별도' : summary.bom_status || 'BOM 상태 미확인';
  const calculationLabel = requirements.state === 'CALCULATED' ? '계산됨' : requirementItems.length ? '부분 계산' : '보류';
  function renderKpis() {
    return `<section class="mk-kpis" aria-label="운영 지표">
      <article class="mk-kpi mk-delivery-kpi">${headerCard('납품', summary.delivery_round_id)}
      <div class="mk-delivery-viz">${donut(delivery)}<ul class="mk-donut-legend">${segmentKeys.map((key, i) => `<li><i class="${segmentClass[i]}"></i><span>${segmentLabels[i]}</span><b>${num(summary[key])}</b></li>`).join('')}</ul></div>
      <div class="mk-kpi-sub">약속 ${num(delivery.promised_qty)}대 · 도착 ${num(summary.arrived_qty)}대<br>남은 약속 ${num(summary.promise_remaining_qty)}대 · 추가 양품 목표 ${num(summary.production_remaining_qty)}대</div></article>
      <article class="mk-kpi">${headerCard('생산실적', summary.production_job_id)}<div class="mk-big">${num(summary.good_qty)}<small>대 양품</small></div>
      <div class="mk-quality"><i class="good" style="flex:${Number(summary.good_qty) || 0}">${num(summary.good_qty)}</i><i class="bad" style="flex:${Number(summary.defect_qty) || 0}"></i><i class="warn" style="flex:${Number(summary.pending_judgement_qty) || 0}"></i></div>
      <div class="mk-small-legend"><span class="good">양품 ${num(summary.good_qty)}</span><span class="bad">불량 ${num(summary.defect_qty)}</span><span class="warn">판정대기 ${num(summary.pending_judgement_qty)}</span></div>
      <div class="mk-kpi-sub">기초 이관 집계 · 개별 serial·장착 이력은 별도 근거가 필요합니다.</div></article>
      <article class="mk-kpi">${headerCard('자재', summary.item_id)}<div class="mk-big mk-warning">${calculationLabel}<small>소요 계산</small></div><div class="mk-kpi-sub">${esc(statusText)}</div><div class="mk-kpi-sub">현재 가용 ${num(summary.available_qty)}개</div>${button('BOM 원본 등록', 'bom')}</article>
      <article class="mk-kpi">${headerCard('구매')}<div class="mk-big">${num(summary.purchase_order_qty)}<small>개 발주</small></div><div class="mk-purchase-stage">${Array.from({ length: 4 }, () => '<i></i>').join('')}</div><div class="mk-kpi-sub">${esc(summary.purchase_status || '구매 실행 서비스 연결 전')}</div><div class="mk-kpi-sub">확정 발주와 공급사 송신을 구분합니다.</div></article>
      <article class="mk-kpi">${headerCard('수입검사')}<div class="mk-big">${num(summary.inspection_pending_qty)}<small>개 대기</small></div><div class="mk-inspection-meter"><i style="width:${pct(summary.inspection_passed_qty, summary.inspection_total_qty)}%"></i></div><div class="mk-small-legend"><span class="warn">대기 ${num(summary.inspection_pending_qty)}</span><span class="good">합격 ${num(summary.inspection_passed_qty)}</span></div><div class="mk-kpi-sub">입하·합격은 별도 사건입니다. 검사대기 수량은 가용으로 보지 않습니다.</div></article>
    </section>`;
  }
  function roundContent() {
    const visible = rows.filter(row => filter === 'all' || (filter === 'done' ? row.promise_remaining_qty === 0 : row.promise_remaining_qty > 0));
    return `<div class="mk-table-scroll"><table class="mk-round-table"><thead><tr><th>계약 · 회차</th><th>보드 / 개정 · 구성</th><th>도착기한</th><th>약속 / 실제 도착</th><th>남은 약속</th><th>현재 상태</th><th>다음 조치</th><th>담당</th></tr></thead><tbody>${visible.map(row => `<tr${selected === row.id ? ' class="selected"' : ''}>
      <td><button type="button" class="mk-link" data-graph="${esc(row.id || row.node_id)}">${esc(row.delivery_round_id || row.display_id)}</button><small>${esc(row.contract_id || '')}</small></td>
      <td><div class="mk-board"><span class="mk-photo">실물 사진<br>미등록</span><span><strong>${esc(row.board_label || '미확인')}</strong><small>${esc(row.configuration_id || '구성 미확인')}</small></span></div></td>
      <td class="mk-date">${esc(shortDate(row.arrival_due_date))}</td><td class="mk-numeric">${num(row.promised_qty ?? row.promise_qty)} / <b>${num(row.arrived_qty)}</b>${bars(row, row.promised_qty ?? row.promise_qty, true)}</td>
      <td class="mk-numeric"><b>${num(row.promise_remaining_qty)}</b><small>생산 ${num(row.production_remaining_qty)}</small></td><td>${tag(row.status?.label || '기초 이관 집계', 'info')}</td><td>${esc(row.next_action?.label || statusText)}</td><td class="mk-muted">${esc(row.owner_role || '미확인')}</td></tr>`).join('') || '<tr><td colspan="8" class="mk-no-rows">현재 조회 범위에 해당 회차가 없습니다.</td></tr>'}</tbody></table></div>
      <div class="mk-table-footer">${num(visible.length)}개 표시 · 전체 ${num(rows.length)} 회차 · 원장 v${num(version)} · 합성 초기 이관</div>`;
  }
  function materialContent() {
    if (!requirements.bom_id) return `<div class="mk-empty"><b>${blockedBom ? 'BOM이 없어 계산할 수 없습니다' : '승인된 BOM 근거가 필요합니다'}</b><p>실제 원본의 등록·검토·승인·반영 후 같은 버전으로 소요량을 조회합니다. 미확인 수량은 0으로 계산하지 않습니다.</p>${button('BOM 원본 등록', 'bom', true)}</div>`;
    const planned = basis === 'planned', basisData = requirements.basis || {};
    const qtyKey = planned ? 'planned_required_qty' : 'required_qty', shortageKey = planned ? 'planned_shortage_qty' : 'shortage_qty';
    const label = planned ? `계획 투입 ${num(basisData.planned_input_qty)}대 · 수율 ${esc(basisData.plan_yield)} 가정` : `추가 양품 목표 ${num(basisData.production_remaining_qty)}대 기준`;
    const issueLabel = value => ({ UNRESOLVED: '적용 미확인', MAPPING_UNKNOWN: '품목 매핑 미확인', INVENTORY_UNKNOWN: '재고 미확인', APPLICATION_UNREVIEWED: '적용 미검수', ITEM_MAPPING_UNCONFIRMED: '품목 매핑 미확인', QUANTITY_UNCONFIRMED: '수량 미확인', UNIT_UNCONFIRMED: '단위 미확인', INVENTORY_UNIT_UNCONFIRMED: '재고 단위 미확인', INVENTORY_COVERAGE_AMBIGUOUS: '재고 범위 대조 필요', CALCULATED: '계산됨', APPLICABLE: '적용 가정', UNIT_UNKNOWN: '단위 미확인', NOT_MOUNTED: '비실장', SETTING: '설정', UNREVIEWED: '미검수', DEMO_ASSUMPTION: '시연 가정' }[value] || value);
    return `<p class="mk-small-note"><b>전체 BOM 중 일부 품목 기준 · ${num(requirementItems.length)}개 품목</b> · ${label}. 적용 수량·품목 연결은 시연 가정, 재고는 합성 초기 원장 기준입니다. 원장 v${num(version)} / BOM 문서 v${num(requirements.bom_version)}.</p>
      <p class="mk-small-note">실제 BOM 원문 전체 ${num(requirementRows.length)}행은 아래 상세에 보존하며 이 계산을 전체 BOM 기술 검수·제조 가능 확정으로 보지 않습니다. ${planned ? '계획 수율은 실적이나 양품 수량 보장이 아닙니다.' : ''}</p>
      <div class="mk-table-scroll"><table class="mk-round-table" data-register="material-items"><thead><tr><th>품목 / 단위</th><th>합산 소요</th><th>가용</th><th>부족</th><th>판정 / 근거</th></tr></thead><tbody>${requirementItems.map(item => `<tr><td>${esc(item.item_id)}<small>${esc(item.unit || '단위 미확인')}</small></td><td>${num(item[qtyKey])}</td><td>${num(item.available_qty)}</td><td>${num(item[shortageKey])}</td><td>${esc(item.conditional ? '가정에 따른 계산' : issueLabel(item.state))}<small>${esc((item.issues || []).map(issueLabel).join(' · '))}</small><small>원문 ${num(item.row_ids?.length)}행 합산 · ${esc(item.inventory_scope || '재고 범위 미확인')}</small></td></tr>`).join('') || '<tr><td colspan="5">계산 대상 품목이 아직 연결되지 않았습니다. 원문 상세에서 검수 근거를 확인할 수 있습니다.</td></tr>'}</tbody></table></div>
      <details class="mk-material-details"><summary>원문 전체 ${num(requirementRows.length)}행 · 적용 검토와 계산 제외 근거 보기</summary>      <div class="mk-bom-stats"><div><b>${num(requirements.row_count)}</b><span>원문 전체 행</span></div><div><b>${num(requirements.applicable_count)}</b><span>적용 판단 행</span></div><div><b>${num(requirements.unresolved_count)}</b><span>적용 미확인 행</span></div><div><b>${num(requirements.unknown_shortage_count)}</b><span>부족량 미확인</span></div></div><div class="mk-table-scroll"><table class="mk-round-table" data-register="material-rows"><thead><tr><th>원문 근거</th><th>부품 위치</th><th>상태</th><th>대당 수량</th><th>검토 기준 소요</th><th>원문 × ${num(basisData.production_remaining_qty)}</th><th>원문 × ${num(basisData.planned_input_qty)}</th><th>미확인 / 가정</th></tr></thead><tbody>${requirementRows.map(row => `<tr><td><button type="button" class="mk-link" data-bom-evidence="${esc(row.row_id)}">${esc(row.locator || row.row_id)}</button></td><td>${esc((row.designators || []).join(', '))}</td><td>${esc(issueLabel(row.decision?.application_status || row.calculation_state))}</td><td>${num(row.decision?.quantity_per_unit)}</td><td>${num(row[qtyKey])}</td><td>${num(row.source_quantity_times_target)}</td><td>${num(row.source_quantity_times_plan)}</td><td>${esc(issueLabel(row.decision?.review_basis || 'UNREVIEWED'))}<small>${esc((row.issues || []).map(issueLabel).join(' · '))}</small></td></tr>`).join('')}</tbody></table></div></details>
      <p class="mk-small-note">원문 수량 단순 환산 · 단위/실장 검수 전. 원문의 수량 × 목표/계획 투입 수량이며 적용 소요·부족량과 구분합니다.</p>
      <p class="mk-small-note">같은 품목을 사용하는 BOM 행은 먼저 합산하고 재고를 한 번만 차감합니다. 검사대기·다른 수요 예약은 가용으로 더하지 않습니다.</p>${button('BOM 원문·검수 근거', 'bom-evidence')}`;
  }
  function render() {
    const date = meta.effective_at || meta.as_of;
    const dateLabel = date ? `기초 원장 기준 ${date} · ${requirements.bom_id ? '실제 BOM 원문 별도 등록' : '합성 초기 이관'}` : '합성 초기 이관 · 기준 시점 미확인';
    container.innerHTML = `<div class="mk-dashboard-heading"><h1>운영 대시보드</h1><span>${esc(dateLabel)}</span><label class="mk-live"><input type="checkbox" disabled>실시간 사건 반영</label></div>
      <div class="mk-config-banner"><b>${blockedBom ? '구성 미등록' : '계산 기준'}</b><span>${esc(statusText)}. 확인된 원장과 같은 버전의 근거로 판단합니다.</span>${button('BOM 원본 등록', 'bom', true)}</div>
      ${renderKpis()}
      <div class="mk-viz-grid"><section class="mk-card"><h3>자재 충족률<span class="mk-spacer"></span><span class="mk-toggle" role="group" aria-label="계산 기준"><button type="button" data-basis="production_remaining" aria-pressed="${basis === 'production_remaining'}">남은 생산 ${num(summary.production_remaining_qty)}대</button><button type="button" data-basis="planned" aria-pressed="${basis === 'planned'}">계획 수율</button></span></h3><div data-region="materials">${materialContent()}</div></section>
      <section class="mk-card"><h3>회차별 이행 단계<span class="mk-spacer"></span>${tag('단위: 대')}</h3>${rows.map(row => `<button type="button" class="mk-round-bar" data-select="${esc(row.id || row.node_id)}"><span class="mk-round-label">${esc(row.delivery_round_id || row.display_id)}<small>${num(row.promised_qty ?? row.promise_qty)}대</small></span><span class="mk-bar-width">${bars(row, row.promised_qty ?? row.promise_qty)}</span><span class="mk-round-remaining">${num(row.promise_remaining_qty)}</span></button>`).join('')}<p class="mk-small-note">오른쪽 숫자는 남은 고객 도착 약속입니다.</p><div class="mk-inline-legend">${segmentLabels.map((label, i) => `<span><i class="${segmentClass[i]}"></i>${label}</span>`).join('')}</div></section></div>
      <section class="mk-card mk-rounds-card"><h3>계약 회차별 이행 현황 ${tag(num(rows.length))}<span class="mk-spacer"></span><span class="mk-toggle" role="group" aria-label="회차 상태">${[['all', '전체'], ['open', '미완료'], ['done', '납품 완료']].map(([id, label]) => `<button type="button" data-filter="${id}" aria-pressed="${filter === id}">${label}</button>`).join('')}</span></h3><div data-region="rounds">${roundContent()}</div></section>
      <div class="mk-lower-grid"><section class="mk-card"><h3>원장 사건 ${tag(`${snapshot.events?.length ?? 0} / ${snapshot.event_count ?? snapshot.events?.length ?? 0}`)}<span class="mk-spacer"></span>${tag(`원장 v${num(version)}`, 'info')}</h3><ul class="mk-event-list">${(snapshot.events || []).map(event => `<li><span>${esc(event.recorded_at || '')}</span><span>${esc(event.summary || event.event_type || event.kind || '')}</span></li>`).join('') || '<li><span>기초 이관</span><span>기초 집계와 이후 서버 사건을 구분합니다.</span></li>'}</ul></section>
      <section class="mk-card"><h3>품목별 재고<span class="mk-spacer"></span>${tag('원장 조회', 'info')}</h3><div class="mk-table-scroll"><table class="mk-round-table"><thead><tr><th>품목 / LOT</th><th>보유</th><th>예약</th><th>가용</th></tr></thead><tbody>${inventory.map(item => `<tr><td><button class="mk-link" type="button" data-graph="${esc(item.id || item.node_id)}">${esc(item.display_id || item.item_id)}</button></td><td>${num(item.on_hand_qty)}</td><td>${num(item.reserved_qty)}</td><td>${num(item.available_qty)}</td></tr>`).join('') || '<tr><td colspan="4">조회된 재고 근거가 없습니다.</td></tr>'}</tbody></table></div></section></div>
      <section class="mk-card"><h3>원장 객체 ${tag(num(entities.length))}<span class="mk-spacer"></span>${tag('동일 조회 버전')}</h3><div class="mk-table-scroll"><table class="mk-round-table" data-register="entities"><thead><tr><th>객체</th><th>종류</th><th>근거</th><th>상태</th></tr></thead><tbody>${entities.map(entity => `<tr><td><button type="button" class="mk-link" data-graph="${esc(entity.id)}">${esc(entity.display_id || entity.id)}</button></td><td>${esc(entity.type)}</td><td>${num(Array.isArray(entity.evidence) ? entity.evidence.length : entity.evidence ? 1 : entity.evidence_refs?.length)}</td><td>${esc(entity.attributes?.status || entity.attributes?.state || originLabel(entity.origin))}</td></tr>`).join('')}</tbody></table></div></section>`;
  }
  function openBom(step = 0) { bom.open(step); }
  container.addEventListener('click', event => {
    if (event.target.closest('[data-action="bom"]')) openBom();
    const evidence = event.target.closest('[data-bom-evidence]');
    if (evidence) bom.open(4, requirements.bom_id, evidence.dataset.bomEvidence);
    else if (event.target.closest('[data-action="bom-evidence"]')) bom.open(2, requirements.bom_id);
    const selectedNode = event.target.closest('[data-select]')?.dataset.select;
    if (selectedNode) { selected = selectedNode; onSelect(selected); render(); }
    const graphNode = event.target.closest('[data-graph]')?.dataset.graph;
    if (graphNode) { selected = graphNode; onSelect(selected); onGraph(selected); }
    const targetFilter = event.target.closest('[data-filter]')?.dataset.filter;
    if (targetFilter) { filter = targetFilter; render(); }
    const targetBasis = event.target.closest('[data-basis]')?.dataset.basis;
    if (targetBasis) { basis = targetBasis; render(); }
  }, { signal });
  render();
  return { select(id) { selected = id; render(); }, openBom,
    destroy() { bom.destroy(); abort.abort(); container.replaceChildren(); container.classList.remove('mk-dashboard'); } };
}
