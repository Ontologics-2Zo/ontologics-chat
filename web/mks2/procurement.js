// Existing supply-card flow. Commands stay explicit and server-authorized;
// this module never sends supplier messages or changes browser-only inventory.
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const kinds = { QUOTE: '견적 조건', PR: '구매 요청', PO: '내부 발주', SUPPLY_CONFIRMATION: '공급확인 · 모의', RECEIPT: '입하', IQC: '수입검사', CANCEL: '잔여 취소',
  PRODUCTION: '생산 실적', FG_RECEIPT: '완성품 입고', SHIPMENT: '출하', ARRIVAL: '고객 도착' , SCENARIO: '일정 가정 시나리오' , COHORT: '기존 보드 이행' };
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
  PRODUCTION: ['iqc_id', 'lot_code', 'production_ref', 'board_serials', 'note'],
  FG_RECEIPT: ['production_id', 'receipt_ref', 'board_serials', 'note'],
  SHIPMENT: ['fg_receipt_id', 'shipment_ref', 'board_serials', 'note'],
  ARRIVAL: ['shipment_id', 'arrival_ref', 'board_serials', 'note'],
  // T12: what-if schedule assumptions for one delivery round.
  COHORT: ['cohort_ref', 'round_id', 'stage', 'quantity', 'note'],
  SCENARIO: ['scenario_ref', 'round_id', 'inspection_business_days', 'daily_capacity', 'ship_prep_business_days', 'transit_days', 'provenance', 'note'],
};
const nextStep = { PRODUCTION: ['FG_RECEIPT', 'production_id', 'PRODUCED', '완성품 입고 기록'],
  FG_RECEIPT: ['SHIPMENT', 'fg_receipt_id', 'RECEIVED', '출하 기록'], SHIPMENT: ['ARRIVAL', 'shipment_id', 'SHIPPED', '고객 도착 기록'] };
const labels = { production_ref: '생산 실적 식별자', shipment_ref: '출하 식별자', arrival_ref: '도착 식별자', board_serials: '보드 serial · 쉼표로 구분', quote_ref: '내부 견적 식별자', supplier_id: '공급사', item_id: '품목', unit: '단위', unit_price: '단가', currency: '통화', moq: '최소 주문량', order_multiple: '포장 배수', freight: '운임 · 미확인은 빈칸', valid_until: '유효기간', lead_business_days: '참고 리드타임 · 영업일', quantity: '수량', confirmation_ref: '모의 공급확인 식별자', confirmed_qty: '모의 확인 수량', receipt_ref: '입하 식별자', lot_code: '신규 LOT 식별자', inspection_ref: '검사 식별자', passed_qty: '합격 수량', failed_qty: '불합격 수량', note: '검토 근거 · 조건', reason: '반려 사유',
  inspection_business_days: '수입검사 기간 · 영업일', daily_capacity: '생산능력 · 하루 대수', ship_prep_business_days: '출하 준비 · 영업일', transit_days: '운송 기간 · 일' };
const numberFields = new Set(['moq', 'order_multiple', 'quantity', 'confirmed_qty', 'passed_qty', 'failed_qty', 'lead_business_days',
  'inspection_business_days', 'daily_capacity', 'ship_prep_business_days', 'transit_days']);
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
// It follows one purchase like the shared screen: 초안 (unsaved request form) → 승인 요청
// (saved draft, waiting for another account) → 승인 → 확정 (ledger posting), then
// receipt and inspection. Buttons only run the existing explicit commands.
const unitWord = unit => unit === 'EA' || !unit ? '개' : ` ${unit}`;
// Short readable document label from the real ID (for example PR-16BF), like the shared screen.
const docPrefix = { COHORT: 'CH', SCENARIO: 'SCN', PR: 'PR', PO: 'PO', SUPPLY_CONFIRMATION: 'SC', RECEIPT: 'RC', IQC: 'IQC', CANCEL: 'CX', QUOTE: 'QT',
  PRODUCTION: 'PD', FG_RECEIPT: 'FG', SHIPMENT: 'SH', ARRIVAL: 'AR' };
