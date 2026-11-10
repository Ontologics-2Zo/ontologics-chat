// Presentation only. Every displayed fact is supplied by the authenticated ledger read model.
// Explicit BOM commands are delegated to the authenticated server through the BOM drawer.
import { createBomDrawer } from './bom.js';
import { mountProcurementFlow } from './procurement.js';
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
const eventCursors = new WeakMap();
const eventLabels = { OPENING_BALANCE_IMPORTED: '합성 기초 원장 이관', BomRegistered: 'BOM 원본 등록 반영',
  ProcurementPosted: '구매 흐름 문서 원장 반영', EvidenceDocumentPosted: '근거 문서 원장 반영', InventoryReserved: '재고 예약 원장 반영' };
const eventOriginLabels = { synthetic_initial: '합성 기초 이관', actual_source_registration: '실제 원본 등록',
  internal_development: '내부 개발 원장', unknown: '출처 확인 필요' };
const eventDocumentLabels = { BOM: 'BOM 등록', EVIDENCE_NOTE: '근거 문서', QUOTE: '견적', PR: '구매 요청', PO: '발주',
  SUPPLY_CONFIRMATION: '내부 모의 공급 확인', RECEIPT: '입하', IQC: '수입검사', CANCEL: '취소', RESERVATION: '재고 예약' };
const eventVersion = value => Number.isSafeInteger(value) && value > 0;
const eventLabel = (labels, key) => Object.hasOwn(labels, key) ? labels[key] : null;

export function formatLedgerEventTime(value) {
  // Require an explicit zone; a browser-local interpretation would change the
  // meaning of an unzoned server value. Reject dates Date.parse would normalize.
  const match = typeof value === 'string' && /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  const unknown = { short: '—', full: '기록 시각 미확인', datetime: null };
  if (!match) return unknown;
  const [, y, mo, d, h, mi, sec, zone] = match;
  const year = Number(y), month = Number(mo), day = Number(d);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (month < 1 || month > 12 || day < 1 || day > days[month - 1] || Number(h) > 23 || Number(mi) > 59
      || Number(sec) > 59 || (zone !== 'Z' && (Number(zone.slice(1, 3)) > 23 || Number(zone.slice(4)) > 59))) return unknown;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return unknown;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Seoul',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
    .formatToParts(date).map(part => [part.type, part.value]));
  return { short: `${parts.hour}:${parts.minute}`,
    full: `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} Asia/Seoul · 원본 ${value}`, datetime: value };
}

export function ledgerEventFreshIds(previous, snapshot) {
  const current = snapshot.meta || {};
  if (!previous || !current.company_id || !current.identity_revision || current.company_id !== previous.company_id
      || current.identity_revision !== previous.identity_revision || !eventVersion(previous.ledger_version)
      || !eventVersion(current.ledger_version) || current.ledger_version <= previous.ledger_version) return new Set();
  return new Set((snapshot.events || []).filter(event => typeof event.event_id === 'string' && eventVersion(event.ledger_version)
    && event.ledger_version > previous.ledger_version && event.ledger_version <= current.ledger_version).map(event => event.event_id));
}

