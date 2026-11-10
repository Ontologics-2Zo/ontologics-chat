// Existing supply-card flow. Commands stay explicit and server-authorized;
// this module never sends supplier messages or changes browser-only inventory.
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const kinds = { QUOTE: '견적 조건', PR: '구매 요청', PO: '내부 발주', SUPPLY_CONFIRMATION: '공급확인 · 모의', RECEIPT: '입하', IQC: '수입검사', CANCEL: '잔여 취소',
  PRODUCTION: '생산 실적', FG_RECEIPT: '완성품 입고', SHIPMENT: '출하', ARRIVAL: '고객 도착' };
const statuses = { DRAFT: '검토 전', REVIEWED: '검토 완료', APPROVED: '승인 · 반영 전', POSTED: '원장 반영' };
const fields = {
  QUOTE: ['quote_ref', 'supplier_id', 'item_id', 'unit', 'unit_price', 'currency', 'moq', 'order_multiple', 'freight', 'valid_until', 'lead_business_days', 'provenance', 'note'],
  PR: ['quote_id', 'quantity', 'material_basis', 'acknowledge_partial_bom', 'note'],
  PO: ['pr_id', 'quantity', 'acknowledge_unknown_freight', 'note'],
  SUPPLY_CONFIRMATION: ['po_id', 'confirmation_ref', 'confirmed_qty', 'note'],
  RECEIPT: ['po_id', 'receipt_ref', 'lot_code', 'quantity', 'note'],
  IQC: ['receipt_id', 'inspection_ref', 'passed_qty', 'failed_qty', 'note'],
  CANCEL: ['target_id', 'note'],
  // DEC-H49: named boards move production → finished goods → shipment → arrival.
  PRODUCTION: ['iqc_id', 'production_ref', 'board_serials', 'note'],
  FG_RECEIPT: ['production_id', 'receipt_ref', 'board_serials', 'note'],
  SHIPMENT: ['fg_receipt_id', 'shipment_ref', 'board_serials', 'note'],
  ARRIVAL: ['shipment_id', 'arrival_ref', 'board_serials', 'note'],
};
const nextStep = { PRODUCTION: ['FG_RECEIPT', 'production_id', 'PRODUCED', '완성품 입고 기록'],
  FG_RECEIPT: ['SHIPMENT', 'fg_receipt_id', 'RECEIVED', '출하 기록'], SHIPMENT: ['ARRIVAL', 'shipment_id', 'SHIPPED', '고객 도착 기록'] };
const labels = { production_ref: '생산 실적 식별자', shipment_ref: '출하 식별자', arrival_ref: '도착 식별자', board_serials: '보드 serial · 쉼표로 구분', quote_ref: '내부 견적 식별자', supplier_id: '공급사', item_id: '품목', unit: '단위', unit_price: '단가', currency: '통화', moq: '최소 주문량', order_multiple: '포장 배수', freight: '운임 · 미확인은 빈칸', valid_until: '유효기간', lead_business_days: '참고 리드타임 · 영업일', quantity: '수량', confirmation_ref: '모의 공급확인 식별자', confirmed_qty: '모의 확인 수량', receipt_ref: '입하 식별자', lot_code: '신규 LOT 식별자', inspection_ref: '검사 식별자', passed_qty: '합격 수량', failed_qty: '불합격 수량', note: '검토 근거 · 조건', reason: '반려 사유' };
const numberFields = new Set(['moq', 'order_multiple', 'quantity', 'confirmed_qty', 'passed_qty', 'failed_qty', 'lead_business_days']);
const button = (label, action, disabled = false, extra = '') => `<button type="button" class="mk-btn" data-purchase-action="${action}" ${extra}${disabled ? ' disabled' : ''}>${esc(label)}</button>`;
const errors = {
  SELF_REVIEW_FORBIDDEN: '작성자와 다른 검토 계정이 필요합니다.', SELF_APPROVAL_FORBIDDEN: '작성자는 자신의 문서를 승인할 수 없습니다.',
  PROCUREMENT_LEDGER_VERSION_CHANGED: '기준 원장이 바뀌었습니다. 최신 원장을 조회하고 초안을 다시 검토해 주세요.',
  PROCUREMENT_DEMAND_ALREADY_REQUESTED: '이 품목의 진행 중인 구매 요청이 있습니다. 기존 문서를 확인해 주세요.',
  PROCUREMENT_QUOTE_EXPIRED: '견적 유효기간이 지났습니다. 새 조건을 검토해 주세요.',
  PROCUREMENT_QUANTITY_CHANGED: '부족량 또는 MOQ·포장 배수를 반영한 추천 수량과 다릅니다.',
  PROCUREMENT_SUPPLY_CONFIRMATION_REQUIRED: '모의 공급확인 수량을 먼저 별도 검토·승인·반영해 주세요.',
  PROCUREMENT_OVER_RECEIPT: '발주 잔여 수량을 초과했습니다.', PROCUREMENT_OVER_INSPECTION: '검사 대기 수량을 초과했습니다.',
  PROCUREMENT_REFERENCE_ALREADY_EXISTS: '같은 식별자가 이미 등록되어 있습니다. 기존 문서를 확인해 주세요.',
  PROCUREMENT_FREIGHT_ACK_REQUIRED: '운임 미확인을 확인해 주세요. 미확인 금액은 0으로 계산하지 않습니다.',
  FULFILLMENT_BOARD_ALREADY_RECORDED: '이미 기록된 보드 serial입니다.', FULFILLMENT_BOARD_STATE_INVALID: '이전 단계가 반영된 보드만 다음 단계로 기록할 수 있습니다.',
  FULFILLMENT_EXCEEDS_REMAINING_TARGET: '남은 양품 목표보다 많은 보드는 기록할 수 없습니다.', FULFILLMENT_LOT_NOT_AVAILABLE: 'LOT의 가용 수량이 부족합니다.',
  FULFILLMENT_BOM_APPLICATION_REQUIRED: '이 품목의 BOM 적용(대당 수량)이 반영돼 있어야 합니다.',
};