export const documentLabel = (kind, id) => `${docPrefix[kind] || kind}-${String(id || '').replace(/[^0-9a-z]/gi, '').slice(0, 4).toUpperCase()}`;
const progressSteps = ['초안', '승인 요청', '승인', '확정'];
const reachedStep = { DRAFT: 1, REVIEWED: 2, APPROVED: 3, POSTED: 4 };
export function supplyProgress(documents, snapshot, identity, editor = null) {
  const item = snapshot?.summary?.item_id;
  const live = row => !row.cancelled_by && row.status !== 'REJECTED';
  const rows = (documents || []).filter(live);
  const pr = rows.filter(row => row.kind === 'PR' && row.content?.resolved?.item_id === item
    && !(row.status === 'DRAFT' && row.rejection_reason)).at(-1) || null;
  const can = permission => (identity?.permitted_actions || []).includes(permission);
  const docLabel = documentLabel;
  const short = id => String(id || '').slice(0, 8);
  const quoteOf = id => (documents || []).find(row => row.id === id);
  const stepsAt = reached => progressSteps.map((label, index) => ({ label, state: index < reached ? 'done' : index === reached ? 'current' : 'pending' }));
  if (!pr) {
    if (editor?.kind === 'PR' && !editor.revise) {
      const quote = quoteOf(editor.values.quote_id)?.content || {};
      return { item, status: '승인 진행', tone: 'warn', steps: stepsAt(0), draft: true,
        next: [],
        notes: ['승인 요청을 누르면 견적 권장 수량으로 초안이 서버에 저장되고 다른 계정의 승인을 기다립니다.'],
        rows: [['구매 요청', `작성 중 · ${editor.values.quantity ?? '미확인'}${unitWord(quote.unit)}`],
               ['내부 발주', `미작성 · 견적 ${quote.resolved?.supplier_id || quote.supplier_id || '미확인'}`],
               ['납기 가정', quote.lead_business_days != null ? `견적 리드타임 ${quote.lead_business_days}영업일 · 필요일 근거 없음` : '리드타임·필요일 근거 없음'],
               ['작성 · 승인', `${identity?.actor_id || '미확인'} · —`]] };
    }
    return { item, empty: true, status: '발주 없음', tone: 'mute', text: `아직 ${item || '대상 품목'} 구매 요청·발주가 없습니다. 오른쪽 에이전트에게 회차 가능 여부를 물어보세요.` };
  }
  // Every live document descending from this request, in server order.
  const chain = [pr];
  for (let grew = true; grew;) {
    grew = false;
    for (const row of rows) if (!chain.includes(row) && chain.some(parent => parent.id === row.parent_id)) { chain.push(row); grew = true; }
  }
  const latest = kind => chain.filter(row => row.kind === kind && !(row.status === 'DRAFT' && row.rejection_reason)).at(-1) || null;
  const po = latest('PO'), receipt = latest('RECEIPT'), iqc = latest('IQC');
  const resolved = pr.content?.resolved || {}, quote = resolved.quote || {}, unit = unitWord(resolved.unit);
  // The document still moving through approval is the one the chips describe.
  // A returned draft (반려) waits for its author; it is set aside so the flow can continue.
  const returned = chain.filter(row => row.status === 'DRAFT' && row.rejection_reason);
  const focus = chain.filter(row => row.status !== 'POSTED' && !returned.includes(row)).at(-1) || null;
  const requirement = (snapshot?.material_requirements?.items || []).find(row => row.item_id === item);
  const needed = requirement ? Number(pr.content?.material_basis === 'planned' ? requirement.planned_shortage_qty : requirement.shortage_qty) : NaN;
  const shortfall = Number.isFinite(needed) && pr.status !== 'POSTED' && needed > pr.quantity ? needed - pr.quantity : 0;
  const basis = focus?.content?.basis_ledger_version, current = snapshot?.meta?.ledger_version;
  const stale = focus && Number.isInteger(basis) && Number.isInteger(current) && basis < current ? { basis, current } : null;
  const boards = Object.values(snapshot?.fulfillment?.boards || {});
  const pendingBoards = (doc, [, link, state]) => boards.filter(board => board.status === state && board[link] === doc.id).length;
  let status, tone = 'warn', next = [], notes = [];
  const mine = focus && focus.created_by === identity?.actor_id;
  if (focus) {
    status = focus.status === 'APPROVED' ? '확정 대기' : '승인 진행';
    if (stale) {
      next = mine ? [{ label: '초안 수정', action: 'revise', primary: true, disabled: !can('document.update') }] : [{ label: '작성자의 초안 수정 대기', disabled: true }];
    } else if (focus.status === 'APPROVED') {
      next = [{ label: '확정 (원장 반영)', action: 'post', primary: true, disabled: !can('document.post') }];
      notes.push('승인됐지만 아직 원장에 반영되지 않았습니다.');
    } else if (mine) {
      next = [{ label: '승인', disabled: true }, { label: '반려', disabled: true }];
      // The approver role wrote it: no other account can approve this draft.
      notes.push(can('document.approve')
        ? '승인 권한이 있는 계정으로 작성한 요청이라 승인할 다른 계정이 없습니다. 상단에서 작성자 계정으로 바꿔 반려한 뒤, 작성자 계정으로 다시 요청하세요.'
        : '작성자 본인은 승인할 수 없습니다. 상단에서 역할을 검토자로 바꾸세요.');
    } else if (!can('document.approve')) {
      next = [{ label: '승인', disabled: true }, { label: '반려', action: 'reject', disabled: !can('document.review') }];
      notes.push('이 계정에는 승인 권한이 없습니다. 승인은 요청을 작성하지 않은 검토자 계정이 합니다.');
    } else {
      next = [{ label: '승인', action: focus.status === 'DRAFT' ? 'review-approve' : 'approve', primary: true, disabled: !can('document.approve') || (focus.status === 'DRAFT' && !can('document.review')) },
              { label: '반려', action: 'reject', disabled: !can('document.review') }];
    }
  } else {
    // In the demo accounts only the reviewer can approve. If it writes the next document,
    // no other account can approve it, so creation stays with the author account.
    const approver = can('document.approve');
    const create = !can('document.create') || approver;
    const stages = [];
    if (!po) { status = '발주 작성 필요'; stages.push({ label: '내부 발주 초안', action: 'new-po', target: pr.id, disabled: create || !(pr.remaining_qty > 0) }); }
    else if ((po.confirmed_qty || 0) < po.quantity && !receipt) { status = '입하 예정'; tone = 'info'; stages.push({ label: '공급확인 · 모의', action: 'new-confirmation', target: po.id, disabled: create }); }
    else if (po.receivable_qty > 0 && !receipt) { status = '입하 예정'; tone = 'info'; stages.push({ label: `입하 기록 · ${po.receivable_qty}${unit}`, action: 'new-receipt', target: po.id, disabled: create }); }
    else if (receipt && !iqc) { status = '검사 대기'; stages.push({ label: `수입검사 합격 · ${receipt.remaining_qty ?? receipt.quantity}${unit}`, action: 'new-iqc', target: receipt.id, disabled: create }); notes.push('입하했지만 검사 전이라 가용 재고 아님'); }
    else if (iqc) {
      const fulfilled = chain.filter(row => ['PRODUCTION', 'FG_RECEIPT', 'SHIPMENT', 'ARRIVAL'].includes(row.kind));
      const last = fulfilled.at(-1);
      status = { PRODUCTION: '생산 진행', FG_RECEIPT: '출하 대기', SHIPMENT: '운송 중', ARRIVAL: '고객 도착' }[last?.kind] || '가용';
      tone = status === '가용' || status === '고객 도착' ? 'ok' : 'info';
      if (!last) notes.push('가용 재고 반영 완료');
      // Boards this inspected LOT can still build: the remaining good-board target,
      // capped by the LOT's available stock (the server checks both again).
      const target = Number(snapshot?.summary?.production_remaining_qty) || 0;
      if ((iqc.passed_qty || iqc.content?.passed_qty || 0) > 0 && target > 0) {
        const lotCode = iqc.content?.resolved?.lot_code || receipt.lot_code;
        const perBoard = Number(requirement?.quantity_per_unit || requirement?.per_unit_qty || 1) || 1;
        const lotRow = (snapshot?.inventory || []).find(row => row.lot_id === lotCode && row.item_id === item);
        const boards = Math.min(target, lotRow ? Math.floor(Number(lotRow.available_qty) / perBoard) : 0);
        if (boards > 0) stages.push({ label: `생산 실적 기록 · ${boards}대`, action: 'new-production', target: iqc.id, quantity: boards, lot: lotCode, disabled: create });
        else notes.push(`${lotCode || '입하 LOT'} 가용 수량이 없어 생산 실적을 기록할 수 없습니다.`);
      }
      for (const doc of fulfilled) {
        const step = nextStep[doc.kind];
        if (step && pendingBoards(doc, step)) stages.push({ label: `${step[3]} · ${pendingBoards(doc, step)}대`, action: 'new-next', target: doc.id, disabled: create });
      }
    }
    next = stages.map((stage, index) => ({ ...stage, primary: index === 0 }));
    if (approver && stages.length) notes.unshift('다음 문서는 작성자 계정이 만듭니다. 승인 권한이 있는 계정이 작성하면 승인할 다른 계정이 없습니다. 상단에서 작성자 계정으로 바꿔 주세요.');
  }
  const kindLabel = { PR: '구매 요청', PO: '내부 발주', SUPPLY_CONFIRMATION: '공급확인 · 모의', RECEIPT: '입하', IQC: '수입검사',
    PRODUCTION: '생산 실적', FG_RECEIPT: '완성품 입고', SHIPMENT: '출하', ARRIVAL: '고객 도착' };
  const who = focus || po || pr;
  const progressRows = [
    ['구매 요청', `${docLabel('PR', pr.id)} · ${pr.quantity}${unit}`],
    ['내부 발주', po ? `${docLabel('PO', po.id)} · ${resolved.supplier_id || '공급사 미확인'} · ${po.quantity}${unit}` : `미작성 · 견적 ${resolved.supplier_id || '미확인'}`],
    ['납기 가정', quote.lead_business_days != null ? `견적 리드타임 ${quote.lead_business_days}영업일 · 필요일 근거 없음` : '리드타임·필요일 근거 없음'],
    ['작성 · 승인', `${who.created_by || '미확인'} · ${who.approved_by || '—'}`],
  ];
  if (receipt) progressRows.splice(2, 0, ['입하 · 검사', `${receipt.lot_code || short(receipt.id)} · 입하 ${receipt.quantity}${unit}${iqc ? ` · 합격 ${iqc.passed_qty ?? iqc.content?.passed_qty ?? 0}${unit}` : ' · 검사 전'}`]);
  if (boards.length) {
    const count = state => boards.filter(board => board.status === state).length;
    progressRows.splice(3, 0, ['보드 이행', `생산 ${boards.length} · 출하 ${count('SHIPPED') + count('ARRIVED')} · 도착 ${count('ARRIVED')}대`]);
  }
  return {
    item, status, tone, steps: stepsAt(focus ? reachedStep[focus.status] ?? 0 : 4), focusId: focus?.id || null,
    focusLabel: focus && focus !== pr ? kindLabel[focus.kind] : null,
    focusSummary: focus ? `${kindLabel[focus.kind] || focus.kind} ${docLabel(focus.kind, focus.id)}${focus.quantity != null ? ` · ${focus.quantity}${['PRODUCTION', 'FG_RECEIPT', 'SHIPMENT', 'ARRIVAL'].includes(focus.kind) ? '대' : unit}` : ''}` : null, next, shortfall, needed: Number.isFinite(needed) ? needed : null, stale, rows: progressRows,
    notes: returned.length ? [...notes, `반려된 초안 ${returned.length}건은 작성자 수정 전까지 진행에서 제외했습니다.`] : notes,
  };
}
const toneClass = { ok: 'ok', warn: 'warn', mute: 'mute', info: 'info' };
// One "진행 현황" card: a row per workflow with a one-line summary and only the
// buttons this account can press. Steps, detail rows and fixed notes fold away.
function laneButtons(entries, busy, focusId = null, fulfill = false) {
  let first = true;
  return entries.map((entry, index) => ({ entry, index })).filter(({ entry }) => entry.action && !entry.disabled).map(({ entry, index }) => {
    const primary = first; first = false;
    const doc = entry.target || focusId;
    return `<button type="button" class="mk-btn${primary ? ' primary' : ''}" data-purchase-action="${entry.action}"${fulfill ? ` data-fulfill-index="${index}"` : ''}${doc ? ` data-doc="${esc(doc)}"` : ''}${busy ? ' disabled' : ''}>${esc(entry.label)}</button>`;
  }).join('');
}
// Who the row is waiting for when this account has nothing to press.
export function laneWaiting(entries) {
  if (!entries?.length || entries.some(entry => entry.action && !entry.disabled)) return null;
  return entries.some(entry => entry.label === '승인') ? 'review' : 'author';
}
const WAITING_TEXT = { review: '검토자 승인 대기', author: '작성자 차례' };
function lane({ key, title, status, tone, summary, entries, busy, focusId, fulfill, warnings = [], steps = '', rows = [], notes = [], fixed = '', extra = '' }) {
  const waiting = laneWaiting(entries);
  const detail = steps || rows.length || notes.length || fixed;
  return `<section class="mk-flow-lane" ${key}>
    <div class="mk-flow-head"><strong>${esc(title)}</strong><span class="mk-pill ${toneClass[tone] || 'mute'}">${esc(status)}</span></div>
    ${summary ? `<p class="mk-flow-summary">${esc(summary)}</p>` : ''}
    ${warnings.map(text => `<div class="mk-supply-warn">${text}</div>`).join('')}
    <div class="mk-supply-actions">${laneButtons(entries, busy, focusId, fulfill)}${waiting ? `<span class="mk-flow-wait">${WAITING_TEXT[waiting]}</span>` : ''}</div>
    ${extra}
    ${detail ? `<details class="mk-flow-detail"><summary>상세</summary>${steps}${rows.length ? `<dl class="mk-supply-rows">${rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join('')}</dl>` : ''}${[...notes, fixed].filter(Boolean).map(note => `<p class="mk-small-note">${esc(note)}</p>`).join('')}</details>` : ''}
  </section>`;
}
function supplyLane(progress, busy, extra = '') {
  if (!progress) return '';
  const title = `${progress.item || '대상 품목'} 확보`;
  if (progress.empty) return lane({ key: 'data-supply-progress', title, status: progress.status, tone: progress.tone, summary: progress.text, entries: [], busy, extra });
  const pick = label => progress.rows.find(([name]) => name === label)?.[1];
  const order = pick('내부 발주');
  const summary = progress.focusSummary || pick('입하 · 검사') || (order && !order.startsWith('미작성') ? order : pick('구매 요청')) || '';
  const warnings = [];
  if (progress.shortfall > 0) warnings.push(`현재 부족 ${esc(progress.needed)}개 기준으로 이 안은 <b>${esc(progress.shortfall)}개 미충족</b>입니다 · 재검토 필요 (자동 수정 없음)`);
  if (progress.stale) warnings.push(`기준 원장 v${esc(progress.stale.basis)}→v${esc(progress.stale.current)} · 초안 재검토 필요`);
  const steps = `<div class="mk-supply-steps" role="list" aria-label="확보 단계${progress.focusLabel ? ` · ${esc(progress.focusLabel)}` : ''}">${progress.steps.map(step => `<span role="listitem" class="mk-step-${step.state}"${step.state === 'current' ? ' aria-current="step"' : ''}>${esc(step.label)}</span>`).join('<i aria-hidden="true">→</i>')}</div>`;
  return lane({ key: 'data-supply-progress', title, status: progress.status, tone: progress.tone, summary, entries: progress.next, busy, focusId: progress.focusId,
    warnings, steps, rows: progress.rows, notes: progress.notes, fixed: '내부 발주는 공급사 확약이 아닙니다. 외부 송신은 없습니다.', extra });
}

