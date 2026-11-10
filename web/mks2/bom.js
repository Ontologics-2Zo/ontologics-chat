// BOM presentation and explicit commands. All parsing, authority, approval and
// ledger effects belong to the server; local row edits remain unsaved drafts.
import { createProductReview } from './product.js';
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const count = value => Number.isInteger(value) ? value.toLocaleString('ko-KR') : '미확인';
const stages = ['원본 접수', '개정 식별', '추출·검증', '개정 차이', '품목 연결', '등록 초안', '검토·승인', '반영'];
const statuses = { DRAFT: '초안', REVIEWED: '검토 완료', APPROVED: '승인 완료 · 미반영', POSTED: '원장 반영', REJECTED: '반려' };
const applications = { UNRESOLVED: '미확인', APPLICABLE: '적용 가정', NOT_MOUNTED: '비실장', SETTING: '설정' };
const bases = { UNREVIEWED: '미검수', SOURCE_VERIFIED: '원문 대조', DEMO_ASSUMPTION: '시연 가정' };
const linked = value => value?.application_status === 'APPLICABLE' && !!value.item_id;

export function preferredBomSource(sources, documents, configurationId) {
  const reviewed = documents.filter(doc => doc.status === 'POSTED' && doc.content?.configuration_id === configurationId
    && doc.content?.rows?.some(row => linked(row.decision))).sort((a, b) => (b.ledger_version || 0) - (a.ledger_version || 0));
  for (const doc of reviewed) {
    const matches = sources.filter(source => source.sha256 === doc.content.source?.sha256 && source.source_id === doc.content.source?.source_id);
    if (matches.length === 1) return matches[0];
  }
  return sources.length === 1 ? sources[0] : null;
}

/** The initial ledger's mapping assumptions name their source by a SHA-256
 *  prefix (e.g. "…:773c8b3fb0fa:49"). Offer that stored table source only when
 *  exactly one supported source matches. */
export function candidateSource(sources, candidates = []) {
  const prefixes = new Set(candidates.map(row => String(row?.source_row_id || '').split(':')[1]).filter(value => /^[0-9a-f]{12,64}$/.test(value)));
  if (prefixes.size !== 1) return null;
  const [prefix] = prefixes;
  const matches = sources.filter(source => source.sha256?.startsWith(prefix) && ['ods', 'csv'].includes(source.format));
  return matches.length === 1 ? matches[0] : null;
}

export function bomPresentation(rows, decision = row => row.decision || {}) {
  const positions = rows.flatMap(row => (row.designators || []).map(value => JSON.stringify([row.sheet, value])));
  const integer = value => { const match = /^(\d+)(?:\.0+)?$/.exec(String(value ?? '')); return match ? match[1].replace(/^0+/, '') || '0' : null; };
  return { rows: rows.length, positions: positions.length, uniquePositions: new Set(positions).size,
    linked: rows.filter(row => linked(decision(row))).length,
    positiveIntegers: rows.filter(row => integer(row.source_quantity) && integer(row.source_quantity) !== '0').length,
    matchingPositions: rows.filter(row => row.designators?.length > 0 && integer(row.source_quantity) === String(row.designators.length)).length,
    missingPositions: rows.filter(row => !row.designators?.length).length };
}

export function reviewFocusRows(document, response, drafts = new Map()) {
  const rows = document?.content?.rows || [], ref = response?.document;
  const valid = ref?.document_id === document?.id && ref?.version === document?.version && ref?.payload_hash === document?.payload_hash
    && response.read_only === true && response.selection_only === true && response.model_calls === 0 && response.ledger_mutations === 0
    && response.decisions_copied === false && response.approvals_copied === false;
  const ids = new Set(valid ? response.row_ids || [] : []);
  for (const row of rows) if (linked(drafts.get(row.row_id) || row.decision)) ids.add(row.row_id);
  return rows.filter(row => ids.has(row.row_id));
}
// Progress is based on server evidence, independently of the section being viewed.
export function bomStageStates(document, { processing = false, comparison = null } = {}) {
  if (processing) return ['active', 'pending', 'active', 'pending', 'pending', 'pending', 'pending', 'pending'];
  if (!document) return Array(8).fill('pending');
  const content = document.content || {}, rows = content.rows || [];
  const resolved = rows.filter(row => {
    const value = row.decision || {};
    return ['NOT_MOUNTED', 'SETTING'].includes(value.application_status)
      || (value.application_status === 'APPLICABLE' && String(value.item_id || '').trim() && String(value.unit || '').trim()
        && /^\d+(?:\.\d+)?$/.test(String(value.quantity_per_unit ?? '')) && /[1-9]/.test(String(value.quantity_per_unit)));
  }).length;
  const hasDecisions = rows.some(row => ['APPLICABLE', 'NOT_MOUNTED', 'SETTING'].includes(row.decision?.application_status));
  const comparisonState = comparison?.status === 'NEEDS_REVIEW' ? 'review'
    : ['COMPARED', 'IDENTICAL_SOURCE'].includes(comparison?.status) ? 'complete' : 'pending';
  return ['complete', content.source_conflicts?.includes('SOURCE_FILENAME_SHEET_CONFIGURATION_CONFLICT') ? 'review' : 'complete',
    'complete', comparisonState, resolved === rows.length && rows.length ? 'complete' : hasDecisions ? 'review' : 'pending',
    'complete', ['APPROVED', 'POSTED'].includes(document.status) ? 'complete' : document.status === 'REVIEWED' ? 'review' : 'pending',
    document.status === 'POSTED' ? 'complete' : 'pending'];
}

// Selecting a proposed value records a conditional user decision, never a
// source verification or technical approval. Unresolved/excluded rows stay null.
export function decisionFromChoices(row, fields) {
  const status = fields.application_status;
  if (!Object.hasOwn(applications, status)) throw Error('BOM_SELECTION_REQUIRED');
  const base = { application_status: status, review_basis: status === 'UNRESOLVED' ? 'UNREVIEWED' : 'DEMO_ASSUMPTION',
    item_id: null, quantity_per_unit: null, unit: null };
  if (status === 'APPLICABLE') {
    base.item_id = String(fields.item_id || '').trim() || null;
    base.quantity_per_unit = String(fields.quantity_per_unit || '').trim() || null;
    base.unit = String(fields.unit || '').trim() || null;
    if (!base.item_id || !base.quantity_per_unit || !base.unit) throw Error('BOM_SELECTION_REQUIRED');
  }
  const old = row.decision || {};
  if (Object.entries(base).every(([key, value]) => (old[key] ?? null) === value) && old.evidence_note && !fields.evidence_note?.trim()) {
    return { ...base, evidence_note: old.evidence_note };
  }
  const chosen = status === 'UNRESOLVED' ? '미확인 유지' : status === 'APPLICABLE' ? '시연에 적용' : status === 'NOT_MOUNTED' ? '시연에서 비실장' : '시연 설정 항목';
  const note = `사용자 선택: ${chosen}. 원문 ${row.locator}; 원문 수량 ${row.source_quantity_text || '미확인'}.`
    + (status === 'APPLICABLE' ? ` 품목 ${base.item_id}, 대당 ${base.quantity_per_unit}, 단위 ${base.unit}.` : '')
    + ' 제조 기술검수·품목 동일성·단위 확정 승인이 아님.';
  return { ...base, evidence_note: `${note}${fields.evidence_note?.trim() ? ` 추가 의견: ${fields.evidence_note.trim()}` : ''}`.slice(0, 2000) };
}
const option = (value, label, current) => `<option value="${esc(value)}"${value === current ? ' selected' : ''}>${esc(label)}</option>`;
const button = (label, action, disabled = false, primary = false) => `<button type="button" class="mk-btn${primary ? ' primary' : ''}" data-bom-action="${action}"${disabled ? ' disabled' : ''}>${esc(label)}</button>`;
const errors = {
  BOM_TABLE_FORMAT_NOT_SUPPORTED: '현재 BOM 표 등록은 ODS·CSV를 지원합니다. PDF는 텍스트 추출 단계이며 TXT·스캔 PDF 등록은 아직 지원하지 않습니다.',
  BOM_ITEM_SELECTION_AMBIGUOUS: '같은 코드의 사내 품목이 여러 개이거나 현재 목록에 없습니다. 품목 기준정보를 확인해 주세요.',
  BOM_SELECTION_REQUIRED: '적용할 사내 품목·대당 수량·단위를 선택해 주세요. 확인할 수 없으면 미확인으로 보류할 수 있습니다.',
  RUNNER_UNAVAILABLE: '제한된 파일 처리기가 준비되지 않았습니다. 서버의 Runner 설정을 확인해 주세요.',
  RUNNER_ENFORCEMENT_UNAVAILABLE: '이 실행환경의 파일 처리 자원 제한 미지원으로 원본 접수가 차단되었습니다. 제한을 지원하는 환경에서 다시 확인해 주세요.',
  RUNNER_TIMEOUT: '원본 처리 시간이 초과되었습니다. 원본 상태를 확인한 뒤 다시 시도해 주세요.',
  BOM_SOURCE_NOT_FOUND: '원본 목록이 바뀌었습니다. 목록을 다시 조회해 주세요.',
  SELF_REVIEW_FORBIDDEN: '작성자는 자신의 등록 초안을 검토할 수 없습니다. 다른 검토 계정으로 전환해 주세요.',
  SELF_APPROVAL_FORBIDDEN: '작성자는 자신의 등록 초안을 승인할 수 없습니다. 다른 승인 계정으로 전환해 주세요.',
  BOM_SELF_REVIEW_FORBIDDEN: '작성자는 자신의 등록 초안을 검토할 수 없습니다. 다른 검토 계정으로 전환해 주세요.',
  BOM_SELF_APPROVAL_FORBIDDEN: '작성자는 자신의 등록 초안을 승인할 수 없습니다. 다른 승인 계정으로 전환해 주세요.',
  DOCUMENT_VERSION_CHANGED: '문서 버전이 바뀌었습니다. 서버 문서를 다시 조회한 뒤 검토해 주세요.',
  BOM_VERSION_CHANGED: '문서 버전이 바뀌었습니다. 서버 문서를 다시 조회한 뒤 검토해 주세요.',
  LEDGER_VERSION_CHANGED: '기준 원장이 바뀌었습니다. 최신 원장 조회 후 초안의 기준을 다시 검토해 주세요.',
  BOM_UNRESOLVED_ACKNOWLEDGEMENT_REQUIRED: '미확인 행과 원본 충돌을 확인했다는 표시 및 검토 의견이 필요합니다.',
  EXACT_APPROVAL_REQUIRED: '이 문서 버전과 내용에 대한 승인이 필요합니다.',
};
function errorMessage(error) {
  if (errors[error.message]) return errors[error.message];
  if (error.status === 403) return '현재 계정에는 이 작업 권한이 없습니다. 작성자와 검토·승인자를 확인해 주세요.';
  if (error.status === 409) return '문서 상태나 기준 버전이 바뀌었습니다. 다시 조회해 확인해 주세요.';
  if (error.status === 422) return '입력값 또는 검수 근거가 맞지 않습니다. 행별 상태·수량·단위·품목·근거를 확인해 주세요.';
  return '요청 결과를 확인하지 못했습니다. 서버 문서를 다시 조회하거나 같은 작업을 다시 시도해 주세요. 같은 입력의 재시도는 동일 요청 키를 사용합니다.';
}