// Compact 'IM-xxxx 확보 진행' card from the same server documents (DEC-H53).
// Steps: 초안 → 승인 요청 (saving a draft requests review) → 승인 → 확정 (ledger posting).
const progressSteps = ['초안', '승인 요청', '승인', '확정'];
export function supplyProgress(documents, snapshot, identity) {
  const item = snapshot?.summary?.item_id;
  const live = row => !row.cancelled_by && row.status !== 'REJECTED';
  const pr = (documents || []).filter(row => row.kind === 'PR' && live(row) && row.content?.resolved?.item_id === item).at(-1);
  if (!pr) return null;
  const po = (documents || []).filter(row => row.kind === 'PO' && live(row) && (row.parent_id === pr.id || row.content?.pr_id === pr.id)).at(-1) || null;
  const focus = po || pr;
  const reached = { DRAFT: 2, REVIEWED: 2, APPROVED: 3, POSTED: 4 }[focus.status] ?? 0;
  const steps = progressSteps.map((label, index) => ({ label, state: index < reached ? 'done' : index === reached ? 'current' : 'pending' }));
  const status = focus.status === 'POSTED' ? (po ? '발주 확정' : '발주 작성 필요') : focus.status === 'APPROVED' ? '확정 대기' : '승인 진행';
  const resolved = pr.content?.resolved || {}, quote = resolved.quote || {};
  const unit = resolved.unit === 'EA' ? '개' : ` ${resolved.unit || ''}`;
  const short = id => String(id || '').slice(0, 8);
  const requirement = (snapshot?.material_requirements?.items || []).find(row => row.item_id === item);
  const needed = requirement ? Number(pr.content?.material_basis === 'planned' ? requirement.planned_shortage_qty : requirement.shortage_qty) : NaN;
  const shortfall = Number.isFinite(needed) && pr.status !== 'POSTED' && needed > pr.quantity ? needed - pr.quantity : 0;
  const mine = focus.created_by === identity?.actor_id, can = permission => (identity?.permitted_actions || []).includes(permission);
  // A draft written on an older ledger version must be revised before review (server rule).
  const basis = focus.content?.basis_ledger_version, current = snapshot?.meta?.ledger_version;
  const stale = focus.status !== 'POSTED' && Number.isInteger(basis) && Number.isInteger(current) && basis < current;
  let next = null;
  if (stale) next = mine ? { label: '초안 수정', action: 'revise', disabled: !can('document.update') } : { label: '작성자의 초안 수정 대기', disabled: true };
  else if (focus.status === 'DRAFT') next = mine ? { label: '승인 요청됨 · 다른 계정 검토 대기', disabled: true } : { label: '검토 기록', action: 'review', disabled: !can('document.review') };
  else if (focus.status === 'REVIEWED') next = mine ? { label: '승인 대기 · 다른 계정', disabled: true } : { label: '승인', action: 'approve', disabled: !can('document.approve') };
  else if (focus.status === 'APPROVED') next = { label: '확정 · 원장 반영', action: 'post', disabled: !can('document.post') };
  else if (!po && pr.remaining_qty > 0) next = { label: '내부 발주 초안', action: 'new-po', disabled: !can('document.create') };
  return {
    item, status, steps, focusId: focus.id, next, shortfall, stale: stale ? { basis, current } : null,
    rows: [
      ['구매 요청', `${short(pr.id)} · ${pr.quantity}${unit}`],
      ['내부 발주', po ? `${short(po.id)} · ${resolved.supplier_id || '공급사 미확인'} · ${po.quantity}${unit}` : '미작성'],
      ['납기 가정', quote.lead_business_days != null ? `견적 리드타임 ${quote.lead_business_days}영업일 · 필요일 근거 없음` : '리드타임·필요일 근거 없음'],
      ['작성 · 승인', `${focus.created_by || '미확인'} · ${focus.approved_by || '—'}`],
    ],
  };
}
function progressCard(progress, busy) {
  if (!progress) return '';
  const pill = (progress.shortfall > 0 ? `<span class="mk-pill bad">${esc(progress.shortfall)}개 미충족 · 재검토 필요</span>` : '')
    + (progress.stale ? `<span class="mk-pill warn">기준 원장 v${esc(progress.stale.basis)}→v${esc(progress.stale.current)} · 초안 재검토 필요</span>` : '');
  const next = progress.next ? `<button type="button" class="mk-btn primary" ${progress.next.action ? `data-purchase-action="${progress.next.action}" data-doc="${esc(progress.focusId)}"` : ''}${progress.next.disabled || busy ? ' disabled' : ''}>${esc(progress.next.label)}</button>` : '';
  return `<section class="mk-supply-progress" data-supply-progress><div class="mk-supply-head"><h4>${esc(progress.item)} 확보 진행</h4>${pill}<span class="mk-pill warn">${esc(progress.status)}</span></div>
    <div class="mk-supply-steps" role="list" aria-label="확보 단계">${progress.steps.map(step => `<span role="listitem" class="mk-step-${step.state}"${step.state === 'current' ? ' aria-current="step"' : ''}>${esc(step.label)}</span>`).join('<i aria-hidden="true">→</i>')}</div>
    <dl class="mk-supply-rows">${progress.rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join('')}</dl>
    ${next}<p class="mk-small-note">내부 발주는 공급사 확약이 아닙니다. 외부 송신은 없습니다.</p></section>`;
}

