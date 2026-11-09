// Read-only Product review. Source descriptions are escaped data, never commands.
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[char]));
const labels = {
  APPLICABLE: '적용 판단 있음', UNRESOLVED: '적용 미확인', NOT_MOUNTED: '비실장 판단', SETTING: '설정 판단',
  DEMO_ASSUMPTION: '시연 적용 가정', UNREVIEWED: '미검수', SOURCE_VERIFIED: '원문 대조',
  CURRENT_CONDITIONAL_DECISION: '현재 적용 판단의 후보', EXACT_PART_TOKEN: '부품 식별 문자열 일치',
  LEXICAL_CANDIDATE: '명칭·규격 일부 일치', HISTORICAL_UNREVIEWED_CANDIDATE: '기존 미검수 후보',
  LOCAL_RETRIEVAL_CANDIDATE: '로컬 근거 검색 후보', synthetic_operation: '합성 기초 원장', actual_bom: '실제 BOM 원문',
  TECHNICAL_EQUIVALENCE_UNVERIFIED: '실물·기술 동일성이 확인되지 않았습니다.',
  SOURCE_UNIT_UNCONFIRMED: '원문에서 적용 단위를 확인하지 못했습니다.', APPLICATION_REVIEW_REQUIRED: '실장 여부·대당 수량·단위의 적용 검수가 필요합니다.',
  SOURCE_QUANTITY_UNCONFIRMED: '원문 수량을 확정할 수 없습니다.', SOURCE_DESIGNATOR_UNCONFIRMED: '부품 위치가 미확인입니다.',
  NO_ITEM_CANDIDATE: '현재 품목 목록에서 후보를 찾지 못했습니다.', AMBIGUOUS_ITEM_CANDIDATES: '후보가 여러 개이므로 품목을 확정할 수 없습니다.',
  ITEM_CODE_SCOPE_AMBIGUOUS: '같은 표시 코드의 품목 범위를 대조해야 합니다.', SOURCE_CONFLICT_REQUIRES_REVIEW: '원본의 식별·구성 충돌을 확인해야 합니다.',
  CONFIGURATION_BINDING_UNVERIFIED: '이 원문이 적용될 제품 구성이 기술 검수되지 않았습니다.',
  SOURCE_FILENAME_SHEET_CONFIGURATION_CONFLICT: '원본 파일명과 시트의 구성 식별이 다릅니다.',
  SOURCE_ROW_REQUIRES_REVIEW: '원문에 수량·위치 미확인 행이 있습니다.', DUPLICATE_SOURCE_DESIGNATOR: '같은 시트에 부품 위치가 중복됩니다.',
  HIT: '근거 검색됨', NO_HIT: '일치 근거 없음', INDEX_INCOMPLETE: '검색 색인 일부 미완료', INDEX_ERROR: '근거 검색 오류',
  PROFILE_NOT_IMPLEMENTED: '요청 검색 방식 미지원', NOT_CONFIGURED: '근거 검색 연결 대기', NO_QUERY: '검색할 명칭·위치 없음',
  NOT_REQUESTED_FOR_SUMMARY: '행 선택 후 근거 검색',
};
const label = value => labels[value] || value || '미확인';
const quantity = value => value === null || value === undefined || value === '' ? '미확인' : String(value);

