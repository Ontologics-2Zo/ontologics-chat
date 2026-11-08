// Presentation only. Every displayed fact is supplied by the authenticated ledger read model.
// This module never approves documents, changes a ledger, or calls a model.
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const num = value => value == null ? '미확인' : Number.isFinite(Number(value)) ? Number(value).toLocaleString('ko-KR') : '미확인';
const pct = (value, total) => total > 0 ? Math.max(0, Math.min(100, Number(value || 0) / total * 100)) : 0;
const shortDate = value => value ? String(value).slice(5, 10) : '미확인';
const tone = value => ['ok','warn','bad','info','mute'].includes(value) ? value : 'mute';
const tag = (label, kind='mute') => `<span class="mk-pill ${tone(kind)}">${esc(label)}</span>`;
const button = (label, action, primary=false, extra='') => `<button type="button" class="mk-btn${primary ? ' primary' : ''}" data-action="${action}" ${extra}>${esc(label)}</button>`;
const dget = (entry, state='initial') => entry?.states?.[state]?.data ?? entry?.data ?? {};
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

export function mountDashboard(container, { snapshot, onSelect = () => {}, onAsk = () => {}, onGraph = () => {} }) {
  const summary = snapshot.summary || {}, meta = snapshot.meta || {};
  const entities = snapshot.entities || [];
  const rows = (snapshot.rounds || []).map(row => ({ ...row, id: row.id || row.node_id || entities.find(entity => entity.type === 'DeliveryRound' && entity.display_id === row.delivery_round_id)?.id }));
  const inventory = (snapshot.inventory || []).map(row => ({ ...row, reserved_qty: row.reserved_qty ?? row.reserved_other_qty, display_id: row.lot_id || row.display_id || row.item_id, id: row.id || row.node_id || entities.find(entity => entity.type === (row.scope === 'LOT' ? 'PartLot' : 'InternalItem') && entity.display_id === (row.lot_id || row.item_id))?.id }));
  const version = meta.ledger_version;
  const delivery = { ...summary, promised_qty: summary.promised_qty ?? summary.promise_qty,
    breakdown: Object.fromEntries(segmentKeys.map(key => [key, summary[key]])) };
  let selected = null, filter = 'all', drawer = null, previousFocus = null;
  let basis = 'production_remaining';
  const abort = new AbortController(), { signal } = abort;
  container.classList.add('mk-dashboard');
  const blockedBom = ['unregistered', 'UNREGISTERED', 'NOT_REGISTERED', 'BLOCKED_NO_BOM', 'missing'].includes(summary.bom_status);
  const statusText = blockedBom ? 'BOM 미등록 · 소요 계산 보류' : summary.bom_status || 'BOM 상태 미확인';
  function renderKpis() {
    return `<section class="mk-kpis" aria-label="운영 지표">
      <article class="mk-kpi mk-delivery-kpi">${headerCard('납품', summary.delivery_round_id)}
      <div class="mk-delivery-viz">${donut(delivery)}<ul class="mk-donut-legend">${segmentKeys.map((key, i) => `<li><i class="${segmentClass[i]}"></i><span>${segmentLabels[i]}</span><b>${num(summary[key])}</b></li>`).join('')}</ul></div>
      <div class="mk-kpi-sub">약속 ${num(delivery.promised_qty)}대 · 도착 ${num(summary.arrived_qty)}대<br>남은 약속 ${num(summary.promise_remaining_qty)}대 · 추가 양품 목표 ${num(summary.production_remaining_qty)}대</div></article>
      <article class="mk-kpi">${headerCard('생산실적', summary.production_job_id)}<div class="mk-big">${num(summary.good_qty)}<small>대 양품</small></div>
      <div class="mk-quality"><i class="good" style="flex:${Number(summary.good_qty) || 0}">${num(summary.good_qty)}</i><i class="bad" style="flex:${Number(summary.defect_qty) || 0}"></i><i class="warn" style="flex:${Number(summary.pending_judgement_qty) || 0}"></i></div>
      <div class="mk-small-legend"><span class="good">양품 ${num(summary.good_qty)}</span><span class="bad">불량 ${num(summary.defect_qty)}</span><span class="warn">판정대기 ${num(summary.pending_judgement_qty)}</span></div>
      <div class="mk-kpi-sub">기초 이관 집계 · 개별 serial·장착 이력은 별도 근거가 필요합니다.</div></article>
      <article class="mk-kpi">${headerCard('자재', summary.item_id)}<div class="mk-big mk-warning">${blockedBom ? '보류' : '미확인'}<small>소요 계산</small></div><div class="mk-kpi-sub">${esc(statusText)}</div><div class="mk-kpi-sub">현재 가용 ${num(summary.available_qty)}개</div>${button('BOM 원본 등록', 'bom')}</article>
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
  function render() {
    const date = meta.effective_at || meta.as_of;
    const dateLabel = date ? `기준 ${date} · 합성 초기 이관` : '합성 초기 이관 · 기준 시점 미확인';
    container.innerHTML = `<div class="mk-dashboard-heading"><h1>운영 대시보드</h1><span>${esc(dateLabel)}</span><label class="mk-live"><input type="checkbox" disabled>실시간 사건 반영</label></div>
      <div class="mk-config-banner"><b>${blockedBom ? '구성 미등록' : '계산 기준'}</b><span>${esc(statusText)}. 확인된 원장과 같은 버전의 근거로 판단합니다.</span>${button('BOM 원본 등록', 'bom', true)}</div>
      ${renderKpis()}
      <div class="mk-viz-grid"><section class="mk-card"><h3>자재 충족률<span class="mk-spacer"></span><span class="mk-toggle" role="group" aria-label="계산 기준"><button type="button" data-basis="production_remaining" aria-pressed="${basis === 'production_remaining'}">남은 생산 ${num(summary.production_remaining_qty)}대</button><button type="button" data-basis="planned" aria-pressed="${basis === 'planned'}">계획 수율</button></span></h3><div data-region="materials"><div class="mk-empty"><b>${blockedBom ? 'BOM이 없어 계산할 수 없습니다' : '승인된 BOM 근거가 필요합니다'}</b><p>${basis === 'planned' ? '계획 수율은 실적과 구분하며, 적용 BOM과 가정을 확인한 뒤 계산합니다.' : '가용 재고와 실제 부품 소요를 구분합니다. 미확인 수량은 0으로 계산하지 않습니다.'}</p>${button('BOM 원본 등록', 'bom', true)}</div></div></section>
      <section class="mk-card"><h3>회차별 이행 단계<span class="mk-spacer"></span>${tag('단위: 대')}</h3>${rows.map(row => `<button type="button" class="mk-round-bar" data-select="${esc(row.id || row.node_id)}"><span class="mk-round-label">${esc(row.delivery_round_id || row.display_id)}<small>${num(row.promised_qty ?? row.promise_qty)}대</small></span><span class="mk-bar-width">${bars(row, row.promised_qty ?? row.promise_qty)}</span><span class="mk-round-remaining">${num(row.promise_remaining_qty)}</span></button>`).join('')}<p class="mk-small-note">오른쪽 숫자는 남은 고객 도착 약속입니다.</p><div class="mk-inline-legend">${segmentLabels.map((label, i) => `<span><i class="${segmentClass[i]}"></i>${label}</span>`).join('')}</div></section></div>
      <section class="mk-card mk-rounds-card"><h3>계약 회차별 이행 현황 ${tag(num(rows.length))}<span class="mk-spacer"></span><span class="mk-toggle" role="group" aria-label="회차 상태">${[['all', '전체'], ['open', '미완료'], ['done', '납품 완료']].map(([id, label]) => `<button type="button" data-filter="${id}" aria-pressed="${filter === id}">${label}</button>`).join('')}</span></h3><div data-region="rounds">${roundContent()}</div></section>
      <div class="mk-lower-grid"><section class="mk-card"><h3>원장 사건 ${tag(`${snapshot.events?.length ?? 0} / ${snapshot.event_count ?? snapshot.events?.length ?? 0}`)}<span class="mk-spacer"></span>${tag(`원장 v${num(version)}`, 'info')}</h3><ul class="mk-event-list">${(snapshot.events || []).map(event => `<li><span>${esc(event.recorded_at || '')}</span><span>${esc(event.summary || event.event_type || event.kind || '')}</span></li>`).join('') || '<li><span>기초 이관</span><span>기초 집계와 이후 서버 사건을 구분합니다.</span></li>'}</ul></section>
      <section class="mk-card"><h3>품목별 재고<span class="mk-spacer"></span>${tag('원장 조회', 'info')}</h3><div class="mk-table-scroll"><table class="mk-round-table"><thead><tr><th>품목 / LOT</th><th>보유</th><th>예약</th><th>가용</th></tr></thead><tbody>${inventory.map(item => `<tr><td><button class="mk-link" type="button" data-graph="${esc(item.id || item.node_id)}">${esc(item.display_id || item.item_id)}</button></td><td>${num(item.on_hand_qty)}</td><td>${num(item.reserved_qty)}</td><td>${num(item.available_qty)}</td></tr>`).join('') || '<tr><td colspan="4">조회된 재고 근거가 없습니다.</td></tr>'}</tbody></table></div></section></div>
      <section class="mk-card"><h3>원장 객체 ${tag(num(entities.length))}<span class="mk-spacer"></span>${tag('동일 조회 버전')}</h3><div class="mk-table-scroll"><table class="mk-round-table" data-register="entities"><thead><tr><th>객체</th><th>종류</th><th>근거</th><th>상태</th></tr></thead><tbody>${entities.map(entity => `<tr><td><button type="button" class="mk-link" data-graph="${esc(entity.id)}">${esc(entity.display_id || entity.id)}</button></td><td>${esc(entity.type)}</td><td>${num(Array.isArray(entity.evidence) ? entity.evidence.length : entity.evidence ? 1 : entity.evidence_refs?.length)}</td><td>${esc(entity.attributes?.status || entity.attributes?.state || '기초 이관')}</td></tr>`).join('')}</tbody></table></div></section>`;
  }
  function closeDrawer() {
    if (!drawer) return;
    drawer.remove(); drawer = null; previousFocus?.focus(); previousFocus = null;
  }
  function openBom(step = 0) {
    closeDrawer(); previousFocus = document.activeElement;
    const stages = ['원본 접수', '개정 식별', '추출·검증', '개정 차이', '품목 연결', '등록 초안', '검토·승인', '반영'];
    const title = stages[step];
    drawer = document.createElement('div'); drawer.className = 'mk-drawer-wrap';
    drawer.innerHTML = `<div class="mk-drawer-scrim" data-close></div><section class="mk-drawer" role="dialog" aria-modal="true" aria-labelledby="mk-drawer-title"><header><div><div class="mk-drawer-eyebrow">제품 구성 · BOM 원본 등록</div><h2 id="mk-drawer-title">${esc(summary.configuration_id || 'BOM 등록')}</h2></div><button type="button" class="mk-btn" data-close>닫기</button></header>
      <nav class="mk-drawer-steps" aria-label="BOM 처리 단계">${stages.map((label, index) => `<button type="button" data-bom-step="${index}" aria-current="${index === step ? 'step' : 'false'}">${label}</button>`).join('')}</nav><div class="mk-drawer-body">
      ${step === 0 ? '<div class="mk-drop"><b>BOM 원본 접수</b><span>원본 접수와 제한된 파일 처리 기능은 연결 준비 중입니다.</span><button type="button" class="mk-btn" disabled>파일 선택 · 연결 준비 중</button></div>' : `<section class="mk-drawer-block"><h3>${title}</h3><p>현재 원장에는 이 단계의 실행 결과가 없습니다.</p><button type="button" class="mk-btn" disabled>${title} · 연결 준비 중</button></section>`}
      <section class="mk-drawer-block"><h3>현재 원장 조회</h3><dl class="mk-key-values"><dt>원장 버전</dt><dd>v${num(version)}</dd><dt>BOM 상태</dt><dd>${esc(statusText)}</dd><dt>기술 검수</dt><dd>${esc(summary.technical_review || '미확인')}</dd></dl></section><p class="mk-preview-notice">원본 접수·기술 검수·정확 버전 승인·반영을 각각 확인합니다. 이 화면의 단계 선택은 업무 상태를 변경하지 않습니다.</p></div></section>`;
    document.body.append(drawer);
    drawer.addEventListener('click', event => {
      if (event.target.closest('[data-close]')) closeDrawer();
      const target = event.target.closest('[data-bom-step]'); if (target) openBom(Number(target.dataset.bomStep));
    }, { signal });
    drawer.querySelector('[data-close].mk-btn').focus();
  }
  container.addEventListener('click', event => {
    if (event.target.closest('[data-action="bom"]')) openBom();
    const selectedNode = event.target.closest('[data-select]')?.dataset.select;
    if (selectedNode) { selected = selectedNode; onSelect(selected); render(); }
    const graphNode = event.target.closest('[data-graph]')?.dataset.graph;
    if (graphNode) { selected = graphNode; onSelect(selected); onGraph(selected); }
    const targetFilter = event.target.closest('[data-filter]')?.dataset.filter;
    if (targetFilter) { filter = targetFilter; render(); }
    const targetBasis = event.target.closest('[data-basis]')?.dataset.basis;
    if (targetBasis) { basis = targetBasis; render(); }
  }, { signal });
  document.addEventListener('keydown', event => {
    if (!drawer) return;
    if (event.key === 'Escape') { event.preventDefault(); closeDrawer(); }
    if (event.key === 'Tab') {
      const targets = [...drawer.querySelectorAll('button:not(:disabled),[href],input:not(:disabled),[tabindex="0"]')];
      const first = targets[0], last = targets.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }, { signal });
  render();
  return { select(id) { selected = id; render(); }, openBom,
    destroy() { closeDrawer(); abort.abort(); container.replaceChildren(); container.classList.remove('mk-dashboard'); } };
}
