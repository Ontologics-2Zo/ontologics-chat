// Presentation only. Private reference data is supplied by an explicit read-only adapter.
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

export function mountDashboard(container, {bundle, onSelect=()=>{}, onAsk=()=>{}, onGraph=()=>{}}) {
  const session = dget(bundle.session);
  const kpis = dget(bundle.kpis).kpis ?? {};
  const roundsData = dget(bundle.rounds);
  const rows = roundsData.rows ?? [];
  const planning = dget(bundle.planning);
  const bom = bundle.bom?.current?.data ?? {};
  const delivery = kpis.delivery ?? {};
  const production = kpis.production ?? {};
  const material = kpis.material ?? {};
  const purchase = kpis.purchase ?? {};
  const inspection = kpis.inspection ?? {};
  const mainRound = rows.find(row => row.delivery_round_id === delivery.delivery_round_id) ?? rows[0] ?? {};
  const version = session.ledger_version;
  let selected = null;
  let filter = 'all';
  let basis = 'production_remaining';
  let drawer = null;
  let previousFocus = null;
  const abort = new AbortController();
  const signal = abort.signal;
  container.classList.add('mk-dashboard');

  function dateLabel() {
    if (!session.as_of) return '기준 시점 미확인';
    const date = new Date(session.as_of);
    if (Number.isNaN(date.valueOf())) return '기준 시점 미확인';
    return `기준 ${new Intl.DateTimeFormat('sv-SE',{timeZone:session.timezone ?? 'UTC',year:'numeric',month:'2-digit',day:'2-digit'}).format(date)} (${new Intl.DateTimeFormat('ko-KR',{timeZone:session.timezone ?? 'UTC',weekday:'short'}).format(date)}) ${new Intl.DateTimeFormat('en-GB',{timeZone:session.timezone ?? 'UTC',hour:'2-digit',minute:'2-digit',hour12:false}).format(date)} · 합성 데이터`;
  }

  function renderKpis() {
    const good = Number(production.good_qty) || 0;
    const defect = Number(production.defect_qty) || 0;
    const pending = Number(production.pending_judgement_qty) || 0;
    const productionTotal = good + defect + pending;
    const breakdown = delivery.breakdown ?? {};
    return `<section class="mk-kpis" aria-label="운영 지표">
      <article class="mk-kpi mk-delivery-kpi">${headerCard(`${delivery.label ?? '납품'} · ${delivery.customer_name ?? ''}`,`${delivery.delivery_round_id ?? ''} · 도착기한 ${shortDate(delivery.arrival_due_date)} (D-${num(delivery.days_to_due)})`)}
        <div class="mk-delivery-viz">${donut(delivery)}<ul class="mk-donut-legend">${segmentKeys.map((key,i) => `<li><i class="${segmentClass[i]}"></i><span>${segmentLabels[i]}</span><b>${num(breakdown[key])}</b></li>`).join('')}</ul></div>
        <div class="mk-kpi-sub">약속 ${num(delivery.promised_qty)}대 중 남은 약속 ${num(delivery.promise_remaining_qty)} = 이미 생산 ${num(delivery.already_produced_qty)} + 남은 생산 ${num(breakdown.production_remaining_qty)}</div>
      </article>
      <article class="mk-kpi">${headerCard(production.label ?? '생산실적',production.production_job_id)}<div class="mk-big">${production.defect_rate == null ? '미확인' : esc((Number(production.defect_rate)*100).toFixed(2))+'%'}<small>불량률</small></div>
        <div class="mk-quality" aria-label="생산 실적"><i class="good" style="flex:${pct(good,productionTotal)}">${num(production.good_qty)}</i><i class="bad" style="flex:${pct(defect,productionTotal)}"></i><i class="warn" style="flex:${pct(pending,productionTotal)}"></i></div>
        <div class="mk-small-legend"><span class="good">양품 ${num(production.good_qty)}</span><span class="bad">불량 ${num(production.defect_qty)}</span><span class="warn">판정대기 ${num(production.pending_judgement_qty)}</span></div>
      </article>
      <article class="mk-kpi">${headerCard(`${material.label ?? '자재'} · ${material.production_job_id ?? ''}`)}<div class="mk-big mk-warning">${material.state === 'BLOCKED_NO_BOM' ? '보류' : num(material.shortage_qty)}<small>소요 계산</small></div><div class="mk-kpi-sub">${esc(material.reason ?? '확인된 계산 근거가 없습니다.')}</div>${button('BOM 원본 등록','bom')}</article>
      <article class="mk-kpi">${headerCard(`${purchase.label ?? '구매'} · ${purchase.item_id ?? ''}`)}<div class="mk-big">${num(purchase.order_qty)}<small>개 발주</small></div><div class="mk-purchase-stage">${Array.from({length:4},(_,i) => `<i${i<=purchase.stage_index ? ' class="on"' : ''}></i>`).join('')}</div><div class="mk-kpi-sub">${esc(purchase.document_status ?? '발주 없음')}</div><div class="mk-kpi-sub">${purchase.shortage_with_defect_qty == null ? '불량률 반영 소요 계산 보류' : `불량률 반영 시 부족 ${num(purchase.shortage_with_defect_qty)}개`}</div></article>
      <article class="mk-kpi">${headerCard(inspection.label ?? '수입검사')}<div class="mk-big">${num(inspection.pending_qty)}<small>개 대기</small></div><div class="mk-inspection-meter"><i style="width:${pct(inspection.passed_qty, Number(inspection.passed_qty)+Number(inspection.pending_qty))}%"></i></div><div class="mk-small-legend"><span class="warn">대기 ${num(inspection.pending_qty)}</span><span class="good">합격 ${num(inspection.passed_qty)}</span></div><div class="mk-kpi-sub">${esc(inspection.note ?? '')}</div></article>
    </section>`;
  }

  function renderMaterials() {
    const plan = planning.bases?.[basis] ?? {};
    if (planning.state === 'BLOCKED_NO_BOM') return `<div class="mk-empty"><b>BOM이 없어 계산할 수 없습니다</b><p>${esc(material.production_job_id)}의 조립 구성 ${esc(bom.configuration_id)}(${esc(bom.product_model_id)} Rev ${esc(bom.revision)} ${esc(bom.assembly_variant)})이 아직 등록되지 않았습니다. 원본 BOM을 등록하면 품목별 충족률이 여기에 표시됩니다.</p>${button('BOM 원본 등록','bom',true)}</div>`;
    if (!(plan.lines?.length)) return `<div class="mk-empty"><b>조회된 자재 근거가 없습니다</b><p>미확인 수량은 0으로 계산하지 않습니다.</p></div>`;
    return `<div class="mk-material-lines">${plan.lines.map(line => `<div><span>${esc(line.item_id)}</span><div class="mk-inspection-meter"><i style="width:${pct(line.available_qty,line.required_qty)}%"></i></div><span>${num(line.available_qty)} / ${num(line.required_qty)}</span></div>`).join('')}</div>`;
  }

  function renderRoundBars() {
    const max = Math.max(1,...rows.map(row => Number(row.promised_qty)||0));
    return rows.map(row => `<button type="button" class="mk-round-bar" data-select="${esc(row.delivery_round_id)}"><span class="mk-round-label">${esc(row.delivery_round_id)}<small>${esc(row.customer_name)} · ${num(row.promised_qty)}대</small></span><span class="mk-bar-width" style="width:${pct(row.promised_qty,max)}%">${bars(row)}</span><span class="mk-round-remaining">${num(row.promise_remaining_qty)}</span></button>`).join('');
  }

  function renderRounds() {
    const visible = rows.filter(row => filter === 'all' || (filter === 'done' ? row.promise_remaining_qty === 0 : row.promise_remaining_qty > 0));
    return `<div class="mk-table-scroll"><table class="mk-round-table"><thead><tr><th>계약 · 회차</th><th>보드 / 개정 · 구성</th><th>도착기한</th><th>약속 / 실제 도착</th><th>남은 약속</th><th>현재 상태</th><th>다음 조치</th><th>담당</th></tr></thead><tbody>${visible.map(row => `<tr${selected === row.delivery_round_id ? ' class="selected"' : ''}>
      <td><button type="button" class="mk-link" data-graph="${esc(row.delivery_round_id)}">${esc(row.delivery_round_id.replace(/-\d{2}$/,''))}</button><small>${esc(row.contract_line_id)} · ${num(row.round_no)}차 · ${esc(row.customer_name)}</small></td>
      <td><div class="mk-board"><span class="mk-photo">실물 사진<br>미등록</span><span><strong>${esc(row.board_label)}</strong><small>${esc(row.revision_label)} · ${esc(row.configuration_id)}</small></span></div></td>
      <td class="mk-date">${esc(shortDate(row.arrival_due_date))}<small${row.days_to_due <= 4 ? ' class="mk-danger"' : ''}>D-${num(row.days_to_due)}</small></td>
      <td class="mk-numeric">${num(row.promised_qty)} / <b>${num(row.arrived_qty)}</b>${bars(row,row.promised_qty,true)}</td>
      <td class="mk-numeric"><b>${num(row.promise_remaining_qty)}</b><small>생산 ${num(row.production_remaining_qty)}</small></td>
      <td>${tag(row.status?.label ?? '미확인',row.status?.tone)}</td><td>${esc(row.next_action?.label ?? '미확인')}</td><td class="mk-muted">${esc(row.owner_role)}</td>
    </tr>`).join('') || '<tr><td colspan="8" class="mk-no-rows">해당 상태의 회차가 없습니다.</td></tr>'}</tbody></table></div>
    <div class="mk-table-footer">${num(visible.length)}개 표시 · 전체 ${num(rows.length)} 회차　기준 ${esc(session.business_date ?? '')} · 단위: 대　원장 v${num(version)} 기준 · 합성 데이터 <span class="mk-inline-legend">${segmentLabels.slice(0,4).map((label,i) => `<span><i class="${segmentClass[i]}"></i>${i===3?'양품 미입고':label}</span>`).join('')}</span></div>`;
  }

  function render() {
    const baseQty = planning.bases?.production_remaining?.base_qty;
    const events = bundle.ledger?.data?.events_initial ?? [];
    container.innerHTML = `<div class="mk-dashboard-heading"><h1>운영 대시보드</h1><span>${esc(dateLabel())}</span><label class="mk-live"><input type="checkbox" disabled title="원장 사건 서비스 미연결">실시간 사건 반영</label></div>
      <div class="mk-config-banner"><b>구성 미등록</b><span>${esc(delivery.delivery_round_id)}의 조립 구성 <strong>${esc(bom.configuration_id)}</strong>(${esc(bom.product_model_id)} Rev ${esc(bom.revision)} ${esc(bom.assembly_variant)})에 BOM이 없어 <strong>자재 계산을 보류</strong>합니다.</span>${button('BOM 원본 등록','bom',true)}</div>
      ${renderKpis()}
      <div class="mk-viz-grid"><section class="mk-card"><h3>자재 충족률 · ${esc(planning.production_job_id)}<span class="mk-spacer"></span><span class="mk-toggle" role="group" aria-label="계산 기준"><button type="button" data-basis="production_remaining" aria-pressed="${basis==='production_remaining'}">남은 생산 ${num(baseQty)}대</button><button type="button" data-basis="defect_adjusted" aria-pressed="${basis==='defect_adjusted'}">불량률 반영</button></span></h3><div data-region="materials">${renderMaterials()}</div></section>
      <section class="mk-card"><h3>회차별 이행 단계<span class="mk-spacer"></span>${tag('단위: 대')}</h3><div>${renderRoundBars()}</div><p class="mk-small-note">오른쪽 숫자는 남은 약속(미도착). 막대 길이는 약속 수량에 비례</p><div class="mk-inline-legend">${segmentLabels.map((label,i)=>`<span><i class="${segmentClass[i]}"></i>${label}</span>`).join('')}</div></section></div>
      <section class="mk-card mk-rounds-card"><h3>계약 회차별 이행 현황 ${tag(num(rows.length))}<span class="mk-spacer"></span><span class="mk-toggle" role="group" aria-label="회차 상태">${[['all','전체'],['open','미완료'],['done','납품 완료']].map(([id,label])=>`<button type="button" data-filter="${id}" aria-pressed="${filter===id}">${label} ${num(roundsData.tabs?.[id])}</button>`).join('')}</span></h3><div data-region="rounds">${renderRounds()}</div></section>
      <div class="mk-lower-grid"><section class="mk-card"><h3>원장 사건<span class="mk-spacer"></span>${tag(`원장 v${num(version)}`,'info')}</h3><ul class="mk-event-list">${events.length ? events.map(event=>`<li><span>${esc(event.time ?? '')}</span><span>${esc(event.summary ?? event.description ?? '')}</span></li>`).join('') : '<li><span>–</span><span>새 사건을 기다리는 중</span></li>'}</ul></section>
      <section class="mk-card"><h3>${esc(purchase.item_id)} 확보 진행<span class="mk-spacer"></span>${tag(purchase.document_status ?? '발주 없음')}</h3><div class="mk-supply-text">아직 ${esc(purchase.item_id)} 구매 요청·발주가 없습니다. 오른쪽 에이전트에게 회차 가능 여부를 물어보세요.</div></section></div>`;
  }

  function closeDrawer() {
    if (!drawer) return;
    drawer.remove();
    drawer = null;
    previousFocus?.focus();
    previousFocus = null;
  }

  function openDrawer(title, subtitle, content, steps='') {
    closeDrawer();
    previousFocus = document.activeElement;
    drawer = document.createElement('div');
    drawer.className = 'mk-drawer-wrap';
    drawer.innerHTML = `<div class="mk-drawer-scrim" data-close></div><section class="mk-drawer" role="dialog" aria-modal="true" aria-labelledby="mk-drawer-title"><header><div><div class="mk-drawer-eyebrow">${esc(subtitle)}</div><h2 id="mk-drawer-title">${esc(title)}</h2></div><button type="button" class="mk-btn" data-close>닫기</button></header>${steps}<div class="mk-drawer-body">${content}</div></section>`;
    document.body.append(drawer);
    drawer.addEventListener('click',event=>{
      if(event.target.closest('[data-close]')) closeDrawer();
      const step = event.target.closest('[data-bom-step]');
      if(step) openBom(Number(step.dataset.bomStep));
      if(event.target.closest('[data-preview-source]')) openBom(2);
    },{signal});
    drawer.querySelector('button[data-close]').focus();
  }

  function openBom(step=0) {
    const source = bundle.bom?.source_registry?.sources?.find(entry => entry.revision === bom.revision) ?? bundle.bom?.source_registry?.sources?.[0] ?? {};
    const extraction = bundle.bom?.extraction_L1 ?? {};
    const stages = ['원본 접수','개정 식별','추출·검증','L 대비 차이','품목 연결','등록 초안','검토·승인','반영'];
    const stagesHtml = `<nav class="mk-drawer-steps" aria-label="BOM 화면 미리보기">${stages.map((label,i)=>`<button type="button" data-bom-step="${i}" aria-current="${i===step ? 'step':'false'}" title="해당 단계의 참조 화면 보기 · 원장 변경 없음">${label}</button>`).join('')}</nav>`;
    const note = '<p class="mk-preview-notice">비공개 참조 화면 · 원장 미연결. 단계 선택은 화면 열람이며 등록·검수·승인·반영을 실행하지 않습니다.</p>';
    let content;
    if(step === 0) content = `<div class="mk-drop"><b>BOM 원본 파일을 끌어다 놓거나 선택하세요</b><span>ODS · CSV · PDF. 파일의 SHA-256으로 보관된 원본과 개정을 식별합니다.</span><button type="button" class="mk-btn" disabled title="제한 파일 처리·원장 서비스 미연결">파일 선택</button></div><div class="mk-source-line"><button type="button" class="mk-btn primary" data-preview-source>보관 원본 미리보기 · ${esc(source.file_name ?? '자료 없음')}</button><span>공개 저장소 커밋 ${esc(source.repository_commit?.slice(0,8) ?? '미확인')} · ${esc(source.license ?? '미확인')}</span></div>${note}`;
    else if(step === 1) content = `${note}<section class="mk-drawer-block"><h3>원본 식별 정보</h3><dl class="mk-key-values">${[['파일',source.file_name],['제품',source.product_model_id],['개정',source.revision],['조립 변형',source.assembly_variant],['SHA-256',source.sha256],['식별 근거',extraction.identification_evidence]].map(([label,value])=>`<dt>${label}</dt><dd>${esc(value ?? '미확인')}</dd>`).join('')}</dl></section>`;
    else if(step === 2) content = `${note}<section class="mk-drawer-block"><h3>추출·검증 참조 결과</h3><div class="mk-bom-stats">${[['원문 행',extraction.summary?.rows_total],['위치',extraction.summary?.positions_total],['실장 미확인',extraction.summary?.placement_unknown_rows],['품목 연결',extraction.summary?.item_links]].map(([label,value])=>`<div><b>${num(value)}</b><span>${label}</span></div>`).join('')}</div><p>이 결과는 보관된 시연 자료이며 현재 서비스의 파싱·검증 실행 결과가 아닙니다.</p><div class="mk-table-scroll"><table><thead><tr><th>원문 행</th><th>위치</th><th>수량</th><th>원문 값</th><th>패키지</th><th>실장</th></tr></thead><tbody>${(extraction.rows ?? []).map(row=>`<tr><td>${num(row.source_row)}</td><td>${esc(row.refdes?.join(' '))}</td><td>${num(row.qty)}</td><td>${esc(row.raw_value)}</td><td>${esc(row.package)}</td><td>${tag(row.placement==='FIT'?'실장':'미확인',row.placement==='FIT'?'ok':'warn')}</td></tr>`).join('')}</tbody></table></div></section>`;
    else if(step === 3) content = `${note}<section class="mk-drawer-block"><h3>개정 차이 · 참조 자료</h3><table><thead><tr><th>위치</th><th>이전 개정</th><th>선택 개정</th></tr></thead><tbody>${(extraction.diff_vs_L ?? []).map(row=>`<tr><td>${esc(row.refdes)}</td><td>${esc(row.rev_L)}</td><td>${esc(row.rev_L1)}</td></tr>`).join('')}</tbody></table></section>`;
    else if(step === 4) content = `${note}<section class="mk-drawer-block"><h3>품목 연결 후보</h3><p>후보는 기술 검수·승인된 제조 사실과 구분합니다.</p><table><thead><tr><th>원문 행</th><th>위치</th><th>사내 품목</th><th>대당 수량</th></tr></thead><tbody>${(extraction.item_link_candidates ?? []).map(row=>`<tr><td>${num(row.source_row)}</td><td>${esc(row.refdes?.join(' '))}</td><td>${esc(row.item_id)}</td><td>${num(row.qty_per_board)}</td></tr>`).join('')}</tbody></table></section>`;
    else content = `${note}<section class="mk-drawer-block"><h3>${stages[step]}</h3><p>작성·검토·정확 버전 승인·원장 반영은 서버 신원과 권한을 확인한 뒤 수행해야 합니다.</p><p>현재 이 미리보기에서는 업무 상태를 변경하지 않습니다.</p><button type="button" class="mk-btn primary" disabled>${stages[step]} · 서비스 미연결</button></section>`;
    openDrawer(`${bom.product_model_id ?? ''} Rev ${bom.revision ?? ''} ${bom.assembly_variant ?? ''}`,'제품 구성 · BOM 원본 등록',content,stagesHtml);
  }

  function quoteContent() {
    const response = bundle.purchase?.quotations ?? {};
    const data = response.data ?? {};
    const cards = (data.quotations ?? []).map(quote => {
      const needBy = quote.parts_need_by_date ?? data.parts_need_by_date;
      const needByLabel = quote.meets_need_by === true ? `필요일 ${shortDate(needBy)} 충족` : quote.meets_need_by === false ? '필요일 초과' : '필요일 미확인';
      const needByTone = quote.meets_need_by === true ? 'ok' : quote.meets_need_by === false ? 'bad' : 'warn';
      const cells = [
        ['주문 수량',num(quote.order_qty)],
        ['단가',num(quote.unit_price)],
        ['상품 금액',num(quote.goods_amount)],
        ['운송비',num(quote.freight)],
        ['총액',num(quote.total_amount)],
        ['입하 가정',shortDate(quote.assumed_arrival_date)]
      ];
      return `<article class="mk-quote-card"><div class="mk-quote-heading"><b>${esc(quote.supplier_name)}</b><span class="mk-quote-id">${esc(quote.quote_id)}</span>${tag(needByLabel,needByTone)}${quote.excess_qty > 0 ? tag(`MOQ로 ${num(quote.excess_qty)}개 초과`,'warn') : ''}${quote.freight == null ? tag('운송비 미확인','warn') : ''}</div><div class="mk-quote-metrics" aria-label="견적 조건 · 금액 단위 ${esc(quote.currency ?? '미확인')}">${cells.map(([label,value])=>`<span>${label}<b>${esc(value)}</b></span>`).join('')}</div><p>${esc(quote.comparison_note)}</p><button class="mk-btn" type="button" disabled title="견적 선택·발주 초안 서비스 미연결로 실행할 수 없습니다.">이 견적으로 발주 초안</button></article>`;
    }).join('');
    return `<div class="mk-quote-list" data-reference-ledger-version="${esc(response.meta?.ledger_version)}">${cards || '<p>조회된 견적이 없습니다.</p>'}</div><p class="mk-preview-notice">참조 견적 카드 · 선택·발주 초안 서비스 미연결</p>`;
  }

  function renderQuotes(target) {
    // Mounted by the shell inside the original right AI panel, never a new page.
    target.innerHTML = quoteContent();
    target.classList.add('mk-quote-preview');
  }

  container.addEventListener('click',event=>{
    const action = event.target.closest('[data-action]')?.dataset.action;
    if(action==='bom') openBom();
    const basisButton = event.target.closest('[data-basis]');
    if(basisButton) { basis=basisButton.dataset.basis; render(); }
    const tab = event.target.closest('[data-filter]');
    if(tab) { filter=tab.dataset.filter; render(); }
    const selection = event.target.closest('[data-select]')?.dataset.select;
    if(selection) { selected=selection; onSelect(selection); render(); }
    const graph = event.target.closest('[data-graph]')?.dataset.graph;
    if(graph) { selected=graph; onSelect(graph); onGraph(graph); }
    const ask = event.target.closest('[data-ask]')?.dataset.ask;
    if(ask) onAsk(ask);
  },{signal});
  document.addEventListener('keydown',event=>{
    if(!drawer) return;
    if(event.key==='Escape') { event.preventDefault();closeDrawer(); }
    if(event.key==='Tab') {
      const targets=[...drawer.querySelectorAll('button:not(:disabled),[href],input:not(:disabled),[tabindex="0"]')];
      const first=targets[0],last=targets.at(-1);
      if(event.shiftKey && document.activeElement===first) { event.preventDefault();last?.focus(); }
      else if(!event.shiftKey && document.activeElement===last) { event.preventDefault();first?.focus(); }
    }
  },{signal});
  render();
  return {
    select(id) { selected=id; render(); },
    openBom,
    renderQuotes,
    destroy() { closeDrawer(); abort.abort(); container.replaceChildren();container.classList.remove('mk-dashboard'); }
  };
}