export function renderLedgerEvents(snapshot, { freshIds = new Set() } = {}) {
  const version = snapshot.meta?.ledger_version;
  const supplied = Array.isArray(snapshot.events) ? snapshot.events : [];
  const events = supplied.filter(event => event && typeof event === 'object'
    && !(eventVersion(version) && eventVersion(event.ledger_version) && event.ledger_version > version));
  const total = Number.isSafeInteger(snapshot.event_count) && snapshot.event_count >= events.length ? snapshot.event_count : null;
  const detail = (name, value) => `<dt>${esc(name)}</dt><dd>${esc(value ?? '미확인')}</dd>`;
  const rows = events.map(event => {
    const time = formatLedgerEventTime(event.recorded_at);
    const summary = typeof event.summary === 'string' && event.summary.trim() ? event.summary
      : eventLabel(eventLabels, event.event_type) || `원장 사건 · ${event.event_type || '유형 미확인'}`;
    const document = event.document_ref;
    const reference = document && typeof document === 'object' ? detail('문서 종류', eventLabel(eventDocumentLabels, event.document_kind) || event.document_kind || document.type)
      + detail('문서 ID', document.id) + detail('문서 버전', eventVersion(document.version) ? `v${document.version}` : null)
      + detail('문서 hash', document.payload_hash) : '';
    const reservation = event.reservation && typeof event.reservation === 'object' ? detail('예약 수요', event.reservation.demand_ref)
      + detail('품목 / LOT', `${event.reservation.item_id ?? '미확인'} / ${event.reservation.lot_id ?? '미확인'}`)
      + detail('예약 수량', event.reservation.quantity) + detail('가용 변화', `${event.reservation.available_before} → ${event.reservation.available_after}`)
      + detail('예약 hash', event.reservation.payload_hash) : '';
    return `<li data-ledger-event-id="${esc(event.event_id)}"${freshIds.has(event.event_id) ? ' class="mk-event-fresh"' : ''}><time class="mk-event-time"${time.datetime ? ` datetime="${esc(time.datetime)}"` : ''} title="${esc(time.full)}" aria-label="${esc(time.full)}">${esc(time.short)}</time><span class="mk-event-version" aria-label="원장 버전 ${esc(eventVersion(event.ledger_version) ? event.ledger_version : '미확인')}">${eventVersion(event.ledger_version) ? `v${event.ledger_version}` : '미확인'}</span><details class="mk-event-detail"><summary aria-label="${esc(summary)} · 사건 근거 보기">${esc(summary)}</summary><dl>${detail('사건 ID', event.event_id)}${detail('사건 유형', event.event_type)}${detail('기록 시각', time.full)}${detail('출처', eventLabel(eventOriginLabels, event.origin) || '출처 확인 필요')}${reference}${reservation}</dl></details></li>`;
  }).join('');
  return `<section class="mk-card" data-ledger-events><h3>원장 사건 ${tag(`${events.length} / ${total === null ? '미확인' : num(total)}`)}<span class="mk-spacer"></span>${tag(`원장 v${eventVersion(version) ? num(version) : '미확인'}`, 'info')}</h3><ul class="mk-event-list" data-ledger-event-list aria-label="확정 원장 사건">${rows || '<li class="mk-event-empty"><span>—</span><span>—</span><span>조회된 원장 사건이 없습니다.</span></li>'}</ul>${total !== null && total > events.length ? `<p class="mk-small-note">최근 ${num(events.length)}건 표시 · 전체 ${num(total)}건</p>` : ''}${supplied.length !== events.length ? '<p class="mk-small-note">조회 버전에 포함되지 않은 사건은 표시하지 않았습니다.</p>' : ''}</section>`;
}

export function createLedgerEventRenderer(snapshot, previous) {
  let freshIds = ledgerEventFreshIds(previous, snapshot);
  return () => {
    const result = renderLedgerEvents(snapshot, { freshIds });
    // Local repainting must not announce an old ledger commit as new again.
    freshIds = new Set();
    return result;
  };
}
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

export function renderMaterialTableRows(items, { planned = false, targetQty } = {}) {
  const qtyKey = planned ? 'planned_required_qty' : 'required_qty';
  const shortageKey = planned ? 'planned_shortage_qty' : 'shortage_qty';
  const statusKey = planned ? 'planned_quantity_status' : 'quantity_status';
  const labels = { COVERED: '계산상 충족', SHORTAGE: '수량 부족', UNKNOWN: '판정 보류' };
  return items.map(item => `<tr><td><button type="button" class="mk-link" data-graph="${esc(item.item_entity_id)}">${esc(item.item_id)}</button><small>${esc(item.item_name || '품목명 미확인')}</small><small>단위: ${esc(item.unit_label || item.unit || '미확인')}</small></td>
    <td>${num(item.quantity_per_unit)}<small>제품 1대 기준</small></td>
    <td>${num(item[qtyKey])}<small>${num(targetQty)}대 × ${num(item.quantity_per_unit)}</small></td>
    <td>${num(item.available_qty)}<small>${esc(item.inventory_scope_label || '재고 범위 미확인')}</small></td>
    <td>${num(item[shortageKey])}</td>
    <td>${esc(labels[item[statusKey]] || '판정 보류')}<small>${esc(item.conditional ? '시연 입력 · 기술검수 대기' : '적용 판단 근거 확인')}</small><small>원문 ${num(item.row_ids?.length)}행 합산</small>${item.issues?.length ? '<small>입력 근거 확인 필요</small>' : ''}</td></tr>`).join('');
}