export function createBomComparison({ api, onChange = () => {} }) {
  let baseline = '', result = null, failure = '', busy = false, generation = 0;
  const ref = doc => ({ document_id: doc.id, expected_version: doc.version, payload_hash: doc.payload_hash });
  function reset() { generation++; baseline = ''; result = null; failure = ''; busy = false; }
  async function load(document, documents, selected = baseline) {
    baseline = selected; result = null; failure = ''; busy = true;
    const current = ++generation, previous = documents.find(doc => doc.id === baseline);
    const request = { left: previous ? ref(previous) : null, right: ref(document) };
    onChange();
    try {
      if (baseline && !previous) throw Error('COMPARISON_BASELINE_CHANGED');
      const response = await api('/api/mks2/bom/compare', request);
      if (current !== generation) return;
      const matches = (value, expected) => expected ? value?.document_id === expected.document_id
        && value?.version === expected.expected_version && value?.payload_hash === expected.payload_hash : value === null;
      if (!matches(response.left, request.left) || !matches(response.right, request.right)
        || response.read_only !== true || response.model_calls !== 0 || response.ledger_mutations !== 0) throw Error('COMPARISON_RESULT_MISMATCH');
      result = response;
    } catch (error) {
      if (current !== generation) return;
      failure = error.status === 409 ? '비교할 문서 버전이 바뀌었습니다. 등록 문서를 다시 조회해 주세요.'
        : error.status === 403 ? '선택한 문서를 비교할 권한이 없습니다.' : '비교 결과를 확인하지 못했습니다. 기준 문서를 확인한 뒤 다시 비교해 주세요.';
    } finally { if (current === generation) { busy = false; onChange(); } }
  }
  function render(document, documents) {
    const labels = {ADDED:'추가',REMOVED:'삭제',CHANGED:'변경',UNCHANGED:'동일',NEEDS_REVIEW:'대응 확인 필요'};
    const cell = row => row ? `<b>${esc(row.source_description || '원문 값 미기재')}</b><br>원문 수량 ${esc(row.source_quantity_text || '미확인')}<small>${esc(row.locator)}</small><details><summary>원문 셀 전체</summary><ol class="mk-bom-cells">${(row.cells || []).map(value => `<li>${esc(value) || '〈빈 셀〉'}</li>`).join('')}</ol></details>` : '—';
    const reasons = { MISSING_DESIGNATOR:'위치 식별값 없음', DUPLICATE_DESIGNATOR:'중복된 위치 표기', AMBIGUOUS_ROW_CORRESPONDENCE:'행 분할·병합 또는 중복 대응', SHEET_CORRESPONDENCE_UNKNOWN:'시트 대응 확인 필요', UNRESOLVED_DESIGNATOR:'실제 위치인지 확인할 표기', QUANTITY_UNCONFIRMED:'원문 수량 미확인', DESIGNATOR_SET_CHANGED:'부품 위치 묶음 변경', SAME_SOURCE_EXTRACTION_DIFFERS:'같은 원본의 추출 결과가 다름' };
    const warningLabels = {DIFFERENT_CONFIGURATION_EXPLICIT_BASELINE:'선택한 두 문서의 기준 구성이 다릅니다.', SINGLE_SHEET_NAMES_DIFFER:'두 원본의 시트명이 다릅니다. 위치 표기를 기준으로 비교한 결과입니다.', SAME_SOURCE_EXTRACTION_DIFFERS:'같은 원본 hash에서 추출 결과 차이가 발견됐습니다.'};
    const changes = (result?.rows || []).filter(row => row.change_type !== 'UNCHANGED');
    return `<section class="mk-drawer-block" data-bom-section="3"><h3>개정 대비 차이${result && result.status !== 'NO_BASELINE' ? ` <span class="mk-pill info">${count((result.rows || []).filter(row => row.change_type !== 'UNCHANGED').length)}위치</span>` : ''}</h3><label class="mk-bom-field">비교 기준 문서<select data-bom-field="compare-baseline"${busy ? ' disabled' : ''}>${option('','기준 없음 · 최초 등록 / 비교하지 않음',baseline)}${documents.filter(doc => doc.id !== document.id).map(doc => option(doc.id, `${doc.content?.source?.filename || doc.id} · 문서 v${doc.version} · ${statuses[doc.status] || doc.status} · ${doc.id.slice(-6)}`,baseline)).join('')}</select></label>
      <p class="mk-small-note">기준 문서를 선택하면 원문 위치·수량·값을 비교합니다. 두 문서의 제조 구성이나 개정 계보를 자동 확정하지 않습니다.</p>
      ${busy ? '<p role="status">저장된 두 원본을 비교하고 있습니다…</p>' : failure ? `<p role="status" class="mk-bom-notice error">${esc(failure)}</p>` : result ? `<p class="mk-bom-compare-summary">${result.status === 'NO_BASELINE' ? '비교 기준 없음 · 최초 등록으로 원문 검토를 진행할 수 있습니다.' : result.status === 'IDENTICAL_SOURCE' ? '같은 원본입니다. 원문 변경은 없으며, 문서별 검수 판단·승인 상태는 별개입니다.' : `추가 ${count(result.summary.added)} · 삭제 ${count(result.summary.removed)} · 변경 ${count(result.summary.changed)} · 동일 ${count(result.summary.unchanged)} · 대응 확인 ${count(result.summary.needs_review)}`}</p>
      ${result.left ? `<p class="mk-small-note">기준 ${esc(result.left.filename)} v${count(result.left.version)} → 현재 ${esc(result.right.filename)} v${count(result.right.version)}</p>` : ''}${(result.warnings || []).map(warning => `<p class="mk-small-note">${esc(warningLabels[warning] || '원본 대응의 추가 확인이 필요합니다.')}</p>`).join('')}${result.source_warnings?.left?.length ? '<p class="mk-small-note">기준 문서에도 원본 식별·검수 경고가 있습니다. 비교 완료가 경고 해소를 뜻하지 않습니다.</p>' : ''}
      ${changes.length ? `<div class="mk-table-scroll mk-bom-review-table"><table class="mk-bom-source-table" data-bom-table="comparison"><thead><tr><th>구분 / 부품 위치</th><th>기준 원본</th><th>현재 원본</th></tr></thead><tbody>${changes.map(row => `<tr><td>${esc(labels[row.change_type] || row.change_type)}<br>${esc((row.right?.designators || row.left?.designators || []).join(', '))}<small>${esc((row.review_reasons || []).map(reason => reasons[reason] || '위치·시트 대응의 추가 확인이 필요합니다.').join(' · '))}</small></td><td>${cell(row.left)}</td><td>${cell(row.right)}</td></tr>`).join('')}</tbody></table></div>` : ''}` : '<p class="mk-small-note">비교 기준을 선택해 주세요. 기준이 없으면 다른 검토 단계를 계속 진행할 수 있습니다.</p>'}
      <div class="mk-bom-actions">${button('다시 비교', 'compare', busy)}</div><p class="mk-small-note">비교는 읽기 전용입니다. 미확인·중복 위치는 별도 검토하며, 이전 품목 연결과 승인을 자동 이월하지 않습니다.</p></section>`;
  }
  return { load, render, reset, result: () => result };
}

/** Identify a local file only by SHA-256 against stored server sources.
 *  The file content is never uploaded or parsed in the browser. */
export function identifyBomFile(sha, sources, documents, configurationId) {
  if (!sha) return { ok: false, why: '이 환경에서는 파일 해시를 계산할 수 없어 개정을 식별하지 못했습니다.' };
  const match = sources.find(source => source.sha256 === sha);
  if (!match) return { ok: false, why: '보관된 원본 목록과 해시가 일치하지 않습니다. 파일 이름이 같아도 근거가 확인되지 않은 구성은 등록하지 않습니다.' };
  if (!['ods', 'csv'].includes(match.format)) return { ok: false, source: match, why: `보관 원본 ${match.filename}과 일치하지만 ${String(match.format).toUpperCase()}는 BOM 표 등록을 지원하지 않습니다.` };
  const other = documents.find(doc => doc.status === 'POSTED' && doc.content?.source?.sha256 === sha && doc.content?.configuration_id !== configurationId);
  if (other) return { ok: false, source: match, why: `이 원본은 이미 ${other.content.configuration_id} 구성으로 등록돼 있어 ${configurationId} 구성으로 새로 등록하지 않습니다.` };
  return { ok: true, source: match };
}

/** Prefill only from the initial ledger's synthetic mapping assumptions.
 *  Rows must match the candidate's source row number and designators exactly;
 *  rows already decided are left alone. Nothing is copied from past approvals. */