export function renderProductRow(row, response, { embedded = false } = {}) {
  const source = row.source || {}, decision = row.current_decision || {}, candidates = row.candidates || [];
  const evidence = row.evidence_refs || [];
  return `${embedded ? '' : `<section class="mk-drawer-block"><h3>추출된 원문</h3><p>${esc(source.source_description || '명칭 미확인')}</p>
    <p class="mk-small-note">${esc(source.locator)} · 부품 위치 ${esc((source.designators || []).join(', ') || '미확인')}</p>
    <dl><dt>원문 수량</dt><dd>${esc(quantity(source.source_quantity))}</dd><dt>원문 단위</dt><dd>미확인</dd></dl>
    <button type="button" class="mk-btn" data-product-source="${esc(row.row_id)}">원문 행과 현재 등록 내용 보기</button></section>
    <section class="mk-drawer-block"><h3>현재 등록된 판단</h3><p>${esc(label(decision.application_status))} · ${esc(label(decision.review_basis))}</p>
    <p>품목 ${esc(decision.item_id || '미연결')} · 대당 ${esc(quantity(decision.quantity_per_unit))} · 단위 ${esc(decision.unit || '미확인')}</p>
    <p class="mk-small-note">${esc(decision.evidence_note || '등록된 추가 검토 근거 없음')}</p>
    <p class="mk-small-note">이 조회는 현재 판단을 바꾸거나 기술 검수를 승인하지 않습니다.</p></section>`}
    <section class="mk-drawer-block"><h3>품목 후보 ${esc(row.candidate_count ?? candidates.length)}개</h3>
    <p class="mk-small-note">명칭·규격의 일치와 검색 결과는 후보 근거입니다. 실물 동일성이나 대체 허용을 확정하지 않습니다.</p>
    ${candidates.map(candidate => `<article class="mk-drawer-block" data-product-candidate="${esc(candidate.item_entity_id)}">
      <h4>${esc(candidate.display_name)} <small>${esc(candidate.item_id)}</small></h4><p>${esc(label(candidate.origin))} · 미검수 후보</p>
      <p>${esc((candidate.match_methods || []).map(label).join(' · '))}</p>
      <ul>${(candidate.evidence_refs || []).map(ref => `<li>${esc(ref.locator || '품목 원장 기록')} <small>${esc(ref.source_sha256?.slice(0, 12) || ref.content_hash?.slice(0, 12) || 'hash 미확인')}</small></li>`).join('')}</ul>
      ${embedded ? '' : `<button type="button" class="mk-btn" data-product-item="${esc(candidate.item_entity_id)}">품목 근거 보기</button>`}</article>`).join('') || '<p>후보 없음. 품목 기준정보와 원문 식별 근거를 보완해 주세요.</p>'}
    ${row.candidates_truncated ? '<p class="mk-small-note">표시 한도를 넘는 후보가 더 있습니다. 첫 후보를 확정 품목으로 취급하지 않습니다.</p>' : ''}</section>
    <section class="mk-drawer-block mk-bom-warning"><h3>확인할 사항</h3><ul>${(row.issues || []).map(issue => `<li>${esc(label(issue))}</li>`).join('')}</ul>
    ${!embedded && (response.source_conflicts || []).length ? `<ul>${response.source_conflicts.map(issue => `<li>${esc(label(issue))}</li>`).join('')}</ul>` : ''}</section>
    <details class="mk-drawer-block"><summary>근거와 조회 기준</summary><p>${esc(label(row.retrieval?.status))}</p>
    <p>원장 v${esc(response.meta?.ledger_version)} / BOM 문서 v${esc(response.bom_version)} · 기존 파싱 결과 재사용</p>
    <p class="mk-small-note">로컬 규칙·검색으로 후보를 제시했습니다. 실제 추출 모델 호출과 자동 적용은 없습니다.</p>
    <ul>${evidence.map(ref => `<li>${esc(ref.locator)} · ${esc(ref.source_sha256?.slice(0, 12) || 'hash 미확인')}</li>`).join('')}</ul></details>`;
}

// Embedded in the existing BOM "품목 연결" step. No navigation or write action.
export function createProductReview({ api, onChange = () => {} }) {
  let key = null, selected = null, result = null, busy = false, failure = '', generation = 0;
  function reset() { generation++; key = null; selected = null; result = null; busy = false; failure = ''; }
  async function load(snapshot, document, rowId) {
    if (!document || !rowId) { reset(); return; }
    const request = { ledger_version: snapshot.meta.ledger_version, bom_id: document.id,
      bom_version: document.version, payload_hash: document.payload_hash, row_id: rowId, candidate_limit: 3 };
    const nextKey = JSON.stringify(request);
    if (key === nextKey) return;
    key = nextKey; selected = rowId; busy = true; failure = ''; result = null; const current = ++generation;
    try {
      const response = await api('/api/mks2/product/review', request);
      if (current !== generation) return;
      if (response.meta?.ledger_version !== request.ledger_version || response.bom_id !== request.bom_id
          || response.bom_version !== request.bom_version || response.payload_hash !== request.payload_hash
          || response.ledger_mutations !== 0 || response.model_calls !== 0) throw new Error('PRODUCT_RESULT_MISMATCH');
      result = response;
    } catch (error) {
      if (current !== generation) return;
      failure = error.status === 403 ? '현재 계정에는 후보 근거 조회 권한이 없습니다.'
        : error.status === 409 ? '원장 또는 문서 버전이 바뀌었습니다. 기존 다시 조회 버튼으로 문서를 확인해 주세요.'
          : '후보 근거를 조회하지 못했습니다. 기존 다시 조회 버튼으로 다시 확인할 수 있습니다.';
    } finally {
      if (current === generation) { busy = false; onChange(); }
    }
  }
  function render() {
    if (busy) return '<p class="mk-bom-notice" role="status" aria-live="polite">선택한 원문 행의 품목 후보와 근거를 조회하고 있습니다.</p>';
    if (failure) return `<p class="mk-bom-notice error" role="status">${esc(failure)}</p>`;
    const row = result?.rows?.find(value => value.row_id === selected);
    return row ? `<div data-product-inline><p class="mk-small-note">후보는 서버에 저장된 문서 기준입니다. 위 검수 입력에 자동 적용하지 않습니다.</p>${renderProductRow(row, result, { embedded: true })}</div>` : '';
  }
  return { load, render, reset, candidates() {
    return result?.rows?.find(value => value.row_id === selected)?.candidates || [];
  } };
}