function materialDecimal(value) {
  const text = typeof value === 'number' && Number.isFinite(value) ? formatQuantity(value).replaceAll(',', '') : value;
  if (typeof text !== 'string' || text.length > 256 || !/^\d+(?:\.\d+)?$/.test(text)) return null;
  const [whole, fraction = ''] = text.split('.');
  return { value: BigInt(whole + fraction), scale: 10n ** BigInt(fraction.length) };
}

/** Display-only ratio. Server statuses and exact shortages remain authoritative. */
export function materialCoverage(item, { planned = false } = {}) {
  const required = materialDecimal(item[planned ? 'planned_required_qty' : 'required_qty']);
  const available = materialDecimal(item.available_qty);
  const shortage = materialDecimal(item[planned ? 'planned_shortage_qty' : 'shortage_qty']);
  const status = item[planned ? 'planned_quantity_status' : 'quantity_status'];
  const unknown = { state: 'unknown', width: null, label: '미확인' };
  if (!required || !available || !shortage || !['COVERED', 'SHORTAGE'].includes(status)) return unknown;
  if (required.value === 0n) return status === 'COVERED' && shortage.value === 0n
    ? { state: 'not_required', width: null, label: '소요 없음' } : unknown;
  const stock = available.value * required.scale, need = required.value * available.scale;
  if ((status === 'SHORTAGE') !== (stock < need) || (status === 'SHORTAGE') !== (shortage.value > 0n)) return unknown;
  const points = stock * 10000n / need;
  const width = Number(points > 10000n ? 10000n : points) / 100;
  const percent = stock * 100n / need;
  const label = stock === 0n ? '0%' : percent === 0n ? '1% 미만'
    : percent > 999n ? '999% 초과' : `${percent}%`;
  return { state: status === 'SHORTAGE' ? 'shortage' : 'covered', width, label };
}

export function renderMaterialCoverage(items, { planned = false } = {}) {
  if (!items.length) return '';
  const requiredKey = planned ? 'planned_required_qty' : 'required_qty';
  const shortageKey = planned ? 'planned_shortage_qty' : 'shortage_qty';
  const rows = items.map(item => {
    const coverage = materialCoverage(item, { planned });
    const ratio = `${num(item.available_qty)}/${num(item[requiredKey])}`;
    const caption = `가용 ${num(item.available_qty)} / 필요 ${num(item[requiredKey])} ${item.unit_label || item.unit || '단위 미확인'} · ${coverage.state === 'shortage' ? `부족 ${num(item[shortageKey])}` : coverage.label}${item.conditional ? ' · 시연 적용 가정' : ''}`;
    return `<div class="mk-material-row" data-material-item="${esc(item.item_id)}" data-coverage-state="${coverage.state}"><div class="mk-material-label">${item.item_entity_id ? `<button type="button" class="mk-link" data-graph="${esc(item.item_entity_id)}">${esc(item.item_id)}</button>` : esc(item.item_id)}<small>${esc(item.item_name || '품목명 미확인')}</small></div><div class="mk-material-track ${coverage.state}" role="img" tabindex="0" aria-label="${esc(caption)}" title="${esc(caption)}">${coverage.width === null ? '' : `<i style="width:${coverage.width}%"></i>`}<span class="mk-material-tick"></span></div><div class="mk-material-value"><b>${coverage.state === 'shortage' ? `−${num(item[shortageKey])}` : esc(coverage.label)}</b><span>${ratio}</span></div></div>`;
  }).join('');
  return `<div class="mk-material-bars" aria-label="품목별 자재 충족률">${rows}</div><div class="mk-material-axis" aria-hidden="true"><span></span><div><span>0%</span><span>50%</span><span>100% = 필요량</span></div><span></span></div>`;
}