export function candidateDecisions(rows, candidates = []) {
  const same = (a = [], b = []) => a.length === b.length && [...a].sort().join('|') === [...b].sort().join('|');
  const result = new Map();
  for (const candidate of candidates) {
    const quantity = candidate?.qty_per_board_assumption;
    if (!candidate?.item_id || !candidate.unit_assumption || !(Number(quantity) > 0)) continue;
    const matches = rows.filter(row => row.source_row === candidate.source_row_number && same(row.designators, candidate.refdes));
    if (matches.length !== 1 || (matches[0].decision?.application_status || 'UNRESOLVED') !== 'UNRESOLVED') continue;
    const row = matches[0];
    result.set(row.row_id, { application_status: 'APPLICABLE', review_basis: 'DEMO_ASSUMPTION', item_id: candidate.item_id,
      quantity_per_unit: String(quantity), unit: candidate.unit_assumption,
      evidence_note: `기초 원장 시연 매핑 가정 ${(candidate.assumption_refs || []).join(', ') || '참조 미기재'} · 원문 ${row.locator}. 제조 기술검수·품목 동일성·단위 확정 승인이 아님.` });
  }
  return result;
}

/** Reference sheet flow: 초안 → 승인 요청 → 승인 → 반영.
 *  A saved draft with decisions is the author's approval request. */
export function bomRequestFlow(document, { dirty = false } = {}) {
  const status = document?.status;
  const decided = (document?.content?.rows || []).some(row => ['APPLICABLE', 'NOT_MOUNTED', 'SETTING'].includes(row.decision?.application_status));
  const requested = status === 'DRAFT' && !dirty && decided && document.version > 1 && !document.change_request;
  const index = status === 'POSTED' ? 4 : status === 'APPROVED' ? 2 : status === 'REVIEWED' || requested ? 1 : 0;
  return { requested, steps: ['초안', '승인 요청', '승인', '반영'].map((label, i) => ({ label,
    state: status === 'REJECTED' ? 'rejected' : i < index ? 'done' : i === index ? 'on' : 'pending' })) };
}

/** Reviewer flow shown as 초안 → 검토 → 승인 → 반영 from the server status. */
export function bomFlowStates(status) {
  const index = { DRAFT: 0, REVIEWED: 1, APPROVED: 2, POSTED: 3 }[status];
  return ['초안', '검토', '승인', '반영'].map((label, i) => ({ label,
    state: status === 'REJECTED' ? 'rejected' : index === undefined ? 'pending' : i < index || status === 'POSTED' ? 'done' : i === index ? 'on' : 'pending' }));
}

/** Chip display like the reference sheet: once posted, every stage shows as
 *  done; stages that still carry review items or had no baseline keep a note. */
export function bomStageDisplay(states, document, comparison = null) {
  if (document?.status !== 'POSTED') return states.map(state => ({ state, note: '' }));
  return states.map((state, index) => state === 'review' ? { state: 'noted', note: '반영 완료 · 확인 필요 항목 남음' }
    : index === 3 && state === 'pending' && comparison?.status === 'NO_BASELINE' ? { state: 'noted', note: '최초 등록 · 비교 기준 없음' }
    : { state, note: '' });
}

export function bomReferenceMatches(document, reference, configurationId) {
  return !!document && !!reference && typeof reference.document_id === 'string'
    && Number.isSafeInteger(reference.version) && reference.version > 0
    && typeof reference.payload_hash === 'string' && /^[a-f0-9]{64}$/.test(reference.payload_hash)
    && document.id === reference.document_id && document.version === reference.version
    && document.payload_hash === reference.payload_hash
    && typeof configurationId === 'string' && !!configurationId
    && document.content?.configuration_id === configurationId;
}