// T12 what-if schedule card: the latest scenario document and its next step,
// with the same author/approver separation as every other document.
const SCENARIO_LABELS = [['inspection_business_days', '수입검사', '영업일'], ['daily_capacity', '생산능력', '대/영업일'],
  ['ship_prep_business_days', '출하 준비', '영업일'], ['transit_days', '운송', '일']];
export function scenarioState(documents, identity) {
  const can = permission => (identity?.permitted_actions || []).includes(permission);
  const rows = (documents || []).filter(row => row.kind === 'SCENARIO' && !row.cancelled_by && !(row.status === 'DRAFT' && row.rejection_reason));
  const doc = rows.at(-1) || null, approver = can('document.approve'), mine = doc && doc.created_by === identity?.actor_id;
  const values = doc ? SCENARIO_LABELS.map(([key, label, unit]) => [label, `${doc.content?.[key] ?? '미확인'}${unit}`]) : [];
  const next = [], notes = [];
  if (!doc || doc.status === 'POSTED') {
    next.push({ label: doc ? '새 가정 작성' : '일정 가정 작성', action: 'new-scenario', disabled: approver || !can('document.create') });
    if (approver) notes.push('일정 가정은 작성자 계정이 작성하고 이 계정이 승인합니다.');
  } else if (doc.status === 'APPROVED') {
    next.push({ label: '확정 (원장 반영)', action: 'post', target: doc.id, disabled: !can('document.post') });
  } else {
    next.push({ label: '승인', action: 'review-approve', target: doc.id, disabled: mine || !approver },
              { label: '반려', action: 'reject', target: doc.id, disabled: mine || !can('document.review') });
    if (mine) notes.push('작성자 본인은 승인할 수 없습니다. 상단에서 역할을 검토자로 바꾸세요.');
  }
  const status = !doc ? '가정 없음' : doc.status === 'POSTED' ? `반영됨 · 원장 v${doc.ledger_version ?? '?'}` : doc.status === 'APPROVED' ? '확정 대기' : '승인 진행';
  const tone = !doc ? 'mute' : doc.status === 'POSTED' ? 'ok' : 'warn';
  return { doc, status, tone, values, next, notes, label: doc ? documentLabel('SCENARIO', doc.id) : null };
}
// T10 delivery-round card: the opening-balance boards (no serials) move by
// quantity, and new boards are produced from a LOT and followed to arrival.
// Every step is a one-click draft that another account approves and posts.
const FULFILL_KINDS = new Set(['COHORT', 'PRODUCTION', 'FG_RECEIPT', 'SHIPMENT', 'ARRIVAL']);
export function fulfillmentState(snapshot, documents, identity) {
  const can = permission => (identity?.permitted_actions || []).includes(permission);
  const docs = (documents || []).filter(row => !row.cancelled_by);
  const byId = new Map(docs.map(row => [row.id, row]));
  // Board steps that started from an existing LOT (the supply card follows the inspected-LOT chain).
  const lotOrigin = row => { for (let at = row; at; at = byId.get(at.parent_id)) if (at.kind === 'PRODUCTION') return Boolean(at.content?.lot_code); return false; };
  const mine = docs.filter(row => row.kind === 'COHORT' || (FULFILL_KINDS.has(row.kind) && lotOrigin(row)));
  const pending = mine.filter(row => row.status !== 'POSTED' && !(row.status === 'DRAFT' && row.rejection_reason)).at(-1) || null;
  const approver = can('document.approve'), create = approver || !can('document.create');
  const summary = snapshot?.summary || {}, pools = snapshot?.legacy_cohort || {}, boards = snapshot?.fulfillment?.boards || {};
  const next = [], notes = [];
  if (pending) {
    const own = pending.created_by === identity?.actor_id;
    if (pending.status === 'APPROVED') next.push({ label: '확정 (원장 반영)', action: 'post', target: pending.id, disabled: !can('document.post') });
    else {
      next.push({ label: '승인', action: 'review-approve', target: pending.id, disabled: own || !approver },
                { label: '반려', action: 'reject', target: pending.id, disabled: own || !can('document.review') });
      if (own) notes.push('작성자 본인은 승인할 수 없습니다. 상단에서 역할을 검토자로 바꾸세요.');
    }
  } else {
    for (const [stage, pool, label] of [['FG_RECEIPT', 'good_not_received', '입고'], ['SHIPMENT', 'warehouse', '출하'], ['ARRIVAL', 'in_transit', '도착']]) {
      if (pools[pool] > 0) next.push({ label: `기초 보드 ${label} · ${pools[pool]}대`, action: 'cohort', stage, quantity: pools[pool], disabled: create });
    }
    const item = summary.item_id, target = Number(summary.production_remaining_qty) || 0;
    const requirement = (snapshot?.material_requirements?.items || []).find(row => row.item_id === item);
    const perBoard = Number(requirement?.quantity_per_unit || requirement?.per_unit_qty || 1) || 1;
    const lot = (snapshot?.inventory || []).filter(row => row.item_id === item && (row.lot_id) && Number(row.available_qty) >= perBoard)
      .sort((a, b) => Number(b.available_qty) - Number(a.available_qty))[0];
    if (target > 0 && lot) {
      const n = Math.min(target, Math.floor(Number(lot.available_qty) / perBoard));
      next.push({ label: `생산 실적 기록 · ${n}대 (${lot.lot_id})`, action: 'lot-production', lot: lot.lot_id, quantity: n, disabled: create });
    }
    for (const row of mine.filter(row => row.status === 'POSTED' && nextStep[row.kind])) {
      const [kind, link, state, label] = nextStep[row.kind];
      const serials = Object.entries(boards).filter(([, value]) => value.status === state && value[link] === row.id).map(([serial]) => serial);
      if (serials.length) next.push({ label: `${label.replace(' 기록', '')} · ${serials.length}대`, action: 'board-next', target: row.id, kind, link, serials, disabled: create });
    }
    if (approver && next.length) notes.push('다음 기록은 작성자 계정이 만듭니다. 이 계정은 승인과 확정을 합니다.');
  }
  const rows = [['남은 약속', `${summary.promise_remaining_qty ?? '미확인'}대 · 도착 누계 ${summary.arrived_qty ?? '미확인'}대`],
    ['기초 보드', `입고 대기 ${pools.good_not_received ?? 0} · 창고 ${pools.warehouse ?? 0} · 운송 ${pools.in_transit ?? 0}대`],
    ['신규 생산', `남은 양품 목표 ${summary.production_remaining_qty ?? '미확인'}대`]];
  const done = Number(summary.promise_remaining_qty) === 0;
  return { pending, next, notes, rows, status: done ? '전량 도착' : pending ? '승인 진행' : '진행 중', tone: done ? 'ok' : pending ? 'warn' : 'info',
           label: pending ? documentLabel(pending.kind, pending.id) : null };
}
function fulfillmentLane(state, busy) {
  const [promise, ...rest] = state.rows;
  return lane({ key: 'data-fulfillment-card', title: '회차 이행', status: state.status, tone: state.tone,
    summary: `${promise[0]} ${promise[1]}${state.label ? ` · ${state.label}` : ''}`, entries: state.next, busy, fulfill: true,
    rows: rest, notes: state.notes, fixed: '기초 보드는 serial이 없는 기초 이관 수량이며 기록된 단계별 수량 안에서만 옮깁니다. 신규 보드는 LOT 가용 수량 안에서 생산합니다.' });
}
function scenarioLane(state, busy, extra = '') {
  const summary = state.doc ? `${state.label} · ${state.values.map(([label, value]) => `${label} ${value}`).join(' · ')}` : '가정 없음 · 승인하면 68대 질문의 일정 역산에 쓰입니다';
  return lane({ key: 'data-scenario-card', title: '일정 가정', status: state.status, tone: state.tone, summary, entries: state.next, busy,
    notes: state.notes, fixed: 'What-if 가정입니다. 원장 사실이 아니며 실제 납기 판단은 보류로 남습니다.', extra });
}
// One line above the rows when the next presses belong to the other account.
export function flowBanner(lanes, identity) {
  const approver = (identity?.permitted_actions || []).includes('document.approve');
  const waits = lanes.map(laneWaiting).filter(Boolean);
  const review = waits.filter(wait => wait === 'review').length, author = waits.filter(wait => wait === 'author').length;
  if (!approver && review) return { text: `검토자 승인 대기 ${review}건`, role: 'review', action: '검토자로 전환' };
  if (approver && author) return { text: `작성자 차례 ${author}건`, role: 'author', action: '작성자로 전환' };
  return null;
}