/** Server-computed reservation impact (same snapshot); never a browser recalculation. */
export function renderReservationImpact(snapshot) {
  const impacts = Array.isArray(snapshot.reservation_impacts) ? snapshot.reservation_impacts : [];
  if (!impacts.length) return '';
  const latest = impacts[impacts.length - 1];
  const first = impacts.find(row => row.item_id === latest.item_id) || latest;
  const unit = latest.unit === 'EA' ? '개' : ` ${latest.unit}`;
  return `<div class="mk-config-banner mk-change-banner" data-reservation-impact><b>근거 변경</b><span>다른 수요 ${esc(latest.demand_ref)} 예약 +${num(latest.quantity)}${esc(unit)} · ${esc(latest.item_id)} 가용 ${num(first.available_before)}→${num(latest.available_after)} · 계획 부족 ${num(first.planned_shortage_before)}→${num(latest.planned_shortage_after)}${esc(unit)} · 기존 확보안 재검토 필요 · 원장 v${num(latest.ledger_version)}</span>${button('변경 원인 질문', 'ask-change')}</div>`;
}

function reservationForm(snapshot) {
  const lots = (snapshot.inventory || []).filter(row => row.scope === 'LOT' && Number(row.available_qty) > 0);
  return `<section class="mk-card" data-reservation-form><h3>다른 주문 재고 예약<span class="mk-spacer"></span>${tag('합성 시연 · 원장 반영', 'warn')}</h3>
    <form class="mk-reserve-form"><label>LOT<select name="lot">${lots.map(row => `<option value="${esc(row.lot_id)}" data-item="${esc(row.item_id)}">${esc(row.lot_id)} · ${esc(row.item_id)} · 가용 ${num(row.available_qty)}</option>`).join('')}</select></label>
    <label>수량<input name="quantity" type="number" min="1" step="1" value="5" required></label>
    <label>다른 수요<input name="demand" value="ORD-26042-01" pattern="[A-Za-z0-9][A-Za-z0-9._-]*" required></label>
    <label class="mk-reserve-note">메모<input name="note" value="다른 합성 주문의 공용 품목 예약" minlength="8" required></label>
    <button type="submit" class="mk-btn primary">예약 반영</button></form>
    <p class="mk-small-note">재고 이동·소비가 아니라 다른 수요 예약만 늘립니다. 현재 원장 v${num(snapshot.meta?.ledger_version)} 기준으로 서버가 권한·가용·버전을 다시 확인합니다.</p><p class="mk-small-note" data-reserve-status role="status"></p></section>`;
}