export function mountProcurementFlow(container, { snapshot, api, getIdentity = () => null, onPosted = () => {}, onRefresh = () => {} }) {
  const abort = new AbortController(), pending = new Map();
  let documents = [], choices = [], activeId = null, editor = null, busy = false, destroyed = false;
  let notice = '', failed = false, needsRefresh = false;
  const ledgerVersion = snapshot.meta?.ledger_version;
  const active = () => documents.find(row => row.id === activeId);
  const can = permission => (getIdentity()?.permitted_actions || []).includes(permission);
  const author = doc => doc?.created_by === getIdentity()?.actor_id;
  function message(text, error = false) { notice = text; failed = error; }
  function exact(doc) { return { expected_version: doc.version, payload_hash: doc.payload_hash }; }
  async function command(path, body) {
    const signature = JSON.stringify([path, body]);
    if (!pending.has(signature)) pending.set(signature, `purchase-ui-${crypto.randomUUID()}`);
    const result = await api(path, { ...body, idempotency_key: pending.get(signature) });
    pending.delete(signature);
    return result;
  }
  async function work(callback) {
    if (busy || destroyed) return;
    busy = true; render();
    try { await callback(); }
    catch (error) {
      if (!destroyed && error.name !== 'AbortError') message(errors[error.message] || (error.status === 403
        ? '현재 계정의 권한과 작성자·검토자 구분을 확인해 주세요.'
        : error.status === 409 ? '문서 상태나 기준이 변경되었습니다. 서버 문서를 다시 확인해 주세요.'
        : error.status === 422 ? '입력 수량·단위·조건과 필수 근거를 확인해 주세요.'
        : '결과를 확인하지 못했습니다. 같은 입력으로 재시도하면 동일 요청 키를 사용합니다.'), true);
    } finally { if (!destroyed) { busy = false; render(); } }
  }
  async function reload(preferred = activeId) {
    const response = await api('/api/mks2/procurement/documents');
    if (destroyed) return;
    documents = response.documents || [];
    activeId = documents.some(row => row.id === preferred) ? preferred : documents.at(-1)?.id || null;
    needsRefresh ||= documents.some(row => (row.ledger_version || 0) > ledgerVersion);
    if (!needsRefresh) {
      try {
        const result = await api('/api/mks2/procurement/options', { ledger_version: ledgerVersion });
        if (!destroyed) choices = result.quotes || [];
      } catch (error) {
        if (error.status !== 409) throw error;
        message('같은 버전의 원장·그래프 준비 후 견적 비교를 조회할 수 있습니다.');
      }
    }
  }
  function start(kind, values = {}, revise = null) {
    editor = { kind, values: { kind, note: '', ...values }, revise };
    message('입력 내용을 검토한 뒤 서버에 초안을 저장해 주세요. 승인과 반영은 별도입니다.'); render();
  }
  function quoteReference(index) {
    const source = snapshot.quotations?.[index];
    if (!source) return;
    const values = { quote_ref: `LOCAL-${crypto.randomUUID().slice(0, 8)}`, provenance: 'DEMO_ASSUMPTION' };
    for (const key of fields.QUOTE) if (source[key] != null) values[key] = String(source[key]);
    // No inferred expiry, freight or physical supplier commitment.
    values.valid_until = source.valid_until || '';
    values.freight = source.freight == null ? '' : String(source.freight);
    start('QUOTE', values);
  }
  function form() {
    if (!editor) return '';
    const { kind, values } = editor;
    if (kind === 'REJECT') return `<form class="mk-bom-form" data-purchase-form><h4>문서 반려</h4><label class="mk-bom-field">반려 사유<textarea name="reason" minlength="8" maxlength="2000" required>${esc(values.reason || '')}</textarea></label><button class="mk-btn" ${busy ? 'disabled' : ''}>반려 기록</button>${button('접기', 'close-editor', busy)}</form>`;
    const editable = fields[kind].filter(key => !['_id', 'provenance'].includes(key) && !['quote_id', 'pr_id', 'po_id', 'receipt_id', 'target_id', 'iqc_id', 'production_id', 'fg_receipt_id', 'shipment_id', 'material_basis', 'acknowledge_partial_bom', 'acknowledge_unknown_freight'].includes(key));
    return `<form class="mk-bom-form" data-purchase-form><h4>${esc(kinds[kind])} ${editor.revise ? '초안 수정' : '초안'}</h4>
      ${kind === 'SUPPLY_CONFIRMATION' ? '<p>실제 공급사 응답이 없는 내부 모의 확인입니다. 도착일을 확정하지 않습니다.</p>' : ''}
      <div class="mk-bom-fields">${editable.map(key => key === 'note'
        ? `<label class="mk-bom-field">${labels[key]}<textarea name="${key}" minlength="8" maxlength="2000" required>${esc(values[key] || '')}</textarea></label>`
        : `<label class="mk-bom-field">${esc(labels[key] || key)}<input name="${key}" type="${key === 'valid_until' ? 'date' : numberFields.has(key) ? 'number' : 'text'}" value="${esc(values[key] ?? '')}" ${numberFields.has(key) ? `min="${['passed_qty', 'failed_qty', 'lead_business_days'].includes(key) ? 0 : 1}" max="1000000000" step="1"` : ''} ${['freight', 'lead_business_days'].includes(key) ? '' : 'required'}></label>`).join('')}</div>
      ${kind === 'PR' ? `<p>선택 기준: ${values.material_basis === 'planned' ? '계획 수율 적용안' : '기본 소요안'}</p><label class="mk-bom-ack"><input type="checkbox" name="acknowledge_partial_bom" ${values.acknowledge_partial_bom ? 'checked' : ''}>미확인 BOM 행과 기술검수·전체 제조 가능성의 미확인을 확인했습니다.</label>` : ''}
      ${kind === 'PO' ? `<label class="mk-bom-ack"><input type="checkbox" name="acknowledge_unknown_freight" ${values.acknowledge_unknown_freight ? 'checked' : ''}>운임 미확인을 유지하는 내부 발주임을 확인합니다. 총액은 미확인으로 남습니다.</label>` : ''}
      ${kind === 'QUOTE' ? '<p>시연 가정 견적입니다. 기초 견적에 없던 조건은 직접 확인하고 입력해 주세요.</p>' : ''}
      ${kind === 'PRODUCTION' ? '<p>이 LOT의 부품을 소비해 장착한 양품 보드를 serial로 기록합니다. 계획이나 예정이 아니라 실제 생산 실적만 기록합니다.</p>' : ''}
      ${kind === 'ARRIVAL' ? '<p>고객 인수 근거가 있는 보드만 도착으로 기록합니다. 출하를 도착으로 간주하지 않습니다.</p>' : ''}
      <div class="mk-bom-actions"><button class="mk-btn" ${busy || needsRefresh ? 'disabled' : ''}>${editor.revise ? '수정 초안 저장' : '초안 저장 · 검토 요청'}</button>${button('접기', 'close-editor', busy)}</div></form>`;
  }
  function boardsFor(doc, [, link, state]) {
    return Object.entries(snapshot.fulfillment?.boards || {}).filter(([, value]) => value.status === state && value[link] === doc.id).map(([serial]) => serial).sort();
  }
  function detail(doc) {
    if (!doc) return '<p>등록된 구매 문서가 없습니다. 견적 조건부터 확인해 주세요.</p>';
    const value = doc.content || {}, calc = value.resolved?.calculation;
    const actions = [];
    if (doc.status === 'DRAFT' && !author(doc)) actions.push(button('검토 기록', 'review', !can('document.review') || busy || needsRefresh));
    if (doc.status === 'REVIEWED' && !author(doc)) actions.push(button('승인', 'approve', !can('document.approve') || busy || needsRefresh));
    if (doc.status !== 'POSTED' && !author(doc)) actions.push(button('반려', 'reject', !can('document.review') || busy || needsRefresh));
    if (doc.status === 'APPROVED') actions.push(button('원장 반영', 'post', !can('document.post') || busy || needsRefresh));
    if (doc.status !== 'POSTED' && author(doc)) actions.push(button('초안 수정', 'revise', !can('document.update') || busy || needsRefresh));
    if (doc.status === 'POSTED' && !doc.cancelled_by && !needsRefresh && can('document.create')) {
      if (doc.kind === 'PR' && doc.remaining_qty > 0) actions.push(button('내부 발주 초안', 'new-po', busy));
      if (doc.kind === 'PO') {
        if (doc.quantity > (doc.confirmed_qty || 0)) actions.push(button('공급확인 · 모의', 'new-confirmation', busy));
        if (doc.receivable_qty > 0) actions.push(button('입하 기록', 'new-receipt', busy));
      }
      if (doc.kind === 'RECEIPT' && doc.remaining_qty > 0) actions.push(button('수입검사 기록', 'new-iqc', busy));
      if (doc.kind === 'IQC' && (doc.content?.passed_qty || 0) > 0) actions.push(button('생산 실적 기록', 'new-production', busy));
      const step = nextStep[doc.kind];
      if (step && boardsFor(doc, step).length) actions.push(button(step[3], 'new-next', busy));
      if (['PR', 'PO'].includes(doc.kind) && doc.remaining_qty > 0) actions.push(button('잔여 취소 요청', 'new-cancel', busy));
    }
    const amount = value.kind === 'SUPPLY_CONFIRMATION' ? value.confirmed_qty : value.kind === 'CANCEL' ? value.resolved?.cancelled_remaining_qty : doc.quantity;
    const visible = fields[doc.kind].filter(key => !['note', 'provenance', 'acknowledge_partial_bom', 'acknowledge_unknown_freight'].includes(key));
    const extraLabels = { quote_id: '선택 견적', pr_id: '구매 요청', po_id: '발주 문서', receipt_id: '입하 문서', target_id: '취소 대상', material_basis: '계산 기준' };
    return `<section class="mk-quote-card"><div class="mk-quote-heading"><b>${esc(kinds[doc.kind])}</b><span class="mk-pill">${esc(statuses[doc.status])}${doc.cancelled_by ? ' · 잔여 취소' : ''}</span><span>v${esc(doc.version)}</span></div>
      <p>${amount ? `수량 ${esc(amount)} ${esc(value.resolved?.unit || '')}` : ''}${doc.remaining_qty != null ? ` · 남은 수량 ${esc(doc.remaining_qty)}` : ''}${doc.kind === 'PO' ? ` · 모의 확인 ${esc(doc.confirmed_qty || 0)} · 입하 가능 ${esc(doc.receivable_qty || 0)}` : ''}</p>
      ${calc && ['QUOTE', 'PR', 'PO'].includes(doc.kind) ? `<p>소계 ${esc(calc.subtotal)} ${esc(calc.currency)} · 운임 ${esc(calc.freight ?? '미확인')} · 총액 ${esc(calc.total ?? '미확인')}</p>` : ''}
      ${value.board_serials ? `<p>보드 ${esc(value.board_serials.join(', '))}${value.resolved?.lot_code ? ` · LOT ${esc(value.resolved.lot_code)} 소비 ${esc(value.resolved.consumed_qty)}` : ''}</p>` : ''}
      ${doc.kind === 'IQC' ? `<p>이번 합격 ${esc(value.passed_qty)} · 불합격 ${esc(value.failed_qty)} · 검사 전 대기 ${esc(value.resolved?.remaining_before ?? '미확인')}</p>` : ''}
      ${doc.rejection_reason ? `<p role="status">반려 사유: ${esc(doc.rejection_reason)}</p>` : ''}
      <div class="mk-bom-actions">${actions.join('')}</div>
      <details ${doc.status !== 'POSTED' ? 'open' : ''}><summary>문서 내용·검토 근거</summary><dl class="mk-key-values">${visible.map(key => `<dt>${esc(labels[key] || extraLabels[key] || key)}</dt><dd>${esc(value[key] ?? '미확인')}</dd>`).join('')}<dt>검토 근거</dt><dd>${esc(value.note)}</dd><dt>기준 원장</dt><dd>v${esc(value.basis_ledger_version)}</dd><dt>작성자</dt><dd>${esc(doc.created_by)}</dd><dt>검토자 / 승인자</dt><dd>${esc(doc.reviewed_by || '대기')} / ${esc(doc.approved_by || '대기')}</dd><dt>내용 hash</dt><dd>${esc(doc.payload_hash)}</dd>${value.resolved?.parent ? `<dt>이전 문서</dt><dd>${esc(kinds[value.resolved.parent.kind])} v${esc(value.resolved.parent.version)}</dd>` : ''}</dl></details></section>`;
  }
  function render() {
    if (destroyed) return;
    container.innerHTML = `${progressCard(supplyProgress(documents, snapshot, getIdentity()), busy)}<div class="mk-quote-preview"><p class="mk-preview-notice">내부 시연 기록 · 실제 공급사 송신·확약 없음. 합격분만 가용재고에 반영합니다.</p>
      <p role="status" aria-live="polite" class="${failed ? 'mk-bom-warning' : ''}">${esc(notice)}</p>
      ${needsRefresh ? `<p>원장에 새 사건이 반영되었습니다. 최신 원장 조회 후 다음 업무를 진행해 주세요.</p>${button('최신 원장 조회', 'refresh', busy)}` : ''}
      <div class="mk-bom-actions">${button('서버 문서 조회', 'reload', busy)}${documents.length ? `<label>구매 문서 <select data-purchase-document ${busy ? 'disabled' : ''}>${documents.map(doc => `<option value="${esc(doc.id)}" ${doc.id === activeId ? 'selected' : ''}>${esc(kinds[doc.kind])} · ${esc(doc.content.quote_ref || doc.content.receipt_ref || doc.content.inspection_ref || doc.id.slice(0, 8))} · ${esc(statuses[doc.status])}</option>`).join('')}</select></label>` : ''}</div>
      ${detail(active())}${form()}
      <details ${documents.length ? '' : 'open'}><summary>견적 비교 · 구매 요청</summary><div class="mk-quote-list">${choices.map((choice, index) => `<article class="mk-quote-card"><b>${esc(choice.quote_ref)} · ${esc(choice.item_id)}</b><p>${choice.material_basis === 'planned' ? '계획 수율 적용' : '기본 소요'} · 부족 ${esc(choice.calculation?.shortage_qty ?? '미확인')} · 구매 권고 ${esc(choice.calculation?.recommended_qty ?? '미확인')} ${esc(choice.unit)}</p><p>${esc(choice.supplier_id || '공급사 미확인')} · MOQ ${esc(choice.moq ?? '미확인')} · 배수 ${esc(choice.order_multiple ?? '미확인')} · 공급한도 ${esc(choice.supply_limit_qty ?? '미확인')} · 리드타임 ${esc(choice.lead_business_days ?? '미확인')}영업일(가정)</p><p>${choice.calculation ? `<span class="mk-pill ${({ MET: 'ok', NOT_MET: 'bad', UNKNOWN: 'warn' })[choice.calculation.quantity_condition] || 'mute'}">${esc(({ MET: '수량 충족', NOT_MET: '수량 미충족', UNKNOWN: '판단 보류' })[choice.calculation.quantity_condition] || '미확인')}</span> 여유 ${esc(choice.calculation.margin_qty ?? '미확인')}${choice.calculation.quantity_condition_reason === 'ORDER_EXCEEDS_SUPPLY_LIMIT' ? ' · 주문량이 공급한도를 넘음' : choice.calculation.quantity_condition_reason === 'SUPPLY_LIMIT_UNKNOWN' ? ' · 공급한도 미확인' : ''} · ` : ''}운임 ${esc(choice.calculation?.freight ?? '미확인')} · 총액 ${esc(choice.calculation?.total ?? '미확인')} ${esc(choice.calculation?.currency || '')}${choice.expired ? ' · 견적 만료' : ''}</p><p class="mk-small-note">수량 조건은 납기·비용·공급 확약과 별개입니다. 공급사 확약 없음.</p>${button('구매 요청 초안', 'new-pr', busy || needsRefresh || choice.expired || !choice.calculation?.recommended_qty || choice.calculation?.quantity_condition === 'NOT_MET' || !can('document.create'), `data-choice="${index}"`)}</article>`).join('') || '<p>현재 원장에 반영된 내부 견적이 없습니다.</p>'}
      ${(snapshot.quotations || []).map((quote, index) => `<article class="mk-quote-card"><b>${esc(quote.quote_id)} · ${esc(quote.item_id)}</b><p>기초 견적 · 단가 ${esc(quote.unit_price)} ${esc(quote.currency)} · 운임 ${esc(quote.freight ?? '미확인')} · 유효기간 ${esc(quote.valid_until ?? '미확인')}</p>${button('견적 조건 검토', 'quote-reference', busy || needsRefresh || !can('document.create'), `data-reference="${index}"`)}</article>`).join('')}</div></details></div>`;
  }
  container.addEventListener('click', event => {
    const target = event.target.closest('[data-purchase-action]');
    if (!target || target.disabled || busy) return;
    if (target.dataset.doc && documents.some(row => row.id === target.dataset.doc)) activeId = target.dataset.doc;
    const action = target.dataset.purchaseAction, doc = active();
    if (action === 'refresh') { onRefresh(); return; }
    if (action === 'reload') { work(() => reload()); return; }
    if (action === 'close-editor') { editor = null; render(); return; }
    if (action === 'quote-reference') { quoteReference(Number(target.dataset.reference)); return; }
    if (action === 'new-pr') {
      const choice = choices[Number(target.dataset.choice)];
      start('PR', { quote_id: choice.quote_id, quantity: choice.calculation.recommended_qty, material_basis: choice.material_basis, acknowledge_partial_bom: false }); return;
    }
    if (!doc) return;
    if (action === 'revise') { start(doc.kind, Object.fromEntries(fields[doc.kind].map(key => [key, doc.content[key]])), doc); return; }
    if (action === 'reject') { start('REJECT', { reason: '' }); return; }
    if (action === 'new-po') { start('PO', { pr_id: doc.id, quantity: doc.remaining_qty, acknowledge_unknown_freight: false }); return; }
    if (action === 'new-confirmation') { start('SUPPLY_CONFIRMATION', { po_id: doc.id, confirmed_qty: doc.quantity - (doc.confirmed_qty || 0), confirmation_ref: '' }); return; }
    if (action === 'new-receipt') { start('RECEIPT', { po_id: doc.id, quantity: doc.receivable_qty, receipt_ref: '', lot_code: '' }); return; }
    if (action === 'new-iqc') { start('IQC', { receipt_id: doc.id, passed_qty: 0, failed_qty: 0, inspection_ref: '' }); return; }
    if (action === 'new-cancel') { start('CANCEL', { target_id: doc.id }); return; }
    if (action === 'new-production') { start('PRODUCTION', { iqc_id: doc.id, production_ref: '', board_serials: '' }); return; }
    if (action === 'new-next') {
      const [kind, link] = nextStep[doc.kind], refs = { FG_RECEIPT: 'receipt_ref', SHIPMENT: 'shipment_ref', ARRIVAL: 'arrival_ref' };
      start(kind, { [{ FG_RECEIPT: 'production_id', SHIPMENT: 'fg_receipt_id', ARRIVAL: 'shipment_id' }[kind]]: doc.id, [refs[kind]]: '', board_serials: boardsFor(doc, nextStep[doc.kind]).join(', ') });
      return;
    }
    if (['review', 'approve', 'post'].includes(action)) work(async () => {
      const result = await command(`/api/mks2/procurement/documents/${encodeURIComponent(doc.id)}/${action}`, exact(doc));
      if (destroyed) return;
      editor = null;
      if (action === 'post') { needsRefresh = true; onPosted(result); }
      await reload(result.id);
      message({ review: '이 버전의 문서를 검토했습니다. 승인은 별도입니다.', approve: '이 버전의 문서를 승인했습니다. 원장 반영은 별도입니다.', post: '원장에 반영했습니다. 최신 원장에서 수량을 확인해 주세요.' }[action]);
    });
  }, { signal: abort.signal });
  container.addEventListener('change', event => {
    if (event.target.matches('[data-purchase-document]')) { activeId = event.target.value; editor = null; render(); }
  }, { signal: abort.signal });
  container.addEventListener('input', event => {
    if (editor && event.target.name) editor.values[event.target.name] = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
  }, { signal: abort.signal });
  container.addEventListener('submit', event => {
    if (!event.target.matches('[data-purchase-form]')) return;
    event.preventDefault();
    if (!editor || busy || needsRefresh) return;
    const current = editor, doc = active(), values = { ...current.values, kind: current.kind };
    for (const [key, value] of new FormData(event.target)) values[key] = value;
    for (const key of ['acknowledge_partial_bom', 'acknowledge_unknown_freight']) if (fields[current.kind]?.includes(key)) values[key] = event.target.elements[key]?.checked || false;
    for (const key of numberFields) if (fields[current.kind]?.includes(key)) values[key] = values[key] === '' || values[key] == null ? null : Number(values[key]);
    if (fields[current.kind]?.includes('board_serials')) values.board_serials = String(values.board_serials || '').split(/[,\s]+/).map(value => value.trim()).filter(Boolean);
    if (current.kind === 'QUOTE') { values.freight = values.freight === '' || values.freight == null ? null : values.freight; values.provenance = 'DEMO_ASSUMPTION'; }
    work(async () => {
      const path = current.kind === 'REJECT' ? `/api/mks2/procurement/documents/${encodeURIComponent(doc.id)}/reject`
        : current.revise ? `/api/mks2/procurement/documents/${encodeURIComponent(current.revise.id)}/revise` : '/api/mks2/procurement/documents';
      const body = current.kind === 'REJECT' ? { ...exact(doc), reason: values.reason }
        : { ...(current.revise ? exact(current.revise) : {}), expected_ledger_version: ledgerVersion, content: values };
      const result = await command(path, body);
      if (destroyed) return;
      editor = null; await reload(result.id);
      message(current.kind === 'REJECT' ? '반려 사유를 기록했습니다. 이전 승인은 해제되었습니다.' : '검토할 초안을 저장했습니다. 작성자와 다른 계정에서 검토·승인을 진행해 주세요.');
    });
  }, { signal: abort.signal });
  render(); const loaded = work(() => reload());
  // Opens the same PR draft form as the comparison card's button. Nothing is saved until the user submits.
  async function startPurchaseRequest(quoteId, basis = 'planned') {
    await loaded;
    if (destroyed || busy || needsRefresh || !can('document.create')) return false;
    const choice = choices.find(row => row.quote_id === quoteId && row.material_basis === basis);
    if (!choice || choice.expired || !choice.calculation?.recommended_qty || choice.calculation.quantity_condition === 'NOT_MET') return false;
    start('PR', { quote_id: choice.quote_id, quantity: choice.calculation.recommended_qty, material_basis: choice.material_basis, acknowledge_partial_bom: false });
    container.querySelector?.('[data-purchase-form]')?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    return true;
  }
  return { startPurchaseRequest, refresh() { return work(() => reload()); }, destroy() { destroyed = true; abort.abort(); pending.clear(); container.replaceChildren(); } };
}