export function mountProcurementFlow(container, { snapshot, api, getIdentity = () => null, onPosted = () => {}, onRefresh = () => {}, onActivity = () => {} }) {
  const abort = new AbortController(), pending = new Map();
  let documents = [], choices = [], activeId = null, editor = null, busy = false, destroyed = false, fulfillment = null, supply = null;
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
      // A newer ledger event (for example another order's reservation) makes
      // this form's basis stale; offer the existing refresh instead of a dead end.
      if (error.message === 'PROCUREMENT_LEDGER_VERSION_CHANGED') needsRefresh = true;
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
    // Document steps (draft, approval, ...) shown in the ledger events list.
    api('/api/mks2/procurement/activity').then(result => { if (!destroyed) onActivity(result.activity || []); }, () => {});
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
  function form() {
    if (!editor) return '';
    const { kind, values } = editor;
    if (kind === 'REJECT') return `<form class="mk-bom-form" data-purchase-form><h4>문서 반려</h4><label class="mk-bom-field">반려 사유<textarea name="reason" minlength="8" maxlength="2000" required>${esc(values.reason || '')}</textarea></label><button class="mk-btn" ${busy ? 'disabled' : ''}>반려 기록</button>${button('접기', 'close-editor', busy)}</form>`;
    const editable = fields[kind].filter(key => !['_id', 'provenance'].includes(key) && !['quote_id', 'pr_id', 'po_id', 'receipt_id', 'target_id', 'iqc_id', 'production_id', 'fg_receipt_id', 'shipment_id', 'material_basis', 'acknowledge_partial_bom', 'acknowledge_unknown_freight'].includes(key));
    // A purchase request takes the server's recommended quantity as is (the
    // server refuses any other number), so it has no quantity or note field.
    const shown = kind === 'PR' ? editable.filter(key => !['quantity', 'note'].includes(key))
      : kind === 'SCENARIO' ? editable.filter(key => !['scenario_ref', 'round_id', 'note'].includes(key)) : editable;
    const prChoice = kind === 'PR' ? requestChoice(values) : null;
    return `<form class="mk-bom-form" data-purchase-form><h4>${esc(kinds[kind])} ${editor.revise ? '초안 수정' : '초안'}</h4>
      ${kind === 'SUPPLY_CONFIRMATION' ? '<p>실제 공급사 응답이 없는 내부 모의 확인입니다. 도착일을 확정하지 않습니다.</p>' : ''}
      ${shown.length ? `<div class="mk-bom-fields">${shown.map(key => key === 'note'
        ? `<label class="mk-bom-field">${labels[key]}<textarea name="${key}" minlength="8" maxlength="2000" required>${esc(values[key] || '')}</textarea></label>`
        : `<label class="mk-bom-field">${esc(labels[key] || key)}<input name="${key}" type="${key === 'valid_until' ? 'date' : numberFields.has(key) ? 'number' : 'text'}" value="${esc(values[key] ?? '')}" ${numberFields.has(key) ? `min="${['passed_qty', 'failed_qty', 'lead_business_days', 'inspection_business_days', 'ship_prep_business_days', 'transit_days'].includes(key) ? 0 : 1}" max="1000000000" step="1"` : ''} ${['freight', 'lead_business_days'].includes(key) ? '' : 'required'}></label>`).join('')}</div>` : ''}
      ${kind === 'PR' ? `<p>${prChoice ? `<strong>${esc(prChoice.calculation.recommended_qty)}${esc(unitWord(prChoice.unit))}</strong> · ${esc(prChoice.quote_ref)}(${esc(prChoice.supplier_id)}) 권장` : '견적 권장 수량을 다시 불러와 주세요'} · ${values.material_basis === 'planned' ? '계획 수율 적용안' : '기본 소요안'}</p><p class="mk-bom-ack">미확인 BOM 행·기술검수는 미확인으로 남으며, 승인 요청 시 확인으로 기록됩니다.</p>` : ''}
      ${kind === 'PO' ? `<label class="mk-bom-ack"><input type="checkbox" name="acknowledge_unknown_freight" ${values.acknowledge_unknown_freight ? 'checked' : ''}>운임 미확인을 유지하는 내부 발주임을 확인합니다. 총액은 미확인으로 남습니다.</label>` : ''}
      ${kind === 'QUOTE' ? '<p>시연 가정 견적입니다. 기초 견적에 없던 조건은 직접 확인하고 입력해 주세요.</p>' : ''}
      ${kind === 'PRODUCTION' ? '<p>이 LOT의 부품을 소비해 장착한 양품 보드를 serial로 기록합니다. 계획이나 예정이 아니라 실제 생산 실적만 기록합니다.</p>' : ''}
      ${kind === 'ARRIVAL' ? '<p>고객 인수 근거가 있는 보드만 도착으로 기록합니다. 출하를 도착으로 간주하지 않습니다.</p>' : ''}
      <div class="mk-bom-actions"><button class="mk-btn" ${busy || needsRefresh ? 'disabled' : ''}>${editor.revise ? '수정 후 승인 요청' : '승인 요청'}</button>${button('취소', 'close-editor', busy)}</div></form>`;
  }
  function requestChoice(values) {
    const choice = choices.find(row => row.quote_id === values.quote_id && row.material_basis === values.material_basis);
    return choice && !choice.expired && choice.calculation?.recommended_qty && choice.calculation.quantity_condition !== 'NOT_MET' ? choice : null;
  }
  // Demo board serials continue the round's numbering: <round>-B###-<stamp>.
  function boardSerials(count, stamp) {
    const first = Object.keys(snapshot.fulfillment?.boards || {}).length + 1, round = snapshot.summary?.delivery_round_id;
    return Array.from({ length: count }, (_, i) => `${round}-B${String(first + i).padStart(3, '0')}-${stamp.slice(-3)}`);
  }
  function boardsFor(doc, [, link, state]) {
    return Object.entries(snapshot.fulfillment?.boards || {}).filter(([, value]) => value.status === state && value[link] === doc.id).map(([serial]) => serial).sort();
  }
  function render() {
    if (destroyed) return;
    const status = notice && (failed || editor || needsRefresh) ? `<p role="status" aria-live="polite" class="mk-supply-notice${failed ? ' mk-bom-warning' : ''}">${esc(notice)}</p>` : `<p role="status" aria-live="polite" class="mk-supply-notice"></p>`;
    const refresh = needsRefresh ? `<p class="mk-supply-notice">원장에 새 사건이 반영되었습니다. 최신 원장 조회 후 다음 업무를 진행해 주세요. ${button('최신 원장 조회', 'refresh', busy)}</p>` : '';
    const scenarioEditing = editor?.kind === 'SCENARIO' || (editor?.kind === 'REJECT' && active()?.kind === 'SCENARIO');
    const progress = supply = supplyProgress(documents, snapshot, getIdentity(), scenarioEditing ? null : editor);
    if (progress && editor && !scenarioEditing) progress.next = [];
    const scenario = scenarioState(documents, getIdentity());
    if (scenarioEditing) scenario.next = [];
    fulfillment = fulfillmentState(snapshot, documents, getIdentity());
    const blocked = busy || needsRefresh;
    const banner = flowBanner([progress && !progress.empty ? progress.next : [], scenario.next, fulfillment.next], getIdentity());
    container.innerHTML = `<div class="mk-flow-board"><h3>진행 현황</h3>${banner ? `<p class="mk-flow-banner"><span>${esc(banner.text)}</span><button type="button" class="mk-btn primary" data-switch-role="${banner.role}"${busy ? ' disabled' : ''}>${esc(banner.action)}</button></p>` : ''}${status}${refresh}`
      + supplyLane(progress, blocked, scenarioEditing ? '' : form())
      + scenarioLane(scenario, blocked, scenarioEditing ? form() : '')
      + fulfillmentLane(fulfillment, blocked) + '</div>';
  }
  container.addEventListener('click', event => {
    const switcher = event.target.closest('[data-switch-role]');
    if (switcher && !switcher.disabled) { container.dispatchEvent(new CustomEvent('mks2:switch-role', { bubbles: true, detail: { role: switcher.dataset.switchRole } })); return; }
    const target = event.target.closest('[data-purchase-action]');
    if (!target || target.disabled || busy) return;
    if (target.dataset.doc && documents.some(row => row.id === target.dataset.doc)) activeId = target.dataset.doc;
    const action = target.dataset.purchaseAction, doc = active();
    if (action === 'refresh') { onRefresh(); return; }
    if (action === 'close-editor') { editor = null; render(); return; }
    if (!doc) return;
    if (action === 'revise') { start(doc.kind, Object.fromEntries(fields[doc.kind].map(key => [key, doc.content[key]])), doc); return; }
    if (action === 'reject') { start('REJECT', { reason: '' }); return; }
    if (action === 'new-po') { start('PO', { pr_id: doc.id, quantity: doc.remaining_qty, acknowledge_unknown_freight: false }); return; }
    if (action === 'new-confirmation') { start('SUPPLY_CONFIRMATION', { po_id: doc.id, confirmed_qty: doc.quantity - (doc.confirmed_qty || 0), confirmation_ref: '' }); return; }
    if (action === 'new-receipt') { start('RECEIPT', { po_id: doc.id, quantity: doc.receivable_qty, receipt_ref: '', lot_code: '' }); return; }
    if (['cohort', 'lot-production', 'board-next'].includes(action)) {
      const entry = fulfillment?.next[Number(target.dataset.fulfillIndex)];
      if (!entry || entry.disabled) return;
      const stamp = Date.now().toString(36).toUpperCase(), round = snapshot.summary?.delivery_round_id;
      let content;
      if (action === 'cohort') content = { kind: 'COHORT', cohort_ref: `CH-${entry.stage}-${stamp}`, round_id: round, stage: entry.stage, quantity: entry.quantity,
        note: `자동 작성 · 기초 보드 ${entry.quantity}대 ${entry.stage} 기록 (serial 없음)` };
      else if (action === 'lot-production') {
        const serials = boardSerials(entry.quantity, stamp);
        content = { kind: 'PRODUCTION', lot_code: entry.lot, production_ref: `PRD-${stamp}`, board_serials: serials,
          note: `자동 작성 · ${entry.lot}로 양품 ${entry.quantity}대 생산 실적 (serial 자동 생성)` };
      } else {
        const refField = { FG_RECEIPT: 'receipt_ref', SHIPMENT: 'shipment_ref', ARRIVAL: 'arrival_ref' }[entry.kind];
        content = { kind: entry.kind, [entry.link]: entry.target, [refField]: `${entry.kind}-${stamp}`, board_serials: entry.serials,
          note: `자동 작성 · 보드 ${entry.serials.length}대 ${entry.kind} 기록` };
      }
      work(async () => {
        const result = await command('/api/mks2/procurement/documents', { expected_ledger_version: ledgerVersion, content });
        if (destroyed) return;
        await reload(result.id);
        message('초안을 저장했습니다. 작성자와 다른 계정에서 승인·확정해 주세요.');
      });
      return;
    }
    if (action === 'new-scenario') {
      // Demo defaults the presenter can change before asking for approval.
      start('SCENARIO', { scenario_ref: `SCN-${Date.now().toString(36).toUpperCase()}`, round_id: snapshot.summary?.delivery_round_id,
        inspection_business_days: 1, daily_capacity: 20, ship_prep_business_days: 1, transit_days: 2 });
      return;
    }
    if (action === 'new-iqc') {
      // One click, like the shared screen: an inspection draft that passes all
      // received units is saved at once and waits for another account's
      // approval and posting (no self-approval, nothing posted here).
      const quantity = doc.remaining_qty ?? doc.quantity;
      const content = { kind: 'IQC', receipt_id: doc.id, passed_qty: quantity, failed_qty: 0,
        inspection_ref: `${documentLabel('IQC', doc.id)}-${Date.now().toString(36).toUpperCase()}`,
        note: `자동 작성 · 수입검사 합격 · ${documentLabel('RECEIPT', doc.id)} 입하 ${quantity}개 전량 · 승인 요청(버튼)` };
      work(async () => {
        const result = await command('/api/mks2/procurement/documents', { expected_ledger_version: ledgerVersion, content });
        if (destroyed) return;
        await reload(result.id);
        message('수입검사 합격 초안을 저장했습니다. 작성자와 다른 계정에서 승인·확정하면 가용 재고에 반영됩니다.');
      });
      return;
    }
    if (action === 'new-production') {
      // One click: the record number and board serials are generated, the
      // quantity is what the inspected LOT can still build. Another account
      // approves and posts it (nothing is posted here).
      const entry = supply?.next?.find(row => row.action === 'new-production' && row.target === doc.id);
      if (!entry || entry.disabled) return;
      const stamp = Date.now().toString(36).toUpperCase();
      const content = { kind: 'PRODUCTION', iqc_id: doc.id, production_ref: `PRD-${stamp}`, board_serials: boardSerials(entry.quantity, stamp),
        note: `자동 작성 · ${entry.lot || '입하 LOT'}로 양품 ${entry.quantity}대 생산 실적 (serial 자동 생성)` };
      work(async () => {
        const result = await command('/api/mks2/procurement/documents', { expected_ledger_version: ledgerVersion, content });
        if (destroyed) return;
        await reload(result.id);
        message('생산 실적 초안을 저장했습니다. 작성자와 다른 계정에서 승인·확정해 주세요.');
      });
      return;
    }
    if (action === 'new-next') {
      // One click: the record number is generated and the boards are the ones
      // still waiting for this step. Another account approves and posts it.
      const [kind, link, , stepLabel] = nextStep[doc.kind], refs = { FG_RECEIPT: 'receipt_ref', SHIPMENT: 'shipment_ref', ARRIVAL: 'arrival_ref' };
      const serials = boardsFor(doc, nextStep[doc.kind]);
      if (!serials.length) return;
      const stamp = Date.now().toString(36).toUpperCase();
      const content = { kind, [link]: doc.id, [refs[kind]]: `${kind}-${stamp}`, board_serials: serials,
        note: `자동 작성 · ${documentLabel(doc.kind, doc.id)} 보드 ${serials.length}대 ${stepLabel}` };
      work(async () => {
        const result = await command('/api/mks2/procurement/documents', { expected_ledger_version: ledgerVersion, content });
        if (destroyed) return;
        await reload(result.id);
        message(`${kinds[kind]} 초안을 저장했습니다. 작성자와 다른 계정에서 승인·확정해 주세요.`);
      });
      return;
    }
    if (action === 'review-approve') work(async () => {
      // One button, two exact-version commands by the same reviewer: review, then approve.
      const reviewed = await command(`/api/mks2/procurement/documents/${encodeURIComponent(doc.id)}/review`, exact(doc));
      if (destroyed) return;
      const approved = await command(`/api/mks2/procurement/documents/${encodeURIComponent(doc.id)}/approve`, exact(reviewed));
      if (destroyed) return;
      editor = null; await reload(approved.id);
      message('검토하고 승인했습니다. 원장 반영(확정)은 별도입니다.');
    });
    if (['review', 'approve', 'post'].includes(action)) work(async () => {
      const result = await command(`/api/mks2/procurement/documents/${encodeURIComponent(doc.id)}/${action}`, exact(doc));
      if (destroyed) return;
      editor = null;
      if (action === 'post') { needsRefresh = true; onPosted(result); }
      await reload(result.id);
      message({ review: '이 버전의 문서를 검토했습니다. 승인은 별도입니다.', approve: '이 버전의 문서를 승인했습니다. 원장 반영은 별도입니다.', post: '원장에 반영했습니다. 최신 원장에서 수량을 확인해 주세요.' }[action]);
    });
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
    if (current.kind === 'SCENARIO') {
      values.provenance = 'DEMO_ASSUMPTION';
      values.round_id = current.values.round_id; values.scenario_ref = current.values.scenario_ref;
      values.note = `일정 가정 · 검사 ${values.inspection_business_days}영업일 · 하루 ${values.daily_capacity}대 · 출하 준비 ${values.ship_prep_business_days}영업일 · 운송 ${values.transit_days}일 (What-if)`;
    }
    if (current.kind === 'PR') {
      const choice = requestChoice(values);
      if (!choice) { message('견적 권장 수량을 확인할 수 없습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.'); return; }
      values.quantity = choice.calculation.recommended_qty;
      // The unresolved-BOM notice is shown above the button; pressing it is the
      // acknowledgement, and the note records that it was given this way.
      values.acknowledge_partial_bom = true;
      values.note = `자동 작성 · 견적 ${choice.quote_ref}(${choice.supplier_id}) · ${choice.material_basis === 'planned' ? '계획 수율 적용안' : '기본 소요안'} · 권장 수량 ${values.quantity}${unitWord(choice.unit)} · 미확인 BOM 안내 확인(승인 요청 버튼)`;
    }
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
    if (can('document.approve')) return 'APPROVER_CANNOT_AUTHOR';
    const choice = choices.find(row => row.quote_id === quoteId && row.material_basis === basis);
    if (!choice || choice.expired || !choice.calculation?.recommended_qty || choice.calculation.quantity_condition === 'NOT_MET') return false;
    start('PR', { quote_id: choice.quote_id, quantity: choice.calculation.recommended_qty, material_basis: choice.material_basis, acknowledge_partial_bom: false });
    container.querySelector?.('[data-purchase-form]')?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    return true;
  }
  return { startPurchaseRequest, refresh() { return work(() => reload()); }, destroy() { destroyed = true; abort.abort(); pending.clear(); container.replaceChildren(); } };
}