export function mountDashboard(container, { snapshot, onSelect = () => {}, onAsk = () => {}, onGraph = () => {}, api, getIdentity, onPosted, onRefresh, live = null, onShowBomGraph = null }) {
  const summary = snapshot.summary || {}, meta = snapshot.meta || {};
  const previousEvents = eventCursors.get(container);
  const renderEvents = createLedgerEventRenderer(snapshot, previousEvents);
  const sameEventScope = previousEvents?.company_id === meta.company_id && previousEvents?.identity_revision === meta.identity_revision;
  eventCursors.set(container, { company_id: meta.company_id, identity_revision: meta.identity_revision,
    ledger_version: sameEventScope && eventVersion(previousEvents?.ledger_version) && eventVersion(meta.ledger_version)
      ? Math.max(previousEvents.ledger_version, meta.ledger_version) : meta.ledger_version });
  const entities = snapshot.entities || [];
  const rows = (snapshot.rounds || []).map(row => ({ ...row, id: row.id || row.node_id || entities.find(entity => entity.type === 'DeliveryRound' && entity.display_id === row.delivery_round_id)?.id }));
  const inventory = (snapshot.inventory || []).map(row => ({ ...row, reserved_qty: row.reserved_qty ?? row.reserved_other_qty, display_id: row.lot_id || row.display_id || row.item_id, id: row.id || row.node_id || entities.find(entity => entity.type === (row.scope === 'LOT' ? 'PartLot' : 'InternalItem') && entity.display_id === (row.lot_id || row.item_id))?.id }));
  const version = meta.ledger_version;
  const inventoryBasis = meta.data_origins?.inventory === 'mixed_sources'
    ? '합성 초기 재고와 개발 입하·수입검사 원장' : '합성 초기 원장';
  const delivery = { ...summary, promised_qty: summary.promised_qty ?? summary.promise_qty,
    breakdown: Object.fromEntries(segmentKeys.map(key => [key, summary[key]])) };
  let selected = null, filter = 'all';
  const bom = createBomDrawer({ snapshot, api, getIdentity, onPosted, onRefresh, onShowGraph: onShowBomGraph });
  let procurement = null;
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
      <article class="mk-kpi">${headerCard('구매')}<div class="mk-big">${num(summary.purchase_order_qty)}<small>개 발주</small></div><div class="mk-purchase-stage">${Array.from({ length: 4 }, () => '<i></i>').join('')}</div><div class="mk-kpi-sub">${esc(summary.purchase_status || '등록된 구매 문서 없음 · 실제 발주 현황 별도')}</div><div class="mk-kpi-sub">확정 발주와 공급사 송신을 구분합니다.</div></article>
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
    const qtyKey = planned ? 'planned_required_qty' : 'required_qty';
    const targetQty = planned ? basisData.planned_input_qty : basisData.production_remaining_qty;
    const label = planned ? `계획 투입 ${num(basisData.planned_input_qty)}대 · 수율 ${esc(basisData.plan_yield)} 가정` : `추가 양품 목표 ${num(basisData.production_remaining_qty)}대 기준`;
    const policy = requirements.calculation_policy;
    const issueLabel = value => ({ UNRESOLVED: '적용 미확인', MAPPING_UNKNOWN: '품목 매핑 미확인', INVENTORY_UNKNOWN: '재고 미확인', APPLICATION_UNREVIEWED: '적용 미검수', ITEM_MAPPING_UNCONFIRMED: '품목 매핑 미확인', QUANTITY_UNCONFIRMED: '수량 미확인', UNIT_UNCONFIRMED: '단위 미확인', INVENTORY_UNIT_UNCONFIRMED: '재고 단위 미확인', INVENTORY_COVERAGE_AMBIGUOUS: '재고 범위 대조 필요', CALCULATED: '계산됨', APPLICABLE: '적용 가정', UNIT_UNKNOWN: '단위 미확인', NOT_MOUNTED: '비실장', SETTING: '설정', UNREVIEWED: '미검수', DEMO_ASSUMPTION: '시연 가정' }[value] || value);
    const chart = renderMaterialCoverage(requirementItems, { planned });
    const assumption = requirements.has_demo_assumptions || requirementItems.some(item => item.conditional);
    return `${chart || `<div class="mk-empty" data-material-pending><b>품목 연결·수량 검수가 필요합니다</b><p>계산 대상 ${num(requirementItems.length)}개 품목 · 원문 ${num(requirementRows.length)}행 중 적용 미확인 ${num(requirements.unresolved_count)}행</p>${button('BOM 원문·검수 근거', 'bom-evidence')}</div>`}
      <p class="mk-small-note">${planned ? `${label} · 양품 수량 보장 아님` : `남은 생산 ${num(targetQty)}대 × 1대당 소요량`}${chart ? ' · 100% 넘는 막대는 100%에서 자름' : ''}</p>
      <p class="mk-small-note mk-material-basis">${assumption ? tag('시연 적용 가정', 'warn') : ''}${tag(`재고 · ${inventoryBasis}`, 'info')}<span>적용 미확인 ${num(requirements.unresolved_count)}행 · 제조 가능성 미확인</span></p>
      <details class="mk-material-details"><summary>소요·재고 상세 · ${num(requirementItems.length)}개 품목</summary>
      <p class="mk-small-note"><b>전체 BOM 중 일부 품목 기준 · ${num(requirementItems.length)}개 품목</b> · ${label}. ${assumption ? '적용 수량·품목 연결은 시연 가정, ' : ''}재고는 ${esc(inventoryBasis)} 기준입니다. 원장 v${num(version)} / BOM 문서 v${num(requirements.bom_version)}.</p>
      <p class="mk-small-note">실제 BOM 원문 전체 ${num(requirementRows.length)}행은 아래 상세에 보존하며 이 계산을 전체 BOM 기술 검수·제조 가능 확정으로 보지 않습니다. ${planned ? '계획 수율은 실적이나 양품 수량 보장이 아닙니다.' : ''}</p>
      <p class="mk-small-note">품목 코드는 부품의 식별자이고, EA는 낱개를 세는 단위입니다. 아래 판정은 현재 입력값의 수량 비교이며 실제 제조 가능 판정과 구분합니다.</p>
      <div class="mk-table-scroll"><table class="mk-round-table" data-register="material-items"><thead><tr><th>품목 / 단위</th><th>대당 수량</th><th>합산 소요</th><th>가용</th><th>부족</th><th>수량 판정 / 검수</th></tr></thead><tbody>${renderMaterialTableRows(requirementItems, { planned, targetQty }) || '<tr><td colspan="6">계산 대상 품목이 아직 연결되지 않았습니다. 원문 상세에서 검수 근거를 확인할 수 있습니다.</td></tr>'}</tbody></table></div>
      <p class="mk-small-note">같은 품목을 사용하는 BOM 행은 먼저 합산하고 재고를 한 번만 차감합니다. 검사대기·다른 수요 예약은 가용으로 더하지 않습니다.</p></details>
      <details class="mk-material-details"><summary>원문 전체 ${num(requirementRows.length)}행 · 적용 검토와 계산 제외 근거 보기</summary>      <div class="mk-bom-stats"><div><b>${num(requirements.row_count)}</b><span>원문 전체 행</span></div><div><b>${num(requirements.applicable_count)}</b><span>적용 판단 행</span></div><div><b>${num(requirements.unresolved_count)}</b><span>적용 미확인 행</span></div><div><b>${num(requirements.unknown_shortage_count)}</b><span>부족량 미확인</span></div></div><div class="mk-table-scroll"><table class="mk-round-table" data-register="material-rows"><thead><tr><th>원문 근거</th><th>부품 위치</th><th>상태</th><th>대당 수량</th><th>검토 기준 소요</th><th>원문 × ${num(basisData.production_remaining_qty)}</th><th>원문 × ${num(basisData.planned_input_qty)}</th><th>미확인 / 가정</th></tr></thead><tbody>${requirementRows.map(row => `<tr><td><button type="button" class="mk-link" data-bom-evidence="${esc(row.row_id)}">${esc(row.locator || row.row_id)}</button></td><td>${esc((row.designators || []).join(', '))}</td><td>${esc(issueLabel(row.decision?.application_status || row.calculation_state))}</td><td>${num(row.decision?.quantity_per_unit)}</td><td>${num(row[qtyKey])}</td><td>${num(row.source_quantity_times_target)}</td><td>${num(row.source_quantity_times_plan)}</td><td>${esc(issueLabel(row.decision?.review_basis || 'UNREVIEWED'))}<small>${esc((row.issues || []).map(issueLabel).join(' · '))}</small></td></tr>`).join('')}</tbody></table></div>
      <p class="mk-small-note">원문 수량 단순 환산 · 단위/실장 검수 전. 원문의 수량 × 목표/계획 투입 수량이며 적용 소요·부족량과 구분합니다.</p></details>
      ${policy ? `<details class="mk-material-details"><summary>${esc(policy.label)} · 산식과 판정 기준</summary><p class="mk-small-note">현재 서비스에 적용된 계산 기준입니다. 사내 규정 승인과 실제 BOM·현장 재고 검수는 별도입니다.</p><ol class="mk-small-note">${(policy.rules || []).map(rule => `<li>${esc(rule.text)}</li>`).join('')}</ol><p class="mk-small-note">기준 버전: ${esc(policy.id)}</p></details>` : ''}`;
  }
  function render() {
    const date = meta.effective_at || meta.as_of;
    const dateLabel = meta.opening_effective_at ? `원장 기준 ${date} · 납품·생산 기초 기준 ${meta.opening_effective_at}`
      : date ? `기초 원장 기준 ${date} · ${requirements.bom_id ? '실제 BOM 원문 별도 등록' : '합성 초기 이관'}` : '합성 초기 이관 · 기준 시점 미확인';
    const canReserve = (getIdentity?.()?.permitted_actions || []).includes('inventory.reserve');
    container.innerHTML = `<div class="mk-dashboard-heading"><h1>운영 대시보드</h1><span>${esc(dateLabel)}</span><label class="mk-live"><input type="checkbox" data-live${live ? (live.get() ? ' checked' : '') : ' disabled'}>실시간 사건 반영</label></div>
      <div class="mk-config-banner"><b>${blockedBom ? '구성 미등록' : '계산 기준'}</b><span>${esc(statusText)}. 확인된 원장과 같은 버전의 근거로 판단합니다.</span>${button('BOM 원본 등록', 'bom', true)}</div>
      ${renderReservationImpact(snapshot)}${canReserve ? reservationForm(snapshot) : ''}
      ${renderKpis()}
      <div class="mk-viz-grid"><section class="mk-card" data-material-coverage><h3>자재 충족률${summary.production_job_id ? ` · ${esc(summary.production_job_id)}` : ''}<span class="mk-spacer"></span><span class="mk-toggle" role="group" aria-label="계산 기준"><button type="button" data-basis="production_remaining" aria-pressed="${basis === 'production_remaining'}">남은 생산 ${num(summary.production_remaining_qty)}대</button><button type="button" data-basis="planned" aria-pressed="${basis === 'planned'}">수율 반영 ${num(requirements.basis?.planned_input_qty)}대</button></span></h3><div data-region="materials">${materialContent()}</div></section>
      <section class="mk-card"><h3>회차별 이행 단계<span class="mk-spacer"></span>${tag('단위: 대')}</h3>${rows.map(row => `<button type="button" class="mk-round-bar" data-select="${esc(row.id || row.node_id)}"><span class="mk-round-label">${esc(row.delivery_round_id || row.display_id)}<small>${num(row.promised_qty ?? row.promise_qty)}대</small></span><span class="mk-bar-width">${bars(row, row.promised_qty ?? row.promise_qty)}</span><span class="mk-round-remaining">${num(row.promise_remaining_qty)}</span></button>`).join('')}<p class="mk-small-note">오른쪽 숫자는 남은 고객 도착 약속입니다.</p><div class="mk-inline-legend">${segmentLabels.map((label, i) => `<span><i class="${segmentClass[i]}"></i>${label}</span>`).join('')}</div></section></div>
      <section class="mk-card mk-rounds-card"><h3>계약 회차별 이행 현황 ${tag(num(rows.length))}<span class="mk-spacer"></span><span class="mk-toggle" role="group" aria-label="회차 상태">${[['all', '전체'], ['open', '미완료'], ['done', '납품 완료']].map(([id, label]) => `<button type="button" data-filter="${id}" aria-pressed="${filter === id}">${label}</button>`).join('')}</span></h3><div data-region="rounds">${roundContent()}</div></section>
      <div class="mk-lower-grid"><section class="mk-card"><h3>공급 흐름<span class="mk-spacer"></span>${tag("개발 원장 · 외부 송신 없음")}</h3><div data-region="procurement"></div></section>${renderEvents()}
      <section class="mk-card"><h3>품목별 재고<span class="mk-spacer"></span>${tag('원장 조회', 'info')}</h3><div class="mk-table-scroll"><table class="mk-round-table"><thead><tr><th>품목 / LOT</th><th>보유</th><th>예약</th><th>가용</th></tr></thead><tbody>${inventory.map(item => `<tr><td><button class="mk-link" type="button" data-graph="${esc(item.id || item.node_id)}">${esc(item.display_id || item.item_id)}</button></td><td>${num(item.on_hand_qty)}</td><td>${num(item.reserved_qty)}</td><td>${num(item.available_qty)}</td></tr>`).join('') || '<tr><td colspan="4">조회된 재고 근거가 없습니다.</td></tr>'}</tbody></table></div></section></div>
      <section class="mk-card"><h3>원장 객체 ${tag(num(entities.length))}<span class="mk-spacer"></span>${tag('동일 조회 버전')}</h3><div class="mk-table-scroll"><table class="mk-round-table" data-register="entities"><thead><tr><th>객체</th><th>종류</th><th>근거</th><th>상태</th></tr></thead><tbody>${entities.map(entity => `<tr><td><button type="button" class="mk-link" data-graph="${esc(entity.id)}">${esc(entity.display_id || entity.id)}</button></td><td>${esc(entity.type)}</td><td>${num(Array.isArray(entity.evidence) ? entity.evidence.length : entity.evidence ? 1 : entity.evidence_refs?.length)}</td><td>${esc(entity.attributes?.status || entity.attributes?.state || originLabel(entity.origin))}</td></tr>`).join('')}</tbody></table></div></section>`;
  }
  function openBom(step = 0, preferredId, expectedDocument = null) { bom.open(step, preferredId, null, expectedDocument); }
  container.addEventListener('change', event => { if (event.target.matches?.('[data-live]') && live) live.set(event.target.checked); }, { signal });
  container.addEventListener('submit', async event => {
    const form = event.target.closest?.('.mk-reserve-form');
    if (!form) return;
    event.preventDefault();
    const status = container.querySelector('[data-reserve-status]'), submit = form.querySelector('button[type="submit"]');
    const option = form.lot.selectedOptions[0];
    if (!option) { status.textContent = '예약할 수 있는 LOT가 없습니다.'; return; }
    submit.disabled = true; status.textContent = '서버에서 권한·가용·원장 버전을 확인하고 있습니다.';
    try {
      const result = await api('/api/mks2/inventory/reservations', { item_id: option.dataset.item, lot_id: form.lot.value,
        quantity: Number(form.quantity.value), demand_ref: form.demand.value.trim(), note: form.note.value.trim(),
        expected_ledger_version: version, idempotency_key: crypto.randomUUID() });
      status.textContent = `원장 v${result.ledger_version} 반영 · ${result.item_id} 가용 ${num(result.before.available_qty)}→${num(result.after.available_qty)}. 그래프 투영 후 화면이 갱신됩니다.`;
      onPosted?.();
    } catch (error) {
      status.textContent = ({ RESERVATION_LEDGER_VERSION_CHANGED: '그 사이 원장이 바뀌었습니다. 최신 원장을 조회한 뒤 다시 시도하세요.',
        RESERVATION_EXCEEDS_AVAILABLE: '예약 가능 수량을 넘습니다. 이 수요의 자기 예약과 보류 수량은 가져올 수 없습니다.',
        PERMISSION_DENIED: '현재 계정에는 재고 예약 권한이 없습니다.', RESERVATION_DEMAND_IS_TARGET: '선택 회차 자체는 다른 수요로 예약할 수 없습니다.' })[error.message]
        || `예약을 반영하지 못했습니다 (${error.message}).`;
      submit.disabled = false;
    }
  }, { signal });
  container.addEventListener('click', event => {
    if (event.target.closest('[data-action="ask-change"]')) { const impacts = snapshot.reservation_impacts || []; const latest = impacts[impacts.length - 1]; if (latest) onAsk(`왜 부족이 ${latest.planned_shortage_after}개로 늘었어?`); }
    if (event.target.closest('[data-action="bom"]')) openBom();
    const evidence = event.target.closest('[data-bom-evidence]');
    if (evidence) bom.open(4, requirements.bom_id, evidence.dataset.bomEvidence);
    else if (event.target.closest('[data-action="bom-evidence"]')) bom.open(2, requirements.bom_id);
    const selectedNode = event.target.closest('[data-select]')?.dataset.select;
    if (selectedNode) { selected = selectedNode; onSelect(selected); container.querySelector('[data-region="rounds"]').innerHTML = roundContent(); }
    const graphNode = event.target.closest('[data-graph]')?.dataset.graph;
    if (graphNode) { selected = graphNode; onSelect(selected); onGraph(selected); }
    const targetFilter = event.target.closest('[data-filter]')?.dataset.filter;
    if (targetFilter) {
      filter = targetFilter; container.querySelector('[data-region="rounds"]').innerHTML = roundContent();
      for (const node of container.querySelectorAll('[data-filter]')) node.setAttribute('aria-pressed', node.dataset.filter === filter);
    }
    const targetBasis = event.target.closest('[data-basis]')?.dataset.basis;
    if (targetBasis) {
      basis = targetBasis; container.querySelector('[data-region="materials"]').innerHTML = materialContent();
      for (const node of container.querySelectorAll('[data-basis]')) node.setAttribute('aria-pressed', node.dataset.basis === basis);
    }
  }, { signal });
  render();
  const supplyRoot = container.querySelector?.('[data-region="procurement"]');
  if (supplyRoot) procurement = mountProcurementFlow(supplyRoot, { snapshot, api, getIdentity, onPosted, onRefresh });
  return { select(id) { selected = id; container.querySelector('[data-region="rounds"]').innerHTML = roundContent(); }, openBom,
    destroy() { bom.destroy(); procurement?.destroy(); abort.abort(); container.replaceChildren(); container.classList.remove('mk-dashboard'); } };
}
