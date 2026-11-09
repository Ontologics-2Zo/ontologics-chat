// BOM presentation and explicit commands. All parsing, authority, approval and
// ledger effects belong to the server; local row edits remain unsaved drafts.
import { createProductReview } from './product.js';
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const count = value => Number.isInteger(value) ? value.toLocaleString('ko-KR') : '미확인';
const stages = ['원본 접수', '개정 식별', '추출·검증', '개정 차이', '품목 연결', '등록 초안', '검토·승인', '반영'];
const statuses = { DRAFT: '초안', REVIEWED: '검토 완료', APPROVED: '승인 완료 · 미반영', POSTED: '원장 반영' };
const applications = { UNRESOLVED: '미확인', APPLICABLE: '적용 가정', NOT_MOUNTED: '비실장', SETTING: '설정' };
const bases = { UNREVIEWED: '미검수', SOURCE_VERIFIED: '원문 대조', DEMO_ASSUMPTION: '시연 가정' };
const option = (value, label, current) => `<option value="${esc(value)}"${value === current ? ' selected' : ''}>${esc(label)}</option>`;
const button = (label, action, disabled = false, primary = false) => `<button type="button" class="mk-btn${primary ? ' primary' : ''}" data-bom-action="${action}"${disabled ? ' disabled' : ''}>${esc(label)}</button>`;
const errors = {
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

export function createBomDrawer({ snapshot, api, getIdentity = () => null, onPosted = () => {}, onRefresh = () => {} }) {
  let wrap = null, focusBefore = null, step = 0, active = null, sources = [], documents = [], runner = null;
  let busy = false, operation = '', generation = 0, notice = '', failure = false, selectedRow = null;
  let selectedSource = '', scrollTarget = null;
  let drafts = new Map(), dirty = false, reviewNote = '', reviewerNote = '', acknowledged = false;
  // Text still being typed is separate from explicitly staged row decisions.
  let rowInputs = new Map(), discardRowInputs = false;
  const product = createProductReview({ api, onChange: () => {
    const slot = wrap?.querySelector('[data-product-review-slot]');
    if (slot) slot.innerHTML = product.render();
  } });
  const abort = new AbortController(), pendingCommands = new Map();
  const permitted = action => (getIdentity()?.permitted_actions || []).includes(action);
  const isAuthor = () => active?.created_by === getIdentity()?.actor_id;
  const editable = () => active && active.status !== 'POSTED' && isAuthor() && permitted('document.update');
  const content = () => active?.content || {};
  const allRows = () => content().rows || [];
  const decision = row => drafts.get(row.row_id) || row.decision || {};
  const exact = () => ({ expected_version: active.version, payload_hash: active.payload_hash });
  const currentLedger = () => snapshot.meta.current_ledger_version || snapshot.meta.ledger_version;
  const setNotice = (text, isError = false) => { notice = text; failure = isError; };
  function resetDrafts() {
    product.reset();
    drafts = new Map(); dirty = false; selectedRow = null;
    rowInputs = new Map(); discardRowInputs = true;
    reviewNote = content().review_note || ''; reviewerNote = ''; acknowledged = false;
  }
  function close() {
    generation++;
    product.reset();
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
    const id = preferred || active?.id || rows[0]?.id;
    if (id) doc = await api(`/api/mks2/bom/documents/${encodeURIComponent(id)}`);
    return { rows, doc };
  }
  async function reload(preferred, rowId = null) {
    await work(async current => {
      const [catalog, stored] = await Promise.all([api('/api/mks2/bom/sources'), fetchDocuments(preferred)]);
      if (!current()) return;
      sources = catalog.sources || []; runner = catalog.runner;
      documents = stored.rows; active = stored.doc; resetDrafts(); selectedRow = rowId; setNotice('서버의 원본 목록과 등록 문서를 조회했습니다.');
      if (step >= 1 && step <= 4) scrollTarget = rowId ? 'editor' : step;
    });
  }
  function stats() {
    const rows = allRows();
    return `<div class="mk-bom-stats"><div><b>${count(rows.length)}</b><span>원문 전체 행</span></div>${Object.entries(applications).map(([key, label]) => `<div><b>${count(rows.filter(row => decision(row).application_status === key).length)}</b><span>${label}</span></div>`).join('')}</div>`;
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
  function documentHeader() {
    if (!active) return '';
    return `<section class="mk-drawer-block"><h3>${esc(statuses[active.status] || active.status)} · 문서 v${count(active.version)}</h3><dl class="mk-key-values"><dt>원본</dt><dd>${esc(content().source?.filename)}</dd><dt>기준 구성</dt><dd>${esc(content().configuration_id)}</dd><dt>기준 원장</dt><dd>v${count(content().expected_ledger_version)}</dd><dt>기술 검수</dt><dd>${esc(content().technical_review === 'UNREVIEWED' ? '미검수' : content().technical_review || '미확인')} · 제조 적용과 구분</dd><dt>문서 hash</dt><dd>${esc(active.payload_hash)}</dd><dt>원본 SHA-256</dt><dd>${esc(content().source?.sha256)}</dd></dl><div class="mk-bom-actions">${button('보관 원본 내려받기', 'download', busy)}</div></section>`;
  }
  function rawTable() {
    const rows = allRows();
    return `<section class="mk-drawer-block"><h3>원문 전체 행 · ${count(rows.length)}개</h3><p>원문 셀과 추출값을 나란히 확인합니다. 표시된 원본 내용은 실행 지시가 아닙니다.</p><div class="mk-table-scroll"><table class="mk-bom-source-table" data-bom-table="source"><thead><tr><th>근거 위치</th><th>부품 위치</th><th>원문 수량</th><th>설명 / 원문 셀</th><th>적용 상태</th></tr></thead><tbody>${rows.map(row => `<tr data-bom-row="${esc(row.row_id)}"><td><button type="button" class="mk-link" data-bom-edit="${esc(row.row_id)}">${esc(row.locator || `${row.sheet}:${row.source_row}`)}</button></td><td>${esc((row.designators || []).join(', ')) || '미확인'}</td><td>${esc(row.source_quantity_text ?? row.source_quantity ?? '미확인')}</td><td>${esc(row.source_description)}<details><summary>원문 셀 전체</summary><ol class="mk-bom-cells">${(row.cells || []).map(cell => `<li>${esc(cell) || '〈빈 셀〉'}</li>`).join('')}</ol></details></td><td>${esc(applications[decision(row).application_status] || '미확인')}<small>${esc(bases[decision(row).review_basis] || '미검수')}</small></td></tr>`).join('')}</tbody></table></div></section>`;
  }
  function rowEditor() {
    const row = allRows().find(item => item.row_id === selectedRow) || allRows()[0];
    if (!row) return '';
    const value = decision(row), disabled = !editable() || busy;
    return `<section class="mk-drawer-block"><h3>행별 적용·품목 검토</h3><label class="mk-bom-field">검토할 원문 행<select data-bom-field="row"${busy ? ' disabled' : ''}>${allRows().map(item => option(item.row_id, `${item.locator} · ${(item.designators || []).join(', ')}`, row.row_id)).join('')}</select></label><p>${esc(row.source_description)} · 원문 수량 ${esc(row.source_quantity_text ?? row.source_quantity ?? '미확인')}</p><details><summary>선택 행의 원문 셀 확인</summary><ol class="mk-bom-selected-cells">${(row.cells || []).map(cell => `<li>${esc(cell) || '〈빈 셀〉'}</li>`).join('')}</ol></details><form data-bom-form="row" class="mk-bom-form"><input type="hidden" name="row_id" value="${esc(row.row_id)}"><div class="mk-bom-fields"><label>적용 상태<select name="application_status"${disabled ? ' disabled' : ''}>${Object.entries(applications).map(([key,label]) => option(key,label,value.application_status || 'UNRESOLVED')).join('')}</select></label><label>검토 근거<select name="review_basis"${disabled ? ' disabled' : ''}>${Object.entries(bases).filter(([key]) => key !== 'SOURCE_VERIFIED').map(([key,label]) => option(key,label,value.review_basis || 'UNREVIEWED')).join('')}</select></label><label>대당 수량<input name="quantity_per_unit" inputmode="decimal" value="${esc(value.quantity_per_unit ?? '')}" placeholder="미확인"${disabled ? ' disabled' : ''}></label><label>단위<input name="unit" maxlength="24" value="${esc(value.unit ?? '')}" placeholder="검수한 단위"${disabled ? ' disabled' : ''}></label><label>사내 품목 ID<input name="item_id" list="bom-ledger-items" maxlength="200" value="${esc(value.item_id ?? '')}" placeholder="미연결"${disabled ? ' disabled' : ''}></label></div><datalist id="bom-ledger-items">${(snapshot.entities || []).filter(entity => entity.type === 'InternalItem').map(entity => `<option value="${esc(entity.display_id)}">${esc(entity.label || entity.display_id)}</option>`).join('')}</datalist><label class="mk-bom-field">판단 근거<textarea name="evidence_note" maxlength="2000" rows="3"${disabled ? ' disabled' : ''}>${esc(value.evidence_note || '')}</textarea></label><button class="mk-btn" type="submit"${disabled ? ' disabled' : ''}>행 검토를 초안에 담기</button></form><p class="mk-preview-notice">원문 대조는 제조 기술 승인 근거가 아닙니다. 행 적용은 근거를 적은 시연 가정으로만 등록할 수 있고 기술 검수는 미검수로 남습니다. 품목명 유사도만으로 연결하지 않으며, 적용 가정의 부족량은 제조 가능 확정이 아닙니다. 행 입력은 아래 ‘검수 초안 저장’을 눌러야 서버에 저장됩니다.</p>${!editable() ? '<p>행 검토 수정은 반영 전 문서의 작성 계정에서 할 수 있습니다.</p>' : ''}</section>`;
  }
  function mappingTable() {
    const rows = [...allRows()].sort((left, right) => Number(!!decision(right).item_id) - Number(!!decision(left).item_id));
    const linked = rows.filter(row => decision(row).item_id).length;
    const itemNames = new Map((snapshot.entities || []).filter(entity => entity.type === 'InternalItem').map(entity => [entity.display_id, entity.label || entity.display_id]));
    return `<section class="mk-drawer-block"><h3>사내 품목 연결</h3><p>${count(linked)}행 연결 · ${count(rows.length - linked)}행 미연결${dirty ? ' · 미저장 검토 포함' : ''}</p><div class="mk-table-scroll mk-bom-review-table"><table class="mk-bom-source-table" data-bom-table="mapping"><thead><tr><th>BOM 행</th><th>부품 위치</th><th>원문 값</th><th>사내 품목</th><th>1대당 / 단위</th><th>검토 상태</th></tr></thead><tbody>${rows.map(row => {
      const value = decision(row);
      return `<tr><td><button type="button" class="mk-link" data-bom-edit="${esc(row.row_id)}">${esc(row.source_row)}</button></td><td>${esc((row.designators || []).join(', ') || '미확인')}</td><td>${esc(row.source_description)}</td><td>${esc(value.item_id || '미연결')}<small>${esc(itemNames.get(value.item_id) || '')}</small></td><td>${esc(value.quantity_per_unit ?? '미확인')} / ${esc(value.unit || '미확인')}</td><td>${esc(applications[value.application_status] || '미확인')}<small>${esc(bases[value.review_basis] || '미검수')}</small></td></tr>`;
    }).join('')}</tbody></table></div><p>연결된 행을 먼저 표시합니다. 품목 연결은 기술 적합성 승인이 아니며, 미연결 행도 원문 그대로 보존합니다.</p></section>`;
  }
  function reviewPage() {
    return `<div class="mk-bom-review-page"><div data-bom-section="1"><h3>개정 식별</h3>${documentHeader()}<p class="mk-small-note">원문 시트: ${esc([...new Set(allRows().map(row => row.sheet))].join(' · '))}</p></div><div data-bom-section="2"><h3>추출·검증</h3>${stats()}${conflicts()}<div class="mk-bom-extracted">${rawTable()}</div></div><section class="mk-drawer-block" data-bom-section="3"><h3>개정 차이</h3><p>이전 개정 대비 자동 비교는 아직 연결되지 않았습니다. 현재 원본의 hash·전체 행과 검수 내용을 확인할 수 있습니다.</p><p>원본마다 별도 문서로 보관하며, 행별 판단 수정 시 새 문서 버전이 생성됩니다. 이전 버전의 승인을 새 버전에 적용하지 않습니다.</p></section><div data-bom-section="4">${mappingTable()}<div data-bom-section="editor">${rowEditor()}<div data-product-review-slot>${product.render()}</div></div>${draftForm()}<div class="mk-bom-actions"><button type="button" class="mk-btn" data-bom-action="approval-view"${busy ? ' disabled' : ''}>검토·승인 단계 보기</button></div></div></div>`;
  }
  function draftForm() {
    return `<section class="mk-drawer-block"><h3>검수 초안 ${dirty ? '· 미저장 변경 있음' : '· 서버 저장본'}</h3><label class="mk-bom-field">등록·검수 의견<textarea data-bom-field="review-note" rows="3" maxlength="3000"${!editable() || busy ? ' disabled' : ''}>${esc(reviewNote)}</textarea></label><p>저장하면 새 문서 버전이 생기고 기존 검토·승인은 해제됩니다. 미확인 행은 삭제하거나 0으로 채우지 않습니다.</p>${button('검수 초안 저장', 'revise', !editable() || busy, true)}</section>`;
  }
  function approvalForm() {
    const other = active && !isAuthor();
    return `<section class="mk-drawer-block"><h3>정확 버전 검토·승인</h3><p>대상은 위 문서 v${count(active.version)}와 hash입니다. 작성자와 다른 계정이 원문·모든 행·미해결 사항을 확인합니다.</p><label class="mk-bom-field">검토 의견<textarea data-bom-field="reviewer-note" rows="3" maxlength="3000"${busy ? ' disabled' : ''}>${esc(reviewerNote)}</textarea></label><label class="mk-bom-ack"><input type="checkbox" data-bom-field="acknowledge"${acknowledged ? ' checked' : ''}${busy ? ' disabled' : ''}>미확인 행과 원본 식별 충돌을 확인했으며, 원본 등록과 제조 기술 승인을 구분합니다.</label><div class="mk-bom-actions">${button('검토 기록', 'review', busy || dirty || !other || !permitted('document.review') || active.status !== 'DRAFT')}${button('이 버전 승인', 'approve', busy || dirty || !other || !permitted('document.approve') || active.status !== 'REVIEWED', true)}</div>${isAuthor() ? '<p>작성 계정으로는 자기 검토·승인을 할 수 없습니다. 상단의 다른 개발 계정으로 전환한 뒤 이 문서를 다시 열어 주세요.</p>' : ''}${dirty ? '<p>미저장 행 변경을 먼저 저장해 주세요.</p>' : ''}</section>`;
  }
  function postedSection() {
    return `<section class="mk-drawer-block"><h3>승인된 등록 원장 반영</h3><p>원본과 승인된 행별 판단을 새 원장 버전으로 기록합니다. 기존 재고·생산·출하·도착 수량을 변경하지 않습니다.</p><dl class="mk-key-values"><dt>상태</dt><dd>${esc(statuses[active.status] || active.status)}</dd><dt>반영 원장</dt><dd>${active.ledger_version ? `v${count(active.ledger_version)}` : '미반영'}</dd><dt>기술 검수</dt><dd>${esc(content().technical_review === 'UNREVIEWED' ? '미검수' : content().technical_review || '미확인')}</dd></dl><div class="mk-bom-actions">${button('승인 버전 원장 반영', 'post', busy || dirty || active.status !== 'APPROVED' || !permitted('document.post'), true)}${button('최신 원장·소요량 조회', 'refresh', busy || active.status !== 'POSTED')}</div><p>반영 이후 그래프가 같은 버전으로 준비되면 최신 원장을 조회하세요. 누락된 매핑·단위·재고는 소요량과 부족량에서 미확인으로 표시합니다.</p></section>`;
  }
  function stageBody() {
    if (operation === 'create') return `<section class="mk-drawer-block mk-bom-processing"><h3>추출·검증 중</h3><p>${esc(sources.find(source => source.source_id === selectedSource)?.filename || '선택한 원본')}</p><div class="mk-bom-progress" role="progressbar" aria-label="BOM 원본 추출·검증 진행 중"><span></span></div><p>원본 식별 · 시트와 행 추출 · 수량과 부품 위치 확인</p><p class="mk-small-note">서버 처리 중입니다. 완료되면 검증 결과와 품목 연결을 함께 보여드립니다.</p></section>`;
    if (step === 0) return `<section class="mk-drop"><b>서버 보관 BOM 원본 접수</b><span>등록된 실제 원본을 제한된 파일 처리기로 읽고 전체 행과 hash를 보관합니다.</span><label class="mk-bom-field">원본 선택<select data-bom-field="source"${busy ? ' disabled' : ''}><option value="">원본을 선택하세요</option>${sources.map(source => option(source.source_id, `${source.filename} · ${source.format} · ${count(source.bytes)} bytes`, selectedSource)).join('')}</select></label>${button('선택 원본 접수', 'create', busy || runner?.ready !== true || !permitted('document.create'), true)}<span>${runner?.ready === true ? '제한된 파일 처리기 준비됨' : runner?.code === 'RUNNER_ENFORCEMENT_UNAVAILABLE' ? '이 실행환경의 파일 처리 자원 제한 미지원 · 원본 접수 차단' : '제한된 파일 처리기 준비 필요'}</span></section>${active ? documentHeader() : ''}`;
    if (!active) return '<section class="mk-drawer-block"><h3>등록 문서 없음</h3><p>현재 원장에는 이 단계의 실행 결과가 없습니다. 원본 접수에서 실제 BOM을 선택하거나 기존 문서를 다시 조회하세요.</p></section>';
    if (step >= 1 && step <= 4) return reviewPage();
    if (step === 5) return `${documentHeader()}${stats()}${draftForm()}${rawTable()}`;
    if (step === 6) return `${documentHeader()}${conflicts()}${stats()}${approvalForm()}${rawTable()}`;
    return `${documentHeader()}${postedSection()}`;
  }
  function render() {
    if (!wrap) return;
    const oldForm = wrap.querySelector('[data-bom-form="row"]');
    if (oldForm && !discardRowInputs && editable()) {
      const values = Object.fromEntries(new FormData(oldForm));
      if (values.row_id) rowInputs.set(values.row_id, { ...rowInputs.get(values.row_id), ...values });
    }
    discardRowInputs = false;
    if (step >= 1 && step <= 4 && active && !busy) product.load(snapshot, active, selectedRow || allRows()[0]?.row_id);
    const restoreFocus = wrap.contains(document.activeElement);
    const previousScroll = wrap.querySelector('.mk-drawer-body')?.scrollTop || 0;
    wrap.innerHTML = `<div class="mk-drawer-scrim" data-close></div><section class="mk-drawer" role="dialog" aria-modal="true" aria-labelledby="mk-drawer-title" aria-busy="${busy}"><header><div><div class="mk-drawer-eyebrow">제품 구성 · BOM 원본 등록</div><h2 id="mk-drawer-title">${esc(snapshot.summary.configuration_id || 'BOM 등록')}</h2></div><button type="button" class="mk-btn" data-close>닫기</button></header><nav class="mk-drawer-steps" aria-label="BOM 처리 단계">${stages.map((label,index) => `<button type="button" data-bom-step="${index}" aria-current="${index === (operation === 'create' ? 2 : step) ? 'step' : 'false'}"${busy ? ' disabled' : ''}>${label}</button>`).join('')}</nav><div class="mk-drawer-body"><div class="mk-bom-toolbar"><label class="mk-bom-field">등록 문서<select data-bom-field="document"${busy ? ' disabled' : ''}><option value="">등록 문서를 선택하세요</option>${documents.map(doc => option(doc.id, `${doc.content?.source?.filename || doc.id} · v${doc.version} · ${statuses[doc.status] || doc.status}`, active?.id)).join('')}</select></label>${button('다시 조회', 'reload', busy)}</div><p class="mk-bom-notice${failure ? ' error' : ''}" role="status" aria-live="polite">${esc(notice)}</p>${stageBody()}<p class="mk-preview-notice">현재 회사·계정의 서버 문서만 조회합니다. 단계 선택은 상태를 바꾸지 않으며 검토·승인·반영은 각각 명시적으로 실행합니다.</p></div></section>`;
    const form = wrap.querySelector('[data-bom-form="row"]');
    const inputValues = rowInputs.get(form?.elements.namedItem('row_id')?.value);
    if (form && inputValues && editable()) for (const [name, value] of Object.entries(inputValues)) {
      const field = form.elements.namedItem(name);
      if (field) field.value = value;
    }
    if (restoreFocus) (wrap.querySelector(`[data-bom-step="${step}"]:not(:disabled)`) || wrap.querySelector('.mk-drawer [data-close]')).focus();
    const target = scrollTarget === null ? null : wrap.querySelector(`[data-bom-section="${scrollTarget}"]`);
    if (target) target.scrollIntoView({ block: 'start' });
    else wrap.querySelector('.mk-drawer-body').scrollTop = scrollTarget === null ? previousScroll : 0;
    scrollTarget = null;
  }
  function selectStep(next) {
    // Sections 1–4 share one page. Moving between them must not replace a
    // partially typed row form before the author explicitly stages the edit.
    if (step >= 1 && step <= 4 && next >= 1 && next <= 4 && wrap.querySelector('.mk-bom-review-page')) {
      step = next;
      for (const control of wrap.querySelectorAll('[data-bom-step]')) control.setAttribute('aria-current', Number(control.dataset.bomStep) === step ? 'step' : 'false');
      wrap.querySelector(`[data-bom-section="${step}"]`)?.scrollIntoView({ block: 'start' });
      return;
    }
    step = next; scrollTarget = next; render();
  }
  async function activate(action) {
    if (action === 'approval-view' && !busy) { selectStep(6); return; }
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
    if (!['create','revise','review','approve','post'].includes(action)) return;
    const sourceId = wrap?.querySelector('[data-bom-field="source"]')?.value;
    if (action === 'create' && !sourceId) { setNotice('접수할 실제 원본을 선택해 주세요.', true); render(); return; }
    if (action !== 'create' && !active) return;
    let path = '/api/mks2/bom/documents', payload;
    if (action === 'create') selectedSource = sourceId;
    if (action === 'create') payload = { source_id: sourceId, configuration_id: snapshot.summary.configuration_id, expected_ledger_version: currentLedger() };
    else {
      path += `/${encodeURIComponent(active.id)}/${action}`;
      payload = exact();
      if (action === 'revise') payload = { ...payload, expected_ledger_version: currentLedger(), review_note: reviewNote,
        rows: allRows().map(row => ({ row_id: row.row_id, ...decision(row) })) };
      if (action === 'review') payload = { ...payload, review_note: reviewerNote, acknowledge_unresolved: acknowledged };
    }
    await work(async current => {
      const result = await command(action, path, payload);
      if (!current()) return;
      active = result; resetDrafts();
      const index = documents.findIndex(doc => doc.id === active.id);
      if (index >= 0) documents[index] = active; else documents.push(active);
      step = action === 'create' ? 4 : action === 'post' ? 7 : action === 'revise' ? 5 : 6;
      setNotice({ create: '실제 원본을 접수했습니다. 전체 행과 원본 식별 차이를 검토해 주세요.', revise: '새 버전의 검수 초안을 저장했습니다. 이전 검토·승인은 해제됩니다.', review: '서버에 검토를 기록했습니다. 아직 승인·원장 반영 전입니다.', approve: '이 버전의 등록 내용을 승인했습니다. 원장 반영은 다음 단계입니다.', post: '원장에 등록했습니다. 같은 버전의 그래프 준비 후 최신 원장에서 소요량을 확인하세요.' }[action]);
      scrollTarget = step;
      if (action === 'post') onPosted(active);
    }, action);
  }
  function open(initialStep = 0, preferredId, preferredRow = null) {
    close(); focusBefore = document.activeElement; step = initialStep;
    active = null; sources = []; documents = []; runner = null; selectedSource = ''; resetDrafts();
    wrap = document.createElement('div'); wrap.className = 'mk-drawer-wrap'; document.body.append(wrap);
    render();
    wrap.addEventListener('click', event => {
      if (event.target.closest('[data-close]')) { close(); return; }
      const stage = event.target.closest('[data-bom-step]');
      if (stage && !busy) { selectStep(Number(stage.dataset.bomStep)); return; }
      const edit = event.target.closest('[data-bom-edit]');
      if (edit && !busy) { selectedRow = edit.dataset.bomEdit; step = 4; scrollTarget = 'editor'; render(); return; }
      const target = event.target.closest('[data-bom-action]');
      if (target && !target.disabled) activate(target.dataset.bomAction);
    });
    wrap.addEventListener('change', event => {
      const field = event.target.dataset.bomField;
      if (field === 'document' && !busy) { active = null; resetDrafts(); reload(event.target.value); }
      if (field === 'source' && !busy) selectedSource = event.target.value;
      if (field === 'row' && !busy) { selectedRow = event.target.value; render(); }
      if (field === 'acknowledge') acknowledged = event.target.checked;
    });
    wrap.addEventListener('input', event => {
      if (event.target.dataset.bomField === 'review-note') { reviewNote = event.target.value; dirty = drafts.size > 0 || reviewNote !== (content().review_note || ''); }
      if (event.target.dataset.bomField === 'reviewer-note') reviewerNote = event.target.value;
    });
    wrap.addEventListener('submit', event => {
      if (event.target.dataset.bomForm !== 'row') return;
      event.preventDefault(); if (!editable() || busy) return;
      const form = new FormData(event.target), nullable = name => String(form.get(name) || '').trim() || null;
      drafts.set(String(form.get('row_id')), { application_status: form.get('application_status'), review_basis: form.get('review_basis'),
        quantity_per_unit: nullable('quantity_per_unit'), unit: nullable('unit'), item_id: nullable('item_id'), evidence_note: String(form.get('evidence_note') || '').trim() });
      dirty = true; selectedRow = String(form.get('row_id')); setNotice('행 검토를 화면 초안에 담았습니다. 검수 초안 저장으로 서버에 반영해 주세요.'); render();
    });
    wrap.querySelector('.mk-drawer [data-close]').focus();
    reload(preferredId, preferredRow);
  }
  document.addEventListener('keydown', event => {
    if (!wrap) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key === 'Tab') {
      const controls = [...wrap.querySelectorAll('button:not(:disabled),select:not(:disabled),textarea:not(:disabled),input:not(:disabled):not([type="hidden"]),summary,[href],[tabindex="0"]')];
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }, { signal: abort.signal });
  return { open, close, destroy() { close(); abort.abort(); pendingCommands.clear(); } };
}