export function createBomDrawer({ snapshot, api, getIdentity = () => null, onPosted = () => {}, onRefresh = () => {}, onShowGraph = null }) {
  let wrap = null, focusBefore = null, step = 0, active = null, sources = [], documents = [], runner = null;
  let busy = false, operation = '', generation = 0, notice = '', failure = false, selectedRow = null;
  let selectedSource = '', scrollTarget = null, fileCheck = null, outcomeReason = '', sourceFirst = false, candidateStaged = 0;
  let focusResult = null, focusFailure = false, rowFilter = 'focus';
  let drafts = new Map(), dirty = false, reviewNote = '', reviewerNote = '', acknowledged = false;
  // Text still being typed is separate from explicitly staged row decisions.
  let rowInputs = new Map(), discardRowInputs = false;
  const product = createProductReview({ api, onChange: () => {
    const slot = wrap?.querySelector('[data-product-review-slot]');
    if (slot) slot.innerHTML = product.render();
    const selector = wrap?.querySelector('[name="item_id"]');
    if (selector) { const selected = selector.value; selector.innerHTML = itemOptions(selected); }
  } });
  const comparison = createBomComparison({ api, onChange: () => {
    const slot = wrap?.querySelector('[data-bom-comparison-slot]');
    if (slot && active) slot.innerHTML = comparison.render(active, documents);
    updateStepAppearance();
  } });
  const abort = new AbortController(), pendingCommands = new Map();
  const permitted = action => (getIdentity()?.permitted_actions || []).includes(action);
  const isAuthor = () => active?.created_by === getIdentity()?.actor_id;
  const editable = () => active && active.status !== 'POSTED' && isAuthor() && permitted('document.update');
  const content = () => active?.content || {};
  const allRows = () => content().rows || [];
  const decision = row => drafts.get(row.row_id) || row.decision || {};
  const focusRows = () => reviewFocusRows(active, focusResult, drafts);
  const exact = () => ({ expected_version: active.version, payload_hash: active.payload_hash });
  const currentLedger = () => snapshot.meta.current_ledger_version || snapshot.meta.ledger_version;
  const setNotice = (text, isError = false) => { notice = text; failure = isError; };
  function resetDrafts() {
    product.reset();
    comparison.reset();
    focusResult = null; focusFailure = false; rowFilter = 'focus';
    drafts = new Map(); dirty = false; selectedRow = null;
    rowInputs = new Map(); discardRowInputs = true;
    reviewNote = content().review_note || ''; reviewerNote = ''; acknowledged = false; outcomeReason = '';
  }
  async function handleFile(file) {
    if (!file || busy || !wrap) return;
    let sha = null;
    try {
      if (globalThis.crypto?.subtle) sha = [...new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer()))]
        .map(value => value.toString(16).padStart(2, '0')).join('');
    } catch { sha = null; }
    if (!wrap) return;
    const result = identifyBomFile(sha, sources, documents, snapshot.summary.configuration_id);
    fileCheck = { file: file.name, sha, ...result };
    if (!result.ok) { setNotice('선택한 파일을 등록하지 않았습니다. 보류 사유를 확인해 주세요.', true); render(); return; }
    selectedSource = result.source.source_id;
    setNotice(`보관 원본 ${result.source.filename}과 SHA-256이 일치합니다. 보관 원본으로 추출·검증합니다.`);
    activate('create');
  }
  function close() {
    generation++;
    product.reset();
    comparison.reset();
    if (!wrap) return;
    wrap.remove(); wrap = null; busy = false; operation = ''; scrollTarget = null;
    focusBefore?.focus(); focusBefore = null;
  }
  async function command(action, path, body) {
    const signature = JSON.stringify([action, path, body]);
    let key = pendingCommands.get(signature);
    if (!key) { key = `bom-ui-${crypto.randomUUID()}`; pendingCommands.set(signature, key); }
    const result = await api(path, { ...body, idempotency_key: key });
    pendingCommands.delete(signature);
    return result;
  }
  async function work(action, kind = '') {
    if (busy || !wrap) return;
    busy = true; operation = kind; const current = generation; setNotice(kind === 'create' ? '선택한 원본의 추출·검증 결과를 기다리고 있습니다.' : '서버에서 확인하고 있습니다.'); render();
    try {
      await action(() => current === generation && !!wrap);
    } catch (error) {
      if (error.name !== 'AbortError' && current === generation && wrap) setNotice(errorMessage(error), true);
    } finally {
      if (current === generation && wrap) { busy = false; operation = ''; render(); }
    }
  }
  async function fetchDocuments(preferred) {
    const response = await api('/api/mks2/bom/documents');
    const rows = response.documents || [];
    let doc = null;
    const id = preferred || active?.id || (rows.find(row => row.status !== 'REJECTED') || rows[0])?.id;
    if (id) doc = await api(`/api/mks2/bom/documents/${encodeURIComponent(id)}`);
    return { rows, doc };
  }
  async function reload(preferred, rowId = null, expectedDocument = null) {
    await work(async current => {
      const [catalog, stored] = await Promise.all([api('/api/mks2/bom/sources'), fetchDocuments(preferred)]);
      if (!current()) return;
      if (expectedDocument && !bomReferenceMatches(stored.doc, expectedDocument, snapshot.summary.configuration_id)) {
        throw new Error('DOCUMENT_VERSION_CHANGED');
      }
      sources = catalog.sources || []; runner = catalog.runner;
      documents = stored.rows; active = stored.doc; resetDrafts(); selectedRow = rowId;
      await loadFocus(current);
      if (!current()) return;
      setNotice('서버의 원본 목록과 등록 문서를 조회했습니다.');
      if (step === 0 && active && !sourceFirst) step = 1;
      prepareSheet();
      if (step >= 1 && step <= 4) scrollTarget = rowId ? 'editor' : step;
    });
  }
  async function loadFocus(current) {
    if (!active) return;
    const selected = active;
    try {
      const response = await api('/api/mks2/bom/review-focus', { document_id: selected.id, expected_version: selected.version, payload_hash: selected.payload_hash });
      if (!current() || active !== selected) return;
      const ref = response.document;
      if (ref?.document_id !== selected.id || ref.version !== selected.version || ref.payload_hash !== selected.payload_hash
          || response.read_only !== true || response.selection_only !== true || response.model_calls !== 0 || response.ledger_mutations !== 0
          || response.decisions_copied !== false || response.approvals_copied !== false) throw Error('FOCUS_RESULT_MISMATCH');
      focusResult = response;
    } catch { if (current() && active === selected) focusFailure = true; }
  }
  // Reference sheet: stage the initial ledger's five mapping assumptions into a
  // fresh draft (author still saves and requests approval) and compare with the
  // latest other posted document, read-only.
  function prepareSheet() {
    candidateStaged = 0;
    if (!active) return;
    // Only a fresh draft (version 1) is prefilled; a saved choice is never overridden.
    if (active.status === 'DRAFT' && active.version === 1 && editable() && drafts.size === 0 && !allRows().some(row => linked(row.decision))) {
      const staged = candidateDecisions(allRows(), snapshot.mapping_candidates || []);
      if (staged.size) {
        drafts = staged; dirty = true; candidateStaged = staged.size;
        if (!reviewNote) reviewNote = '원문 전체 행과 미확인을 보존하고, 기초 원장의 시연 매핑 가정 5행만 조건부 적용 판단으로 저장합니다. 제조 기술검수 승인이 아닙니다.';
      }
    }
    const baseline = documents.filter(doc => doc.id !== active.id && doc.status === 'POSTED' && doc.content?.configuration_id === content().configuration_id)
      .sort((a, b) => (b.ledger_version || 0) - (a.ledger_version || 0))[0];
    comparison.load(active, documents, baseline?.id || '');
  }
  function stats() {
    const summary = bomPresentation(allRows(), decision);
    return `<div class="mk-bom-stats"><div><b>${count(summary.rows)}</b><span>BOM 행 · 원본 보존</span></div><div><b>${count(summary.positions)}</b><span>부품 위치 표기</span></div><div><b class="mk-warn-number">${count(allRows().filter(row => decision(row).application_status === 'UNRESOLVED').length)}</b><span>적용 미확인 행</span></div><div><b>${count(summary.linked)}</b><span>사내 품목 연결${dirty ? ' · 초안' : ''}</span></div></div>`;
  }
  function validationChecks() {
    const summary = bomPresentation(allRows()), available = summary.rows > 0;
    const check = (state, text) => `<span class="mk-bom-check" data-state="${state}">${esc(text)}</span>`;
    return `<div class="mk-bom-checks">${check(content().source?.sha256 ? 'pass' : 'unknown', '원본 SHA-256 기록 보관')}${check(available ? 'pass' : 'unknown', 'BOM 열 구조 추출 완료')}${check(available && summary.positiveIntegers === summary.rows ? 'pass' : 'review', '원문 수량이 양의 정수')}${check(available && summary.matchingPositions === summary.rows ? 'pass' : 'review', '원문 수량 = 위치 표기 수')}${check(available && !summary.missingPositions && summary.uniquePositions === summary.positions ? 'pass' : 'review', '위치 누락·중복 표기 없음')}${check(content().source?.format === 'pdf' ? 'unknown' : 'na', content().source?.format === 'pdf' ? 'PDF 제품 식별은 별도 검토' : 'PDF 교차 검증 · 해당 없음')}</div>`;
  }
  function conflicts() {
    const conflicts = content().source_conflicts || [];
    const quantityUnknown = allRows().filter(row => row.source_quantity == null).length;
    const positionUnknown = allRows().filter(row => !row.designators?.length).length;
    const unknownSummary = [quantityUnknown ? `수량 미확인 ${count(quantityUnknown)}행` : '', positionUnknown ? `부품 위치 식별값 없음 ${count(positionUnknown)}행` : ''].filter(Boolean).join(' · ');
    const positions = new Map();
    for (const row of allRows()) for (const position of row.designators || []) {
      const key = JSON.stringify([row.sheet, position]);
      positions.set(key, (positions.get(key) || 0) + 1);
    }
    const evidence = allRows().flatMap(row => {
      const reasons = [];
      if (conflicts.includes('SOURCE_ROW_REQUIRES_REVIEW')) {
        if (row.source_quantity == null) reasons.push(`수량 미확인 (원문: ${row.source_quantity_text || '빈 셀'})`);
        if (!row.designators?.length) reasons.push('부품 위치 식별값 없음');
      }
      if (conflicts.includes('DUPLICATE_SOURCE_DESIGNATOR')) {
        const duplicates = [...new Set((row.designators || []).filter(position => positions.get(JSON.stringify([row.sheet, position])) > 1))];
        if (duplicates.length) reasons.push(`중복 표기: ${duplicates.join(', ')}`);
      }
      return reasons.length ? [`<li><button type="button" class="mk-link" data-bom-edit="${esc(row.row_id)}">${esc(row.locator)}</button> · ${esc(reasons.join(' / '))}</li>`] : [];
    });
    return conflicts.length ? `<section class="mk-drawer-block mk-bom-warning"><h3>원본 식별·검수 확인 필요</h3><p>대상 원본: <b>${esc(content().source?.filename)}</b></p><ul>${conflicts.map(value => `<li>${esc(({ SOURCE_FILENAME_SHEET_CONFIGURATION_CONFLICT: '원본 파일명과 시트명의 구성 식별이 다릅니다. 적용 구성의 확인 근거가 필요합니다.', SOURCE_ROW_REQUIRES_REVIEW: unknownSummary || '수량 또는 부품 위치가 미확인인 원문 행이 있습니다.', DUPLICATE_SOURCE_DESIGNATOR: '같은 시트에 중복된 부품 위치 표기가 있습니다. 반복된 일반 명칭인지 실제 위치 충돌인지 원문 확인이 필요합니다.' })[value] || value)}</li>`).join('')}</ul>${evidence.length ? `<details><summary>경고가 발생한 원문 ${count(evidence.length)}행 확인</summary><ul>${evidence.join('')}</ul><p>저장된 추출값 기준입니다. 원문 행을 눌러 셀과 적용 판단을 대조할 수 있습니다.</p></details>` : ''}<p>원본 등록 승인은 기술 적합성이나 제조 투입 승인을 뜻하지 않습니다. 미해결 항목은 원장에도 그대로 남습니다.</p></section>` : '';
  }
  // Reference: the sheet name may differ from the file name; keep the source as
  // is and point to the row that identifies the variant, without resolving it.
  function identityNote() {
    if (!content().source_conflicts?.includes('SOURCE_FILENAME_SHEET_CONFIGURATION_CONFLICT')) return '';
    const evidence = allRows().find(row => /WROOM|WROVER/i.test(row.source_description || ''));
    return `<p class="mk-small-note">시트명은 파일명과 다르지만 원문 그대로 보존합니다.${evidence ? ` 변형 확인 근거 후보: ${esc((evidence.designators || []).join(' '))} 원문 값 ${esc(evidence.source_description)}(${esc(evidence.locator)}).` : ''} 구성 확정은 검토자가 원문으로 확인합니다.</p>`;
  }
  function documentHeader(compact = false) {
    if (!active) return '';
    if (compact) return `<section class="mk-drawer-block mk-bom-identity-compact" data-bom-section="1"><div class="mk-bom-report-header"><h3>개정 식별</h3>${content().source?.sha256 && sources.some(source => source.sha256 === content().source.sha256) ? '<span class="mk-pill ok">SHA-256 일치</span>' : ''}<span class="mk-pill info">${esc(statuses[active.status] || active.status)} · v${count(active.version)}</span></div><dl class="mk-key-values"><dt>원본 파일</dt><dd>${esc(content().source?.filename)}</dd><dt>SHA-256</dt><dd class="mk-hash">${esc(content().source?.sha256)}</dd><dt>제품 구성</dt><dd>${esc(content().configuration_id)}</dd><dt>원문 시트</dt><dd>${esc([...new Set(allRows().map(row => row.sheet))].join(' · '))}</dd></dl>${identityNote()}<details class="mk-bom-source-details"><summary>원본·버전·검수 근거</summary>${documentHeader()}</details></section>`;
    return `<section class="mk-drawer-block"><h3>${esc(statuses[active.status] || active.status)} · 문서 v${count(active.version)}</h3><dl class="mk-key-values"><dt>원본</dt><dd>${esc(content().source?.filename)}</dd><dt>기준 구성</dt><dd>${esc(content().configuration_id)}</dd><dt>기준 원장</dt><dd>v${count(content().expected_ledger_version)}</dd><dt>기술 검수</dt><dd>${esc(content().technical_review === 'UNREVIEWED' ? '미검수' : content().technical_review || '미확인')} · 제조 적용과 구분</dd><dt>문서 hash</dt><dd>${esc(active.payload_hash)}</dd><dt>원본 SHA-256</dt><dd>${esc(content().source?.sha256)}</dd></dl><div class="mk-bom-actions">${button('보관 원본 내려받기', 'download', busy)}</div></section>`;
  }
  function rawTable() {
    const rows = allRows();
    return `<details class="mk-bom-source-details" data-bom-original><summary>원본 전체 ${count(rows.length)}행 · 시연 범위 밖 ${count(rows.length - focusRows().length)}행 포함</summary><section class="mk-drawer-block"><p>모든 원문·미확인 판단을 보존합니다. 원문 내용은 실행 지시가 아닙니다.</p><div class="mk-table-scroll mk-bom-review-table"><table class="mk-bom-source-table" data-bom-table="source"><thead><tr><th>근거 위치</th><th>부품 위치</th><th>원문 수량</th><th>설명 / 원문 셀</th><th>적용 상태</th></tr></thead><tbody>${rows.map(row => `<tr data-bom-row="${esc(row.row_id)}"><td><button type="button" class="mk-link" data-bom-edit="${esc(row.row_id)}">${esc(row.locator || `${row.sheet}:${row.source_row}`)}</button></td><td>${esc((row.designators || []).join(', ')) || '미확인'}</td><td>${esc(row.source_quantity_text ?? row.source_quantity ?? '미확인')}</td><td>${esc(row.source_description)}<details><summary>원문 셀 전체</summary><ol class="mk-bom-cells">${(row.cells || []).map(cell => `<li>${esc(cell) || '〈빈 셀〉'}</li>`).join('')}</ol></details></td><td>${esc(applications[decision(row).application_status] || '미확인')}<small>${esc(bases[decision(row).review_basis] || '미검수')}</small></td></tr>`).join('')}</tbody></table></div></section></details>`;
  }
  function itemOptions(current) {
    const masters = (snapshot.entities || []).filter(entity => entity.type === 'InternalItem');
    const frequencies = new Map();
    for (const entity of masters) frequencies.set(entity.display_id, (frequencies.get(entity.display_id) || 0) + 1);
    const candidates = new Set(product.candidates().map(candidate => candidate.item_id));
    const options = group => group.map(entity => frequencies.get(entity.display_id) > 1 ? `<option value="${esc(entity.display_id)}" disabled${entity.display_id === current ? ' selected' : ''}>${esc(entity.display_id)} · 동일 코드 복수, 확인 필요</option>` : option(entity.display_id, `${entity.label || entity.attributes?.display_name || entity.display_id} · ${entity.display_id}`, current)).join('');
    return option('', '미연결 · 품목 선택', current) + (candidates.size ? `<optgroup label="조회된 후보 · 동일성 미확인">${options(masters.filter(entity => candidates.has(entity.display_id)))}</optgroup>` : '')
      + `<optgroup label="등록된 사내 품목">${options(masters.filter(entity => !candidates.has(entity.display_id)))}</optgroup>`
      + (current && !masters.some(entity => entity.display_id === current) ? option(current, `${current} · 기존 연결, 기준정보 확인 필요`, current) : '');
  }
  function rowEditor() {
    const row = allRows().find(item => item.row_id === selectedRow);
    if (!row) return '<p class="mk-small-note">연결 내용을 확인하거나 바꾸려면 표에서 해당 행을 선택하세요.</p>';
    const value = decision(row), disabled = !editable() || busy, attr = disabled ? ' disabled' : '';
    const quantities = [...new Set([value.quantity_per_unit, row.source_quantity].filter(Boolean))];
    const units = [...new Set([value.unit, ...(snapshot.inventory || []).map(item => item.unit), ...(snapshot.entities || []).flatMap(entity => [entity.attributes?.unit, entity.attributes?.unit_assumption])].filter(Boolean))];
    return `<section class="mk-drawer-block mk-bom-row-review"><div class="mk-bom-row-heading"><h3>선택 행 검토 · ${esc((row.designators || []).join(', ') || row.locator)}</h3>${button('선택 닫기', 'close-row', busy)}</div><p>${esc(row.source_description)} · 원문 수량 ${esc(row.source_quantity_text ?? '미확인')}</p>
      <form data-bom-form="row" class="mk-bom-form"><input type="hidden" name="row_id" value="${esc(row.row_id)}"><div class="mk-bom-fields">
      <label>검토 결론<select name="application_status"${attr}>${Object.entries({UNRESOLVED:'미확인으로 보류',APPLICABLE:'시연에 적용',NOT_MOUNTED:'시연에서 비실장',SETTING:'시연 설정 항목'}).map(([key,label]) => option(key,label,value.application_status || 'UNRESOLVED')).join('')}</select></label>
      <label>사내 품목<select name="item_id"${attr}>${itemOptions(value.item_id || '')}</select></label>
      <label>대당 수량<select name="quantity_per_unit"${attr}>${option('', '미확인 · 수량 선택', value.quantity_per_unit || '')}${quantities.map(quantity => option(quantity, `${quantity} · ${quantity === row.source_quantity ? '원문 수량을 시연에 사용' : '기존 검토 수량'}`,value.quantity_per_unit)).join('')}${option('__custom__','직접 지정…', '')}</select></label>
      <label>단위<select name="unit"${attr}>${option('', '미확인 · 단위 선택', value.unit || '')}${units.map(unit => option(unit, `${unit}${unit === 'EA' ? ' (개)' : ''} · 기존 원장/검토 단위`,value.unit)).join('')}${option('__custom__','직접 지정…', '')}</select></label></div>
      <div class="mk-bom-fields" data-custom-fields hidden><label data-custom-quantity hidden>직접 지정할 대당 수량<input name="custom_quantity" inputmode="decimal" placeholder="수량 근거 확인 후 입력"${attr}></label><label data-custom-unit hidden>직접 지정할 단위<input name="custom_unit" maxlength="24" placeholder="단위 확인 후 입력"${attr}></label></div>
      <details class="mk-bom-row-details"><summary>원문·기존 판단 근거 / 추가 의견</summary><p class="mk-small-note">${esc(row.locator)}</p><ol class="mk-bom-selected-cells">${(row.cells || []).map(cell => `<li>${esc(cell) || '〈빈 셀〉'}</li>`).join('')}</ol><p>${esc(value.evidence_note || '기존 판단 근거 없음')}</p><label class="mk-bom-field">추가 의견 (선택)<textarea name="evidence_note" maxlength="1000" rows="2"${attr}></textarea></label><div data-product-review-slot>${product.render()}</div></details>
      <p class="mk-small-note">선택한 값과 원문 위치를 검토 기록으로 남깁니다. 적용은 시연 가정이며 기술 적합성 승인이 아닙니다.</p><button class="mk-btn primary" type="submit"${attr}>선택을 초안에 담기</button></form>
      ${!editable() ? '<p class="mk-small-note">현재 저장 내용을 조회하고 있습니다. 반영 전 문서의 작성자만 수정할 수 있습니다.</p>' : ''}</section>`;
  }
  function syncRowChoices() {
    const form = wrap?.querySelector('[data-bom-form="row"]');
    if (!form) return;
    const applies = form.elements.application_status.value === 'APPLICABLE';
    for (const name of ['item_id','quantity_per_unit','unit','custom_quantity','custom_unit']) form.elements[name].disabled = !applies || !editable() || busy;
    const customQuantity = applies && form.elements.quantity_per_unit.value === '__custom__';
    const customUnit = applies && form.elements.unit.value === '__custom__';
    form.querySelector('[data-custom-fields]').hidden = !customQuantity && !customUnit;
    form.querySelector('[data-custom-quantity]').hidden = !customQuantity;
    form.querySelector('[data-custom-unit]').hidden = !customUnit;
  }
  function mappingTable() {
    const rows = focusRows();
    const connected = rows.filter(row => linked(decision(row))).length;
    const itemNames = new Map((snapshot.entities || []).filter(entity => entity.type === 'InternalItem').map(entity => [entity.display_id, entity.label || entity.display_id]));
    return `<section class="mk-drawer-block"><div class="mk-bom-report-header"><h3>사내 품목 연결</h3><span class="mk-pill info">시연 대상 ${count(rows.length)}행</span></div><p>${count(connected)}행 연결 · 검토 대기 ${count(rows.length - connected)}행${dirty ? ' · 미저장 선택 포함' : ''}</p>${rows.length ? `<div class="mk-table-scroll mk-bom-review-table"><table class="mk-bom-source-table" data-bom-table="mapping"><thead><tr><th>BOM 행</th><th>부품 위치</th><th>원문 값</th><th>사내 품목</th><th>1대당 / 단위</th><th>검토 상태</th></tr></thead><tbody>${rows.map(row => {
      const value = decision(row);
      return `<tr><td><button type="button" class="mk-link" data-bom-edit="${esc(row.row_id)}">${esc(row.source_row)}</button></td><td>${esc((row.designators || []).join(', ') || '미확인')}</td><td>${esc(row.source_description)}</td><td>${esc(value.item_id || '미연결')}<small>${esc(itemNames.get(value.item_id) || '')}</small></td><td>${esc(value.quantity_per_unit ?? '미확인')} / ${esc(value.unit || '미확인')}</td><td>${esc(applications[value.application_status] || '미확인')}<small>${esc(bases[value.review_basis] || '미검수')}</small></td></tr>`;
    }).join('')}</tbody></table></div>` : '<p>이 원본에는 확인된 시연 검토 대상이 없습니다. 원본 상세에서 검토할 행을 선택할 수 있습니다.</p>'}<p class="mk-small-note">선택한 대상만 사내 적용 정보를 검토합니다. 나머지 행은 원본 상세에 보존합니다.</p></section>`;
  }
  function changedLocators() {
    return new Set((comparison.result()?.rows || []).filter(row => !['UNCHANGED'].includes(row.change_type))
      .map(row => row.right?.locator).filter(Boolean));
  }
  function extractionReport() {
    const focus = focusRows(), changed = changedLocators(), all = allRows();
    const unresolvedRows = all.filter(row => decision(row).application_status === 'UNRESOLVED');
    const filters = { focus: ['시연 대상', focus], all: ['전체', all], unresolved: ['적용 미확인', unresolvedRows],
      changed: ['개정 변경', all.filter(row => changed.has(row.locator))], linked: ['품목 연결', all.filter(row => linked(decision(row)))] };
    const rows = (filters[rowFilter] || filters.focus)[1];
    const itemNames = new Map((snapshot.entities || []).filter(entity => entity.type === 'InternalItem').map(entity => [entity.display_id, entity.label || entity.display_id]));
    return `<section class="mk-drawer-block" data-bom-section="2"><div class="mk-bom-report-header"><h3>추출·검증</h3><span class="mk-pill info">${content().source?.source_kind === 'actual_bom' ? '실제 원본' : '저장된 원본'}</span></div>${stats()}${validationChecks()}<p class="mk-bom-scope-note">전체 BOM 중 일부 품목 기준 · 시연 검토 ${count(focus.length)}행 · 원본 ${count(all.length)}행 보존${dirty ? ' · 미저장 선택 포함' : ''}</p>
      <div class="mk-bom-filter" role="group" aria-label="추출 결과 표시">${Object.entries(filters).map(([key, [label, list]]) => `<button type="button" data-bom-filter="${key}" aria-pressed="${rowFilter === key}"${key === 'changed' && !comparison.result() ? ' title="개정 차이에서 비교 기준을 선택하면 표시됩니다"' : ''}>${label} ${count(list.length)}</button>`).join('')}</div>
      ${rows.length ? `<div class="mk-table-scroll mk-bom-review-table"><table class="mk-bom-source-table" data-bom-table="extraction"><thead><tr><th>행</th><th>수량</th><th>위치</th><th>원문 값</th><th>적용</th><th>사내 품목</th></tr></thead><tbody>${rows.map(row => { const value = decision(row); return `<tr class="${value.application_status === 'UNRESOLVED' ? 'mk-row-unresolved' : changed.has(row.locator) ? 'mk-row-changed' : ''}"><td><button type="button" class="mk-link" data-bom-edit="${esc(row.row_id)}">${esc(row.source_row)}</button></td><td class="mk-numeric">${esc(row.source_quantity_text || '미확인')}</td><td>${esc((row.designators || []).join(' ') || '미확인')}</td><td>${esc(row.source_description)}</td><td>${value.application_status === 'UNRESOLVED' ? '<span class="mk-pill warn">미확인</span>' : esc(applications[value.application_status] || '미확인')}</td><td>${linked(value) ? `<span class="chip" title="${esc(itemNames.get(value.item_id) || '')}">${esc(value.item_id)}</span>` : ''}</td></tr>`; }).join('')}</tbody></table></div>` : `<p class="mk-small-note">${rowFilter === 'changed' && !comparison.result() ? '개정 차이 단계에서 비교 기준 문서를 고르면 변경 행을 표시합니다.' : '조건에 맞는 행이 없습니다.'}</p>`}
      <p class="mk-small-note">원문의 NA·NS·Opened 같은 표기는 실장 여부를 확정하지 않고 미확인으로 남깁니다. 미확인 행은 소요 계산에 넣지 않으며 부족 0을 뜻하지 않습니다.</p>
      ${focusResult?.basis === 'POSTED_SAME_SOURCE' ? '<p class="mk-small-note">같은 원본의 기존 검토 이력에서 대상 행만 불러왔습니다. 품목·수량·판단·승인은 복사하지 않습니다.</p>' : ''}${focusFailure ? '<p class="mk-small-note">이전 시연 대상 조회를 확인하지 못했습니다. 현재 문서에 연결된 행만 표시합니다.</p>' : ''}<p class="mk-small-note">원본 적용 판단 미확인 ${count(unresolvedRows.length)}행 · 제조 기술검수는 별도입니다.</p>${content().source_conflicts?.length ? `<details class="mk-bom-source-details"><summary>원본 식별·검수 확인 필요 · ${count(content().source_conflicts.length)}항목</summary>${conflicts()}</details>` : ''}${rawTable()}</section>`;
  }
  function draftForm() {
    return `<section class="mk-drawer-block"><h3>검수 초안 ${dirty ? '· 미저장 선택 있음' : '· 서버 저장본'}</h3><label class="mk-bom-field">등록·검수 의견<select data-bom-field="review-preset"${!editable() || busy ? ' disabled' : ''}>${option('', reviewNote ? '현재 기록된 의견 유지' : '의견 선택', '')}${option('preserve','원문 전체 보존 · 선택 행만 조건부 시연 적용','')}${option('hold','미확인 항목은 보완 검토 대기로 유지','')}</select></label><details class="mk-bom-row-details"><summary>기록될 의견 확인 / 직접 수정</summary><label class="mk-bom-field">등록·검수 의견<textarea data-bom-field="review-note" rows="2" maxlength="3000"${!editable() || busy ? ' disabled' : ''}>${esc(reviewNote)}</textarea></label></details><p class="mk-small-note">저장하면 새 문서 버전이 생기고 기존 검토·승인은 해제됩니다. 미확인 행은 그대로 보존합니다.</p>${button('검수 초안 저장', 'revise', !editable() || busy, true)}</section>`;
  }
  function flowChips() {
    if (!active) return '';
    return `<div class="mk-flow" aria-label="검토 흐름">${bomFlowStates(active.status).map(item => `<span class="${item.state}">${esc(item.label)}</span>`).join('<i aria-hidden="true">→</i>')}</div>`;
  }
  function outcomeBanner() {
    if (active?.status === 'REJECTED') return `<div class="mk-warnbox" role="status"><b>반려된 문서입니다.</b> ${esc(active.rejection?.reason || '')}<div class="mk-small-note">반려 문서는 수정·승인·반영할 수 없습니다. 원본 접수에서 새 검수 초안을 시작하세요.</div></div>`;
    if (active?.change_request) return `<div class="mk-warnbox" role="status"><b>보완 요청</b> ${esc(active.change_request.reason)}<div class="mk-small-note">문서 v${count(active.change_request.version)} 검토 중 요청됨. 작성자가 검수 초안을 다시 저장하면 이 요청은 닫히고 새 버전으로 다시 검토합니다.</div></div>`;
    return '';
  }
  function reviewerOutcomes() {
    const allowed = active && !isAuthor() && permitted('document.review') && ['DRAFT', 'REVIEWED', 'APPROVED'].includes(active.status);
    if (!allowed) return '';
    const disabled = busy || dirty;
    return `<details class="mk-bom-row-details" data-bom-outcomes><summary>보완 요청 · 반려</summary><label class="mk-bom-field">사유 (8자 이상)<textarea data-bom-field="outcome-reason" rows="2" maxlength="2000"${disabled ? ' disabled' : ''}>${esc(outcomeReason)}</textarea></label><div class="mk-bom-actions">${button('보완 요청', 'request-changes', disabled)}${button('반려', 'reject', disabled)}</div><p class="mk-small-note">보완 요청은 초안으로 되돌리고 기존 검토·승인을 해제합니다. 반려는 이 문서를 종료합니다. 어느 쪽도 원장에 반영하지 않습니다.</p></details>`;
  }
  function sourcePicker() {
    const shortcut = preferredBomSource(sources, documents, snapshot.summary.configuration_id) || candidateSource(sources, snapshot.mapping_candidates || []);
    const disabled = busy || runner?.ready !== true || !permitted('document.create');
    const check = fileCheck && !fileCheck.ok ? `<div class="mk-warnbox" role="alert"><b>등록 보류.</b> ${esc(fileCheck.why)}<div class="mk-hash">${esc(fileCheck.file)} · ${esc(fileCheck.sha || '해시 없음')}</div></div>` : '';
    return `${check}<label class="mk-drop" data-bom-drop><b>BOM 원본 파일을 끌어다 놓거나 선택하세요</b><span>ODS · CSV · PDF. 파일의 SHA-256으로 보관된 원본과 개정을 식별합니다.</span><span class="mk-small-note">파일 내용은 서버로 올리지 않습니다. 해시가 일치하는 보관 원본만 제한된 처리기로 추출합니다.</span><input type="file" data-bom-field="file" accept=".ods,.csv,.pdf" hidden${busy || !permitted('document.create') ? ' disabled' : ''}><span class="mk-btn">파일 선택</span></label>
      ${shortcut && ['ods','csv'].includes(shortcut.format) ? `<div class="mk-bom-source-pick"><button type="button" class="mk-btn primary" data-bom-source="${esc(shortcut.source_id)}"${disabled ? ' disabled' : ''}>보관된 원본 사용 · ${esc(shortcut.filename)}</button><span class="mk-small-note">서버 보관 원본 · ${count(shortcut.bytes)} bytes</span></div>` : ''}
      <details class="mk-bom-source-details" data-bom-source-picker><summary>다른 보관 파일 선택</summary><label class="mk-bom-field">원본 파일<select data-bom-field="source"${busy ? ' disabled' : ''}><option value="">원본을 선택하세요</option>${sources.map(source => `<option value="${esc(source.source_id)}"${source.source_id === selectedSource ? ' selected' : ''}${['ods','csv'].includes(source.format) ? '' : ' disabled'}>${esc(source.filename)} · ${esc(source.format.toUpperCase())}${['ods','csv'].includes(source.format) ? '' : ' · BOM 표 등록 미지원'}</option>`).join('')}</select></label><div class="mk-bom-actions">${button('선택 원본 추출·검증', 'create', disabled, true)}</div></details>
      <p class="mk-small-note">${runner?.ready === true ? '파일 처리기 준비됨' : runner?.code === 'RUNNER_ENFORCEMENT_UNAVAILABLE' ? '파일 처리 자원 제한 미지원 · 원본 접수 차단' : '파일 처리기 준비 필요'} · 현재는 서버에 보관된 원본을 사용합니다.</p><details class="mk-bom-source-details"><summary>지원 형식 안내</summary><p>ODS·CSV: 지원 열 구조의 BOM 표를 등록합니다. PDF: 텍스트 추출만 준비돼 있으며 BOM 열 인식·스캔 문서 OCR은 미지원입니다. 보관 목록에 없는 새 파일의 업로드·등록은 아직 연결되지 않았습니다.</p></details>`;
  }
  function actBlock() {
    const flow = bomRequestFlow(active, { dirty }), status = active.status, author = isAuthor();
    const reviewer = !author && permitted('document.review') && permitted('document.approve');
    const chips = `<div class="mk-flow" aria-label="등록 흐름">${flow.steps.map(item => `<span class="${item.state}">${esc(item.label)}</span>`).join('<i aria-hidden="true">→</i>')}</div>`;
    const people = `작성 ${esc(active.created_by)}${active.reviewed_by ? ` · 검토 ${esc(active.reviewed_by)}` : ''}${active.approved_by ? ` · 승인 ${esc(active.approved_by)}` : ''}`;
    let actions = '', note = '';
    if (status === 'DRAFT' && author) {
      if (dirty || !flow.requested) { actions = button('등록 초안 저장 · 승인 요청', 'revise', busy || !editable(), true); note = '초안을 저장해도 승인·반영되지 않습니다. 다른 담당자의 검토·승인 후 반영해야 그래프와 자재 계산에 쓰입니다.'; }
      else note = '승인 요청됨 · 다른 담당자의 검토·승인을 기다립니다. 작성자 본인은 승인할 수 없습니다.';
    } else if ((status === 'DRAFT' && flow.requested) || status === 'REVIEWED') {
      if (reviewer) actions = `<label class="mk-bom-ack"><input type="checkbox" data-bom-field="acknowledge"${acknowledged ? ' checked' : ''}${busy ? ' disabled' : ''}>미확인 행과 원본 식별 차이를 확인했으며, 원본 등록과 제조 기술 승인을 구분합니다.</label>${button('승인', 'approve-flow', busy || dirty || !acknowledged, true)}`;
      else note = author ? '작성자 본인은 승인할 수 없습니다. 상단에서 역할을 다른 검토 계정으로 바꾸세요.' : '현재 계정에는 승인 권한이 없습니다.';
    } else if (status === 'DRAFT') note = '작성자가 등록 초안을 저장해 승인을 요청하면 검토할 수 있습니다.';
    else if (status === 'APPROVED') { actions = button('반영 (원장 확정)', 'post', busy || !permitted('document.post'), true); note = '승인됐지만 아직 원장과 그래프에 반영되지 않았습니다.'; }
    else if (status === 'POSTED') actions = `<span class="mk-pill ok">반영 완료 · 원장 v${count(active.ledger_version)}</span>${onShowGraph ? button('그래프에서 보기', 'show-graph', busy, true) : ''}${button('최신 원장·소요량 조회', 'refresh', busy)}`;
    return `<section class="mk-drawer-block" data-bom-section="5"><div class="mk-bom-report-header"><h3>${status === 'DRAFT' && !flow.requested ? '등록 초안' : '검토·승인'}</h3><span class="mk-small-note">${people}</span></div>${chips}${outcomeBanner()}<div class="mk-bom-actions">${actions}</div>${note ? `<p class="mk-small-note">${esc(note)}</p>` : ''}${reviewerOutcomes()}</section>`;
  }
  function linkBlock() {
    const rows = allRows(), connected = rows.filter(row => linked(decision(row)));
    const itemNames = new Map((snapshot.entities || []).filter(entity => entity.type === 'InternalItem').map(entity => [entity.display_id, entity.attributes?.display_name || entity.label || entity.display_id]));
    return `<section class="mk-drawer-block" data-bom-section="4"><div class="mk-bom-report-header"><h3>사내 품목 연결</h3><span class="mk-pill mute">${count(connected.length)}행 연결 · ${count(rows.length - connected.length)}행 미연결</span></div>
      ${connected.length ? `<div class="mk-table-scroll mk-bom-review-table"><table class="mk-bom-source-table" data-bom-table="mapping"><thead><tr><th>행</th><th>위치</th><th>원문 값</th><th>사내 품목</th><th>대당</th><th>근거</th></tr></thead><tbody>${connected.map(row => { const value = decision(row); return `<tr><td><button type="button" class="mk-link" data-bom-edit="${esc(row.row_id)}">${esc(row.source_row)}</button></td><td>${esc((row.designators || []).join(' '))}</td><td>${esc(row.source_description)}</td><td><span class="chip">${esc(value.item_id)}</span><small>${esc(itemNames.get(value.item_id) || '')}</small></td><td class="mk-numeric">${esc(value.quantity_per_unit)} ${esc(value.unit)}</td><td>${esc(bases[value.review_basis] || '미검수')}</td></tr>`; }).join('')}</tbody></table></div>` : '<p class="mk-small-note">연결된 사내 품목이 없습니다.</p>'}
      <p class="mk-small-note">${candidateStaged ? `기초 원장의 시연 매핑 가정 ${count(candidateStaged)}행을 초안에 담았습니다(미저장). ` : ''}연결은 시연 가정이며 품목 동일성·단위·실장 확정이 아닙니다. 연결하지 않은 행은 소요 계산에서 미확인으로 남습니다.</p>
      <details class="mk-bom-row-details"><summary>연결 수정 · 행 선택</summary><div data-bom-section="editor">${rowEditor()}</div></details></section>`;
  }
  function sheetPage() {
    const verdict = active.status === 'POSTED' ? `<div class="mk-verdict ok"><b>${esc(content().configuration_id)} 반영 완료</b>BOM ${count(allRows().length)}행이 원장 v${count(active.ledger_version)}에 반영됐고, 그래프에 구성 → BOM 스냅샷 → BOM 행 → 원문 행·적용 → 사내 품목 관계가 생겼습니다. 적용 미확인 행은 소요 계산에서 미확인으로 남습니다.</div>` : '';
    return `<div class="mk-bom-review-page">${verdict}${actBlock()}${documentHeader(true)}${extractionReport()}<div data-bom-comparison-slot>${comparison.render(active, documents)}</div>${linkBlock()}
      <details class="mk-bom-source-details"><summary>등록 문서 · 검토 의견 · 원본 관리</summary><div class="mk-bom-toolbar"><label class="mk-bom-field">등록 문서<select data-bom-field="document"${busy ? ' disabled' : ''}>${documents.map(doc => option(doc.id, `${doc.content?.source?.filename || doc.id} · v${doc.version} · ${statuses[doc.status] || doc.status}`, active?.id)).join('')}</select></label>${button('다시 조회', 'reload', busy)}${button('새 원본 접수', 'source-view', busy)}</div>${draftForm()}${documentHeader()}</details></div>`;
  }
  function stageBody() {
    if (operation === 'create') return `<section class="mk-drawer-block mk-bom-processing"><h3>추출·검증 중</h3><p>${esc(sources.find(source => source.source_id === selectedSource)?.filename || '선택한 원본')}</p><div class="mk-bom-progress" role="progressbar" aria-label="BOM 원본 추출·검증 진행 중"><span></span></div><p>원본 식별 · 시트와 행 추출 · 수량과 부품 위치 확인</p><p class="mk-small-note">서버 처리 중입니다. 완료되면 검증 결과와 품목 연결을 함께 보여드립니다.</p></section>`;
    if (step === 0 || !active) return `${sourcePicker()}${active ? `<div class="mk-bom-actions">${button('현재 등록 문서 보기', 'sheet-view', busy)}</div>` : ''}`;
    return sheetPage();
  }
  function updateStepAppearance() {
    if (!wrap) return;
    const progress = bomStageDisplay(bomStageStates(active, { processing: operation === 'create', comparison: comparison.result() }), active, comparison.result());
    const labels = { complete:'기록 완료', active:'처리 중', review:'검토 필요', pending:'대기', noted:'완료' };
    for (const control of wrap.querySelectorAll('[data-bom-step]')) {
      const index = Number(control.dataset.bomStep), status = progress[index].state;
      control.dataset.progress = status;
      control.title = `${stages[index]} · ${labels[status]}${progress[index].note ? ` · ${progress[index].note}` : ''}`;
      control.setAttribute('aria-label', control.title);
      control.setAttribute('aria-current', index === (operation === 'create' ? 2 : step) ? 'step' : 'false');
      control.textContent = `${status === 'complete' || status === 'noted' ? '✓ ' : status === 'review' ? '! ' : ''}${stages[index]}`;
    }
  }
  function render() {
    if (!wrap) return;
    const oldForm = wrap.querySelector('[data-bom-form="row"]');
    if (oldForm && !discardRowInputs && editable()) {
      const values = Object.fromEntries(new FormData(oldForm));
      if (values.row_id) rowInputs.set(values.row_id, { ...rowInputs.get(values.row_id), ...values });
    }
    discardRowInputs = false;
    if (step >= 1 && step <= 4 && active && !busy) product.load(snapshot, active, selectedRow);
    const restoreFocus = wrap.contains(document.activeElement);
    const previousScroll = wrap.querySelector('.mk-drawer-body')?.scrollTop || 0;
    wrap.innerHTML = `<div class="mk-drawer-scrim" data-close></div><section class="mk-drawer" role="dialog" aria-modal="true" aria-labelledby="mk-drawer-title" aria-busy="${busy}"><header><div><div class="mk-drawer-eyebrow">제품 구성 · BOM 원본 등록</div><h2 id="mk-drawer-title">${esc(snapshot.summary.configuration_id || 'BOM 등록')}</h2></div><button type="button" class="mk-btn" data-close>닫기</button></header><nav class="mk-drawer-steps" aria-label="BOM 처리 단계">${stages.map((label,index) => `<button type="button" data-bom-step="${index}" aria-current="${index === (operation === 'create' ? 2 : step) ? 'step' : 'false'}"${busy ? ' disabled' : ''}>${label}</button>`).join('')}</nav><div class="mk-drawer-body"><p class="mk-bom-notice${failure ? ' error' : ''}" role="status" aria-live="polite">${esc(notice)}</p>${stageBody()}<p class="mk-preview-notice">현재 회사·계정의 서버 문서만 조회합니다. 단계 선택은 상태를 바꾸지 않으며 검토·승인·반영은 각각 명시적으로 실행합니다.</p></div></section>`;
    const form = wrap.querySelector('[data-bom-form="row"]');
    const inputValues = rowInputs.get(form?.elements.namedItem('row_id')?.value);
    if (form && inputValues && editable()) for (const [name, value] of Object.entries(inputValues)) {
      const field = form.elements.namedItem(name);
      if (field) field.value = value;
    }
    syncRowChoices();
    updateStepAppearance();
    if (restoreFocus) (wrap.querySelector(`[data-bom-step="${step}"]:not(:disabled)`) || wrap.querySelector('.mk-drawer [data-close]')).focus();
    const target = scrollTarget === null ? null : wrap.querySelector(`[data-bom-section="${scrollTarget}"]`);
    if (target) target.scrollIntoView({ block: 'start' });
    else wrap.querySelector('.mk-drawer-body').scrollTop = scrollTarget === null ? previousScroll : 0;
    scrollTarget = null;
  }
  function selectStep(next) {
    // Steps 1–7 share one sheet page; moving only scrolls, so a partially
    // typed row form is never replaced before the author stages it.
    const section = next >= 5 ? 5 : next;
    if (next >= 1 && active && wrap.querySelector('.mk-bom-review-page')) {
      step = next; updateStepAppearance();
      wrap.querySelector(`[data-bom-section="${section}"]`)?.scrollIntoView({ block: 'start' });
      return;
    }
    step = next; scrollTarget = section; render();
  }
  async function activate(action) {
    if (action === 'source-view' && !busy) { selectStep(0); return; }
    if (action === 'sheet-view' && !busy && active) { selectStep(1); return; }
    if (action === 'choose-source' && !busy) { const picker = wrap?.querySelector('[data-bom-source-picker]'); if (picker) { picker.open = true; picker.querySelector('select')?.focus(); } return; }
    if (action === 'show-original' && !busy) { const original = wrap?.querySelector('[data-bom-original]'); if (original) { original.open = true; original.scrollIntoView({ block: 'start' }); } return; }
    if (action === 'compare' && active && !busy) { comparison.load(active, documents); return; }
    if (action === 'close-row' && !busy) { selectedRow = null; product.reset(); scrollTarget = 4; render(); return; }
    if (action === 'approval-view' && !busy) { selectStep(6); return; }
    if (action === 'show-graph' && onShowGraph) { close(); onShowGraph(); return; }
    if (action === 'reload') { await reload(); return; }
    if (action === 'refresh') { close(); onRefresh(); return; }
    if (action === 'download' && active) {
      const documentId = active.id, filename = String(content().source?.filename || 'bom-source').replace(/[\\/\0]/g, '_');
      await work(async current => {
        const blob = await api(`/api/mks2/bom/documents/${encodeURIComponent(documentId)}/source`, undefined, 'GET', { binary: true });
        if (!current()) return;
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        setNotice('보관 원본을 내려받았습니다. 표시된 원본 SHA-256과 대조할 수 있습니다.');
      });
      return;
    }
    if (action === 'approve-flow' && active && !busy) {
      if (!acknowledged) { setNotice(errors.BOM_UNRESOLVED_ACKNOWLEDGEMENT_REQUIRED, true); render(); return; }
      const base = `/api/mks2/bom/documents/${encodeURIComponent(active.id)}`;
      await work(async current => {
        let document = active;
        if (document.status === 'DRAFT') {
          document = await command('review', `${base}/review`, { ...exact(), review_note: reviewerNote || '원문과 등록 판단 및 미확인 사항을 검토했습니다. 미해결 항목을 유지하며 원본 등록 승인과 제조 기술검수를 구분합니다.', acknowledge_unresolved: true });
          if (!current()) return;
          active = document;
        }
        document = await command('approve', `${base}/approve`, { expected_version: document.version, payload_hash: document.payload_hash });
        if (!current()) return;
        active = document; resetDrafts();
        const index = documents.findIndex(doc => doc.id === active.id);
        if (index >= 0) documents[index] = active;
        step = 6; scrollTarget = 5;
        setNotice('검토를 기록하고 이 버전을 승인했습니다. 원장 반영은 다음 단계입니다.');
      }, 'approve');
      return;
    }
    if (!['create','revise','review','approve','post','request-changes','reject'].includes(action)) return;
    if (['request-changes', 'reject'].includes(action) && outcomeReason.trim().length < 8) { setNotice('보완 요청·반려 사유를 8자 이상 적어 주세요.', true); render(); return; }
    const sourceId = selectedSource || wrap?.querySelector('[data-bom-field="source"]')?.value;
    if (action === 'create' && !sourceId) { setNotice('접수할 실제 원본을 선택해 주세요.', true); render(); return; }
    if (action === 'create' && !['ods','csv'].includes(sources.find(source => source.source_id === sourceId)?.format)) { setNotice(errors.BOM_TABLE_FORMAT_NOT_SUPPORTED, true); render(); return; }
    if (action !== 'create' && !active) return;
    let path = '/api/mks2/bom/documents', payload;
    if (action === 'create') selectedSource = sourceId;
    if (action === 'create') payload = { source_id: sourceId, configuration_id: snapshot.summary.configuration_id, expected_ledger_version: currentLedger() };
    else {
      path += `/${encodeURIComponent(active.id)}/${action}`;
      payload = exact();
      if (action === 'revise') payload = { ...payload, expected_ledger_version: currentLedger(), review_note: reviewNote || '원문 전체 행과 미확인을 보존하고, 기초 원장의 시연 매핑 가정 5행만 조건부 적용 판단으로 저장합니다. 제조 기술검수 승인이 아닙니다.',
        rows: allRows().map(row => ({ row_id: row.row_id, ...decision(row) })) };
      if (action === 'review') payload = { ...payload, review_note: reviewerNote, acknowledge_unresolved: acknowledged };
      if (action === 'request-changes' || action === 'reject') payload = { ...payload, reason: outcomeReason.trim() };
    }
    await work(async current => {
      const result = await command(action, path, payload);
      if (!current()) return;
      active = result; resetDrafts();
      const index = documents.findIndex(doc => doc.id === active.id);
      if (index >= 0) documents[index] = active; else documents.push(active);
      await loadFocus(current);
      if (!current()) return;
      step = action === 'create' ? 2 : action === 'post' ? 7 : action === 'revise' || action === 'request-changes' ? 5 : 6;
      prepareSheet();
      setNotice({ create: '실제 원본을 접수했습니다. 전체 행과 원본 식별 차이를 검토해 주세요.', revise: '등록 초안을 새 버전으로 저장하고 승인을 요청했습니다. 다른 담당자의 검토·승인을 기다립니다. 이전 검토·승인은 해제됩니다.', review: '서버에 검토를 기록했습니다. 아직 승인·원장 반영 전입니다.', approve: '이 버전의 등록 내용을 승인했습니다. 원장 반영은 다음 단계입니다.', post: '원장에 등록했습니다. 같은 버전의 그래프 준비 후 최신 원장에서 소요량을 확인하세요.', 'request-changes': '보완 요청을 기록했습니다. 문서는 초안으로 돌아가고 기존 검토·승인은 해제됐습니다.', reject: '이 문서를 반려했습니다. 원장에는 반영되지 않습니다.' }[action]);
      scrollTarget = action === 'create' ? 2 : 5;
      if (action === 'create' && candidateStaged) setNotice(`실제 원본을 접수했습니다. 기초 원장의 시연 매핑 가정 ${count(candidateStaged)}행을 초안에 담았습니다. 확인 후 등록 초안을 저장해 승인을 요청하세요.`);
      if (action === 'post') onPosted(active);
    }, action);
  }
  function open(initialStep = 0, preferredId, preferredRow = null, expectedDocument = null, { source = false } = {}) {
    close(); focusBefore = document.activeElement; step = initialStep; sourceFirst = source;
    active = null; sources = []; documents = []; runner = null; selectedSource = ''; fileCheck = null; resetDrafts();
    wrap = document.createElement('div'); wrap.className = 'mk-drawer-wrap'; document.body.append(wrap);
    render();
    wrap.addEventListener('click', event => {
      if (event.target.closest('[data-close]')) { close(); return; }
      const stage = event.target.closest('[data-bom-step]');
      if (stage && !busy) { selectStep(Number(stage.dataset.bomStep)); return; }
      const source = event.target.closest('[data-bom-source]');
      if (source && !source.disabled && !busy) { selectedSource = source.dataset.bomSource; activate('create'); return; }
      const filter = event.target.closest('[data-bom-filter]');
      if (filter && !busy) { rowFilter = filter.dataset.bomFilter; scrollTarget = 2; render(); return; }
      const edit = event.target.closest('[data-bom-edit]');
      if (edit && !busy) { selectedRow = edit.dataset.bomEdit; step = 4; scrollTarget = 'editor'; render(); return; }
      const target = event.target.closest('[data-bom-action]');
      if (target && !target.disabled) activate(target.dataset.bomAction);
    });
    wrap.addEventListener('change', event => {
      const field = event.target.dataset.bomField;
      if (field === 'document' && !busy) { active = null; resetDrafts(); reload(event.target.value); }
      if (field === 'compare-baseline' && active && !busy) comparison.load(active, documents, event.target.value);
      if (field === 'review-preset') { reviewNote = event.target.value === 'preserve' ? '원문 전체 행과 미확인을 보존하고, 사용자가 선택한 행만 조건부 시연 적용 판단으로 저장합니다. 제조 기술검수 승인이 아닙니다.' : event.target.value === 'hold' ? '원문 전체 행을 보존하고 미확인 적용 판단과 식별 충돌은 보완 검토 대기로 유지합니다.' : reviewNote; dirty = drafts.size > 0 || reviewNote !== (content().review_note || ''); const note = wrap.querySelector('[data-bom-field="review-note"]'); if (note) note.value = reviewNote; }
      if (field === 'reviewer-preset' && event.target.value === 'source') { reviewerNote = '원문과 등록 판단 및 미확인 사항을 검토했습니다. 미해결 항목을 유지하며 원본 등록 승인과 제조 기술검수를 구분합니다.'; wrap.querySelector('[data-bom-field="reviewer-note"]').value = reviewerNote; }
      if (field === 'source' && !busy) selectedSource = event.target.value;
      if (field === 'row' && !busy) { selectedRow = event.target.value; render(); }
      if (event.target.closest('[data-bom-form="row"]')) syncRowChoices();
      if (field === 'acknowledge') { acknowledged = event.target.checked; render(); }
      if (field === 'file') { handleFile(event.target.files?.[0]); event.target.value = ''; }
    });
    for (const type of ['dragenter', 'dragover', 'dragleave', 'drop']) wrap.addEventListener(type, event => {
      const zone = event.target.closest?.('[data-bom-drop]');
      if (!zone) return;
      event.preventDefault();
      zone.classList.toggle('over', type === 'dragenter' || type === 'dragover');
      if (type === 'drop') handleFile(event.dataTransfer?.files?.[0]);
    });
    wrap.addEventListener('input', event => {
      if (event.target.dataset.bomField === 'review-note') { reviewNote = event.target.value; dirty = drafts.size > 0 || reviewNote !== (content().review_note || ''); }
      if (event.target.dataset.bomField === 'reviewer-note') reviewerNote = event.target.value;
      if (event.target.dataset.bomField === 'outcome-reason') outcomeReason = event.target.value;
    });
    wrap.addEventListener('submit', event => {
      if (event.target.dataset.bomForm !== 'row') return;
      event.preventDefault(); if (!editable() || busy) return;
      const form = new FormData(event.target), fields = Object.fromEntries(form), row = allRows().find(item => item.row_id === fields.row_id);
      if (fields.quantity_per_unit === '__custom__') fields.quantity_per_unit = fields.custom_quantity;
      if (fields.unit === '__custom__') fields.unit = fields.custom_unit;
      try {
        if (fields.application_status === 'APPLICABLE' && (snapshot.entities || []).filter(entity => entity.type === 'InternalItem' && entity.display_id === fields.item_id).length !== 1) throw Error('BOM_ITEM_SELECTION_AMBIGUOUS');
        drafts.set(fields.row_id, decisionFromChoices(row, fields));
      }
      catch (error) { setNotice(errorMessage(error), true); render(); return; }
      dirty = true; selectedRow = String(form.get('row_id')); setNotice('선택을 화면 초안에 담았습니다. 검수 초안 저장으로 서버에 반영해 주세요.'); render();
    });
    wrap.querySelector('.mk-drawer [data-close]').focus();
    reload(preferredId, preferredRow, expectedDocument);
  }
  document.addEventListener('keydown', event => {
    if (!wrap) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key === 'Tab') {
      const controls = [...wrap.querySelectorAll('button:not(:disabled),select:not(:disabled),textarea:not(:disabled),input:not(:disabled):not([type="hidden"]),summary,[href],[tabindex="0"]')].filter(element => element.getClientRects().length > 0);
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }, { signal: abort.signal });
  return { open, close, destroy() { close(); abort.abort(); pendingCommands.clear(); } };
}
