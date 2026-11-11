// Server query presentation only. No facts, quantities or answers originate here.
const text = value => typeof value === 'string' ? value : '';
const node = (tag, className = '', value) => {
  const result = document.createElement(tag);
  result.className = className;
  if (value !== undefined) result.textContent = String(value);
  return result;
};
const action = (label, callback, className = 'btn sm') => {
  const result = node('button', className, label);
  result.type = 'button'; result.addEventListener('click', callback); return result;
};
const statuses = {
  ANSWERED: ['확인된 범위의 답변', 'ok'], PARTIAL: ['일부 확인', 'warn'],
  UNKNOWN: ['판단 보류', 'mute'], NEEDS_CLARIFICATION: ['질문 확인 필요', 'warn'],
};
const origins = {
  synthetic_opening_balance: ['합성 초기 원장', 'synthetic'], synthetic: ['합성 원장', 'synthetic'],
  actual_bom: ['실제 원문', 'source'], actual_source: ['실제 원문', 'source'],
  conditional_demo: ['시연 가정', 'assumption'], demo_assumption: ['시연 가정', 'assumption'],
  user_assumption: ['질문에서 지정한 가정', 'assumption'],
  proposed_demo_assumption: ['미적용 시연 제안', 'unknown'],
  unreviewed: ['미검토', 'unknown'], unknown: ['미확인', 'unknown'],
  development_workflow: ['개발 원장 기록', 'ledger'],
  reviewed_application: ['검토된 적용 판단', 'ledger'],
  mixed_sources: ['출처별 근거', 'mixed'],
};
function originBadge(value) {
  const raw = text(value);
  if (!raw) return null;
  const [label, kind] = Object.hasOwn(origins, raw.toLowerCase()) ? origins[raw.toLowerCase()] : [raw, 'other'];
  return node('span', `query-origin query-origin-${kind}`, label);
}
function appendOrigin(container, value) {
  const badge = originBadge(value);
  if (badge) container.append(badge);
}
function statusBadge(value) {
  const raw = text(value);
  if (!raw) return null;
  const [label, kind] = Object.hasOwn(statuses, raw) ? statuses[raw] : [raw, 'mute'];
  return node('span', `pill ${kind}`, label);
}
function sectionCard(section) {
  const card = node('section', 'query-section');
  card.dataset.sectionId = text(section.id);
  const heading = node('div', 'query-section-heading');
  heading.append(node('h3', '', text(section.title) || '확인 내용'));
  appendOrigin(heading, section.origin);
  const status = statusBadge(section.status);
  if (status) heading.append(status);
  card.append(heading);
  const list = node('dl', 'query-items');
  for (const item of Array.isArray(section.items) ? section.items : []) {
    if (!item || typeof item !== 'object') continue;
    const row = node('div', 'query-item');
    if (text(item.label)) row.append(node('dt', '', item.label));
    const value = node('dd', 'query-value', text(item.value));
    appendOrigin(value, item.origin);
    if (text(item.detail)) value.append(node('p', 'query-detail', item.detail));
    row.append(value); list.append(row);
  }
  card.append(list); return card;
}

function deliveryMetrics(sections) {
  const ledger = sections.find(section => section.id === 'ledger');
  if (!ledger) return null;
  // These are already labelled server facts, not values parsed or calculated
  // from the user's question. Missing facts do not get reference-demo defaults.
  const labels = ['남은 고객 도착 약속', '추가 양품 목표'];
  const metrics = node('div', 'query-delivery-metrics');
  for (const label of labels) {
    const matches = (Array.isArray(ledger.items) ? ledger.items : []).filter(item => item?.label === label && text(item.value));
    if (matches.length !== 1) continue;
    const item = matches[0], card = node('section', 'query-delivery-metric');
    card.append(node('h3', '', label), node('strong', 'query-metric-value', item.value));
    appendOrigin(card, item.origin || ledger.origin);
    if (text(item.detail)) card.append(node('p', 'query-detail', item.detail));
    metrics.append(card);
  }
  return metrics.children.length ? metrics : null;
}

function deliverySections(sections) {
  const result = node('div', 'query-sections query-delivery-flow');
  const groups = [
    ['현재 원장 수량', ['ledger']],
    ['양품 목표와 계획 투입', ['production']],
    ['BOM·재고 확인', ['source', 'materials', 'material_unknowns', 'inventory']],
    ['도착기한 판단에 남은 조건', ['delivery', 'next']],
  ];
  const included = new Set(groups.flatMap(([, ids]) => ids));
  const additional = sections.filter(section => !included.has(section.id));
  if (additional.length) groups.push(['추가 확인', additional.map(section => section.id)]);
  let index = 0;
  for (const [title, ids] of groups) {
    const selected = sections.filter(section => ids.includes(section.id));
    if (!selected.length) continue;
    const step = node('section', 'query-flow-step');
    if (ids.includes('delivery')) step.className += ' query-flow-conditions';
    const heading = node('div', 'query-flow-heading');
    heading.append(node('span', 'query-flow-number', String(++index)), node('h3', '', title));
    step.append(heading);
    for (const section of selected) step.append(sectionCard(section));
    result.append(step);
  }
  return result;
}

function evidenceFlow(answer, suppliedEdges, labelFor, onEvidence, onReplay) {
  const allowed = new Set(Array.isArray(answer.evidence_edge_ids) ? answer.evidence_edge_ids : []);
  const hops = new Set((Array.isArray(answer.graph_path) ? answer.graph_path : [])
    .filter(hop => Array.isArray(hop) && hop.length === 2 && hop.every(id => typeof id === 'string')).map(hop => JSON.stringify(hop)));
  const edges = [], seen = new Set();
  for (const raw of Array.isArray(suppliedEdges) ? suppliedEdges : []) {
    const id = text(raw?.edge_id) || text(raw?.id), source = text(raw?.source_node_id) || text(raw?.source);
    const target = text(raw?.target_node_id) || text(raw?.target), predicate = text(raw?.relation_type) || text(raw?.predicate);
    if (!id || !source || !target || !predicate || !allowed.has(id) || seen.has(id)
        || (hops.size && !hops.has(JSON.stringify([source, target])))) continue;
    seen.add(id); edges.push({ id, source, target, predicate, label: text(raw.label) || predicate });
  }
  if (!edges.length) return null;
  const result = node('section', 'query-evidence-flow');
  const heading = node('div', 'query-section-heading');
  heading.append(node('h3', '', '조회한 근거 관계'), action('그래프에서 보기', onReplay));
  result.append(heading, node('p', 'query-detail', '현재 원장에 기록된 관계입니다. 업무 완료 순서나 미래 일정으로 해석하지 않습니다.'));
  const visible = node('ol', 'query-path-list'), remaining = node('ol', 'query-path-list');
  edges.forEach((edge, index) => {
    const row = node('li', 'query-path-hop'); row.dataset.edgeId = edge.id;
    row.setAttribute('title', `${edge.predicate} · ${edge.id}`);
    const arrow = node('span', 'query-path-predicate', `— ${edge.label} →`);
    row.append(action(labelFor(edge.source), () => onEvidence(edge.source), 'chip'), arrow,
      action(labelFor(edge.target), () => onEvidence(edge.target), 'chip'));
    (index < 4 ? visible : remaining).append(row);
  });
  result.append(visible);
  if (edges.length > 4) {
    const more = node('details', 'query-more-paths');
    more.append(node('summary', '', `전체 관계 ${edges.length}개 · 나머지 ${edges.length - 4}개 보기`), remaining);
    result.append(more);
  }
  return result;
}

const bomPrerequisiteReasons = new Set(['REGISTERED_BOM_MISSING', 'NO_REVIEWED_APPLICABLE_ROWS',
  'APPLICATION_REVIEW_INCOMPLETE', 'NO_APPLICABLE_COMPONENTS', 'TECHNICAL_REVIEW_UNCONFIRMED']);
function applicableBomPrerequisite(answer) {
  const value = answer.prerequisite;
  return value && value.stage === 'APPLICABLE_BOM' && ['BLOCKED', 'PARTIAL', 'UNKNOWN'].includes(value.status)
    && bomPrerequisiteReasons.has(value.reason) ? value : null;
}

function reviewBomReference(answer) {
  const prerequisite = applicableBomPrerequisite(answer), action = prerequisite?.next_action;
  const ref = prerequisite?.bom, requirements = answer.calculation?.material_requirements;
  const snapshot = text(answer.meta?.snapshot_id) || text(answer.meta?.read_snapshot_id);
  if (!prerequisite || prerequisite.reason === 'REGISTERED_BOM_MISSING'
      || action?.type !== 'REVIEW_BOM_APPLICATION' || action.read_only !== true || action.requires_explicit_submission !== true
      || !ref || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(text(ref.document_id))
      || !Number.isSafeInteger(ref.version) || ref.version < 1 || !/^[0-9a-f]{64}$/.test(text(ref.payload_hash))
      || requirements?.bom_id !== ref.document_id || requirements.bom_version !== ref.version || !snapshot) return null;
  const matches = (Array.isArray(answer.evidence_refs) ? answer.evidence_refs : []).filter(value =>
    value?.record_type === 'bom_registration' && value.bom_id === ref.document_id && value.bom_version === ref.version);
  if (matches.length !== 1 || matches[0].payload_hash !== ref.payload_hash || matches[0].snapshot_id !== snapshot) return null;
  return Object.freeze({ document_id: ref.document_id, version: ref.version, payload_hash: ref.payload_hash });
}

function answerParagraphs(answer, sections) {
  const main = text(answer.answer) || text(answer.answer?.text) || '확인된 답변이 없습니다.';
  // Split only after sentence punctuation followed by whitespace, or explicit
  // line breaks. Decimal points and identifier contents are not interpreted.
  const paragraphs = main.split(/(?<=[.!?。！？])(?=\s+[^\s])|\n{2,}/u).filter(value => value.trim());
  // These strings are already labelled, same-snapshot server results. Do not
  // infer quantities from the question or calculate from reference fixtures.
  // Suppress placeholder repetition only when no applicable calculation is
  // available. A partial/technically-unconfirmed BOM can still have exact
  // conditional quantities, which must remain in the main answer.
  const prerequisite = applicableBomPrerequisite(answer);
  if (prerequisite && (['REGISTERED_BOM_MISSING', 'NO_REVIEWED_APPLICABLE_ROWS', 'NO_APPLICABLE_COMPONENTS'].includes(prerequisite.reason)
      || prerequisite.counts?.calculation_items === 0
      || Array.isArray(prerequisite.selected_item_ids) && prerequisite.selected_item_ids.length > 0
        && prerequisite.selected_calculation_items === 0)) return [...new Set(paragraphs)];
  // Materials with structured server numbers become one table (materialTable);
  // the per-item sentences stay as row hover text instead of the main answer.
  const tabled = materialRows(answer).length > 0;
  for (const section of sections) {
    if (!['materials', 'inventory'].includes(section.id) || (tabled && section.id === 'materials')) continue;
    for (const item of Array.isArray(section.items) ? section.items : []) {
      if (!item || !text(item.value)) continue;
      const origin = text(item.origin || section.origin);
      const provenance = Object.hasOwn(origins, origin.toLowerCase()) ? origins[origin.toLowerCase()][0] : '';
      paragraphs.push(`${text(item.label) ? item.label + ': ' : ''}${item.value}${text(item.detail) ? '. ' + item.detail : ''}${provenance ? ' (' + provenance + ')' : ''}`);
    }
  }
  return [...new Set(paragraphs)];
}

function materialRows(answer) {
  const items = answer?.calculation?.material_requirements?.items;
  if (!Array.isArray(items)) return [];
  return items.filter(item => item && text(item.item_id) && item.planned_required_qty != null && item.available_qty != null);
}
// Compact table for the graph-screen answer: shortages first, planned basis,
// with the goal-basis numbers in brackets and the server sentence on hover.
function materialTable(answer, sections) {
  const rows = materialRows(answer);
  if (!rows.length) return null;
  const sentences = new Map();
  for (const section of sections) if (section.id === 'materials') for (const item of section.items || []) if (text(item?.label)) sentences.set(item.label, `${item.value}${text(item.detail) ? '. ' + item.detail : ''}`);
  const sorted = [...rows].sort((a, b) => Number(b.planned_shortage_qty || 0) - Number(a.planned_shortage_qty || 0) || String(a.item_id).localeCompare(String(b.item_id)));
  const wrap = node('div', 'query-material-table');
  const table = node('table', 'query-mini-table');
  const head = node('tr');
  for (const label of ['품목', '필요', '가용', '부족']) head.append(node('th', '', label));
  const thead = node('thead'); thead.append(head); table.append(thead);
  const body = node('tbody');
  for (const item of sorted) {
    const row = node('tr');
    const unit = item.unit && item.unit !== 'EA' ? ` ${item.unit}` : '';
    const shortage = Number(item.planned_shortage_qty || 0);
    row.append(node('td', 'mono', item.item_id), node('td', 'n', `${item.planned_required_qty}${unit}`), node('td', 'n', `${item.available_qty}${unit}`),
      node('td', shortage > 0 ? 'n alert' : 'n', shortage > 0 ? `${item.planned_shortage_qty}${unit}` : '—'));
    if (sentences.has(item.item_id)) row.title = sentences.get(item.item_id);
    body.append(row);
  }
  table.append(body); wrap.append(table);
  wrap.append(node('p', 'note', '계획 투입(수율 반영) 기준 수량입니다. 항목에 마우스를 올리면 서버 산출 근거(양품 목표 기준 수량·출처)를 볼 수 있습니다. 적용 매핑·수량은 시연 가정이며 미확인 BOM 행은 부족 0으로 보지 않습니다.'));
  return wrap;
}

function quantityRuns(paragraph, value, runs, segmenter) {
  // Bold literal quantity tokens; never interpret server markup or convert a
  // decimal string to Number. IDs such as PART-0012 remain ordinary text.
  const quantities = /(?<![\p{L}\p{N}_-])[+−-]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:EA(?![A-Za-z])|개|대|행|%)|(?![\p{L}\p{N}_-]))/gu;
  const boundaries = segmenter ? new Set([0, value.length, ...[...segmenter.segment(value)].map(item => item.index)]) : null;
  const append = (part, bold) => {
    if (!part) return;
    const span = node(bold ? 'strong' : 'span', bold ? 'query-quantity' : '');
    paragraph.append(span);
    runs.push({ span, full: part, shown: -1,
      characters: segmenter ? [...segmenter.segment(part)].map(item => item.segment) : [part] });
  };
  let offset = 0;
  for (const match of value.matchAll(quantities)) {
    if (boundaries && (!boundaries.has(match.index) || !boundaries.has(match.index + match[0].length))) continue;
    append(value.slice(offset, match.index), false); append(match[0], true); offset = match.index + match[0].length;
  }
  append(value.slice(offset), false);
}

function evidencePreview(ids, suppliedNodes) {
  // These are exact-snapshot server types, never guesses from an identifier or
  // display label. Source rows remain available in the collapsed remainder.
  const types = new Map((Array.isArray(suppliedNodes) ? suppliedNodes : [])
    .filter(value => value && typeof value === 'object')
    .map(value => [text(value.node_id) || text(value.id),
      text(value.entity_ref?.entity_type || value.type || value.node_type).replace(/[^a-z0-9]/gi, '').toLowerCase()]));
  const priority = new Map([
    ['contractline', 0], ['deliveryround', 1], ['productionjob', 2],
    ['configuration', 3], ['productconfiguration', 3], ['productmodel', 4],
    ['bom', 5], ['bomsnapshot', 5], ['item', 6], ['internalitem', 6],
    ['lot', 7], ['partlot', 7], ['reservation', 8], ['inventoryreservation', 8],
    ['shipment', 9], ['arrival', 10], ['quotation', 11], ['supplierquote', 11],
    ['purchaserequest', 12], ['purchaseorder', 13], ['customer', 14],
    ['sourcedocument', 15], ['sourcesnapshot', 15], ['sourcerow', 100], ['bomline', 100],
  ]);
  if (ids.length <= 6) return { visible: ids, remaining: [] };
  const visible = ids.map((id, index) => ({ id, index, rank: priority.get(types.get(id)) ?? 50 }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index).slice(0, 6).map(value => value.id);
  const selected = new Set(visible);
  return { visible, remaining: ids.filter(id => !selected.has(id)) };
}

const quoteConditions = { MET: ['수량 충족', 'ok'], NOT_MET: ['수량 미충족', 'bad'], UNKNOWN: ['판단 보류 · 공급한도 미확인', 'warn'] };
function answerTable(table) {
  if (!table || !Array.isArray(table.columns) || !Array.isArray(table.rows)) return null;
  const alerts = new Set((Array.isArray(table.alert_cells) ? table.alert_cells : []).map(cell => `${cell?.[0]}:${cell?.[1]}`));
  const element = node('table', 'query-mini-table');
  const head = node('tr'); table.columns.forEach(column => head.append(node('th', '', text(column))));
  const thead = node('thead'); thead.append(head);
  const body = node('tbody');
  table.rows.forEach((row, r) => {
    const tr = node('tr');
    (Array.isArray(row) ? row : []).forEach((cell, c) => tr.append(node('td', `${c >= 2 ? 'n' : 'mono'}${alerts.has(`${r}:${c}`) ? ' alert' : ''}`, text(cell))));
    body.append(tr);
  });
  element.append(thead, body);
  return element;
}
function quoteCard(quote, onChooseQuote) {
  const card = node('article', 'query-quote-card');
  card.dataset.quoteId = text(quote.quote_id);
  const head = node('div', 'query-quote-head');
  head.append(node('b', '', text(quote.supplier_id)), node('span', 'chip', text(quote.quote_ref)));
  const [label, tone] = quoteConditions[quote.quantity_condition] || ['미확인', 'mute'];
  head.append(node('span', `pill ${tone}`, label));
  if (quote.freight == null) head.append(node('span', 'pill warn', '운송비 미확인'));
  const value = v => v == null || v === '' ? '미확인' : String(v);
  const metrics = node('dl', 'query-quote-metrics');
  for (const [name, v] of [['주문 수량', quote.recommended_qty], ['단가', quote.unit_price], ['공급한도', quote.supply_limit_qty],
    ['MOQ / 배수', `${value(quote.moq)} / ${value(quote.order_multiple)}`], ['총액', quote.total], ['리드타임(견적)', quote.lead_business_days == null ? null : `${quote.lead_business_days}영업일`]]) {
    metrics.append(node('dt', '', name), node('dd', '', value(v)));
  }
  card.append(head, metrics, node('p', 'query-quote-reason', text(quote.reason)));
  const choose = action('이 견적으로 구매 요청 초안', () => onChooseQuote(quote), 'btn pri sm');
  choose.dataset.queryAction = 'choose-quote';
  if (quote.quantity_condition === 'NOT_MET' || !quote.recommended_qty) choose.disabled = true;
  card.append(choose);
  return card;
}

const evidenceKinds = { ledger_entity: '원장 개체', source_row: 'BOM 원문 행', application_review: 'BOM 적용 검토', development_document: '개발 문서' };
// Model-written summary that passed the server's checks. Numbers in it were
// filled by the server from its own results; the model only wrote sentences.
// PENDING shows a placeholder that updateAiSummary() fills after the answer.
function fillAiCard(card, summary) {
  const heading = node('div', 'query-answer-heading');
  if (summary?.status === 'PENDING') {
    card.className = 'query-answer-card query-ai-summary query-ai-pending';
    heading.append(node('h3', '', 'AI 요약'), node('span', 'query-answer-tool', '작성 중'));
    const line = node('p', 'query-paragraph');
    line.append(node('span', 'mk-spinner'), node('span', '', 'AI가 요약을 작성하고 있습니다. 원장 답변은 아래에 이미 표시되어 있습니다.'));
    card.replaceChildren(heading, line);
    return true;
  }
  if (!summary || summary.status !== 'VERIFIED' || !Array.isArray(summary.paragraphs) || !summary.paragraphs.length) {
    card.className = 'query-answer-card query-ai-summary-fallback';
    card.replaceChildren(node('p', 'note query-ai-fallback', 'AI 요약이 서버 검증을 통과하지 못해 원장 계산 결과만 표시합니다.'));
    return false;
  }
  card.className = 'query-answer-card query-ai-summary';
  heading.append(node('h3', '', 'AI 요약'), node('span', 'query-answer-tool', summary.execution_mode === 'live' ? '모델 작성 · 서버 검증' : '시험 모델 · 서버 검증'));
  const children = [heading];
  const facts = new Map((summary.facts || []).map(fact => [fact.id, text(fact.name)]));
  summary.paragraphs.slice(0, 4).forEach(paragraph => {
    const p = node('p', 'query-paragraph', text(paragraph.text));
    const chips = node('span', 'query-ai-cites');
    // One chip for the server facts used (names on hover), one per search evidence.
    const cites = Array.isArray(paragraph.cites) ? paragraph.cites : [];
    const used = cites.filter(cite => text(cite?.id) && !cite.evidence).map(cite => facts.get(cite.id) || cite.id);
    if (used.length) {
      const chip = node('span', 'query-ai-cite', `서버 사실 ${used.length}개`);
      chip.title = used.join('\n');
      chips.append(chip);
    }
    for (const cite of cites.filter(cite => text(cite?.id) && cite.evidence)) {
      const chip = node('span', 'query-ai-cite', evidenceKinds[cite.evidence.kind] || '근거');
      chip.title = `검색 근거 ${text(cite.evidence.evidence_id)}`;
      chips.append(chip);
    }
    if (chips.children.length) p.append(chips);
    children.push(p);
  });
  children.push(node('p', 'note', '숫자는 서버 계산값이며 AI는 문장만 작성했습니다. 판정은 아래 원장 답변을 따릅니다.'));
  card.replaceChildren(...children);
  return true;
}
function aiSummaryCard(answer) {
  const summary = answer.ai_summary;
  if (!summary || !['VERIFIED', 'PENDING'].includes(summary.status)) return null;
  const card = node('section', 'query-answer-card');
  card.dataset.blockId = 'ai_summary';
  return fillAiCard(card, summary) ? card : null;
}
// Fills the placeholder card of an answer already on screen; the answer itself is unchanged.
export function updateAiSummary(message, summary) {
  const card = message?.aiSummaryCard;
  if (!card) return false;
  return fillAiCard(card, summary);
}
function retrievalChips(answer) {
  const found = answer.retrieval;
  if (!found || !Array.isArray(found.hits) || !found.hits.length) return null;
  const wrap = node('div', 'query-retrieval');
  wrap.append(node('span', 'query-retrieval-label', '검색 근거'));
  found.hits.slice(0, 5).forEach(hit => {
    // Ledger entity text starts with its internal URN; show the readable part.
    const label = text(hit.text).replace(/^urn:\S+\s*/, '').slice(0, 36);
    const chip = node('span', 'query-retrieval-chip', `${evidenceKinds[hit.kind] || '근거'} · ${label}`);
    chip.title = `${text(hit.origin)} · ${text(hit.review_status)} · ${(hit.match_modes || []).join('+')}`;
    wrap.append(chip);
  });
  return wrap;
}

function confirmedAnswerBlocks(answer, mode) {
  const blocks = answer.answer_blocks;
  if (mode !== 'workflow' || !Array.isArray(blocks) || !blocks.length || blocks.length > 12) return null;
  const ids = new Set();
  for (const block of blocks) {
    if (!block || !text(block.id) || ids.has(block.id) || !text(block.title) || !text(block.tool)
        || !['normal', 'attention', 'assumption', 'verdict'].includes(block.kind) || !Array.isArray(block.paragraphs)
        || !block.paragraphs.length || !block.paragraphs.every(value => text(value).trim())) return null;
    ids.add(block.id);
  }
  return blocks;
}

export function renderQueryAnswer(message, answer, {
  evidenceIds = [], evidenceEdges = [], evidenceNodes = [], labelFor = id => id, onEvidence = () => {},
  onReplay = () => {}, onCancel = () => {}, onOpenBom = () => {}, onReviewBom = () => {}, onChooseQuote = () => {}, animate = false,
  mode = 'graph', beforeBody, onProgress = () => {},
} = {}) {
  let disposed = false, typingComplete = false, frame = null, startedAt = null, settled = false;
  let hookStarted = false, hookWaiting = false, hookVersion = 0, hookTimer = null, bodyStartedAt = null;
  let resolveFinished;
  const finished = new Promise(resolve => { resolveFinished = resolve; });
  const settle = value => { if (!settled) { settled = true; resolveFinished(value); } };
  const guard = callback => (...args) => { if (!disposed) return callback(...args); };
  const progress = () => { if (!disposed) { try { onProgress(); } catch { /* Scrolling is optional presentation only. */ } } };
  onEvidence = guard(onEvidence); onReplay = guard(onReplay); onCancel = guard(onCancel); onOpenBom = guard(onOpenBom); onReviewBom = guard(onReviewBom); onChooseQuote = guard(onChooseQuote);
  message.replaceChildren();
  if (Number.isInteger(answer.meta?.ledger_version)) message.dataset.ledgerVersion = String(answer.meta.ledger_version);
  const sectionData = (Array.isArray(answer.sections) ? answer.sections : []).filter(section => section && typeof section === 'object');
  const blocks = confirmedAnswerBlocks(answer, mode), workflow = Boolean(blocks), cards = [];
  message.dataset.answerLayout = workflow ? 'workflow' : 'graph';
  const delivery = Array.isArray(answer.intent) && answer.intent.includes('delivery') && answer.status !== 'NEEDS_CLARIFICATION';
  message.dataset.queryPresentation = delivery ? 'delivery' : 'standard';
  const steps = node('ol', 'trace-steps');
  steps.setAttribute('aria-label', '서버가 완료한 원장 조회 과정의 순차 표시');
  steps.setAttribute('title', '서버가 완료한 조회 결과입니다. 표시 효과는 실제 처리 진행률이 아닙니다.');
  steps.dataset.presentation = 'confirmed-results';
  for (const step of Array.isArray(answer.steps) ? answer.steps : []) if (text(step)) steps.append(node('li', 'pending', step));
  if (steps.children.length && !workflow) message.append(steps);
  const verdict = node('div', delivery ? 'query-verdict' : 'query-body');
  const summary = node('div', 'query-summary');
  if (text(answer.intent_label)) summary.append(node('span', 'query-intent', answer.intent_label));
  const status = statusBadge(answer.status);
  if (status) summary.append(status);
  if (delivery && answer.calculation?.actual_delivery_feasibility === 'UNKNOWN') summary.append(node('span', 'query-delivery-status', '납기 판단 보류'));
  const provenance = new Set();
  for (const section of sectionData) for (const value of [section.origin, ...(Array.isArray(section.items) ? section.items.map(item => item?.origin) : [])]) {
    const origin = text(value).toLowerCase();
    if (Object.hasOwn(origins, origin)) provenance.add(origins[origin][0]);
  }
  const paragraphs = workflow ? blocks.flatMap(block => block.paragraphs) : answerParagraphs(answer, sectionData), runs = [];
  const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter('ko', { granularity: 'grapheme' }) : null;
  const reduced = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    || globalThis.window?.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const typing = animate === true && !reduced && segmenter && typeof requestAnimationFrame === 'function' && typeof cancelAnimationFrame === 'function';
  const stepNodes = workflow ? [] : [...steps.children];
  const stepDuration = typing ? Math.min(2400, stepNodes.length * 600) : 0;
  message.dataset.answerDisplayState = typing ? (workflow ? 'cards' : stepDuration ? 'steps' : 'typing') : 'complete';
  const visibleBody = node('div', workflow ? 'query-prose query-workflow' : 'query-prose');
  if (workflow) {
    visibleBody.dataset.presentation = 'confirmed-results';
    visibleBody.setAttribute('title', '서버가 확정한 답변을 순서대로 표시합니다. 실제 도구 실행 진행률이 아닙니다.');
  }
  if (typing) {
    // One body only: a clipped full-text duplicate was still copied alongside
    // the animated text. Defer this region's announcement until it is complete.
    visibleBody.setAttribute('role', 'region');
    visibleBody.setAttribute('aria-label', '조회 답변');
    visibleBody.setAttribute('aria-live', 'polite');
    visibleBody.setAttribute('aria-atomic', 'true');
    visibleBody.setAttribute('aria-busy', 'true');
    visibleBody.setAttribute('aria-hidden', 'true');
  }
  const appendParagraphs = (container, values) => values.forEach((value, index) => {
    const paragraph = node('p', index ? 'query-paragraph' : 'answer');
    quantityRuns(paragraph, value, runs, segmenter); container.append(paragraph);
  });
  if (workflow) {
    let number = 0;
    blocks.forEach(block => {
      const kindClass = { attention: ' query-answer-attention', assumption: ' query-answer-assumption', verdict: ' query-answer-verdict' }[block.kind] || '';
      const card = node('section', `query-answer-card${kindClass}`);
      card.dataset.blockId = block.id; card.hidden = typing;
      const heading = node('div', 'query-answer-heading');
      if (block.kind !== 'verdict') heading.append(node('span', 'query-answer-number', String(++number)));
      heading.append(node('h3', '', block.title), node('span', 'query-answer-tool', block.tool));
      card.append(heading);
      // An assumption card is shown open; its heading tag (for example
      // "시연 가정 · 일정") and its own style mark it as a scenario frame.
      const target = card;
      const table = answerTable(block.table);
      if (table) target.append(table);
      const start = runs.length; appendParagraphs(target, block.paragraphs);
      if (Array.isArray(block.bullets) && block.bullets.length) {
        const list = node('ul', 'query-answer-bullets');
        block.bullets.forEach(value => list.append(node('li', '', text(value))));
        target.append(list);
      }
      if (Array.isArray(block.quotes) && block.quotes.length) {
        const wrap = node('div', 'query-quote-list');
        block.quotes.forEach(quote => wrap.append(quoteCard(quote, onChooseQuote)));
        target.append(wrap);
      }
      if (block.action === 'REGISTER_BOM') {
        const register = action('BOM 원본 등록', onOpenBom, 'btn pri sm');
        register.dataset.queryAction = 'bom-register'; target.append(register);
      }
      cards.push({ element: card, runs: runs.slice(start), paragraphs: block.paragraphs });
      visibleBody.append(card);
    });
  } else appendParagraphs(visibleBody, paragraphs);
  const aiCard = aiSummaryCard(answer);
  message.aiSummaryCard = aiCard;
  if (aiCard) verdict.append(aiCard);
  verdict.append(visibleBody);
  const typingControls = node('div', 'query-typing-controls'), typingStatus = node('span', 'query-typing-status');
  typingStatus.setAttribute('role', 'status'); typingStatus.setAttribute('aria-live', 'polite'); typingStatus.setAttribute('aria-atomic', 'true');
  const showAll = action('모두 보기', () => finish());
  typingControls.append(typingStatus, showAll); typingControls.hidden = !typing;
  verdict.append(typingControls);
  const supplement = node('div', 'query-supplement');
  supplement.hidden = typing;
  const requirements = answer.calculation?.material_requirements;
  const cardRegister = (blocks || []).some(block => block.action === 'REGISTER_BOM');
  if (cardRegister) { /* The card already carries the registration entry. */ }
  else if (requirements?.state === 'BLOCKED_BOM' && requirements.bom_id == null) {
    const register = action('BOM 원본 등록', onOpenBom, 'btn pri sm');
    register.dataset.queryAction = 'bom-register';
    supplement.append(register);
  } else {
    const reviewRef = reviewBomReference(answer);
    if (reviewRef) {
      const review = action('BOM 적용 검토', () => onReviewBom(reviewRef), 'btn pri sm');
      review.dataset.queryAction = 'bom-review';
      supplement.append(review);
    }
  }
  const materials = workflow ? null : materialTable(answer, sectionData);
  if (materials) supplement.prepend ? supplement.prepend(materials) : supplement.append(materials);
  const chips = retrievalChips(answer);
  if (chips) supplement.append(chips);
  if (answer.ai_summary && ['REJECTED', 'UNAVAILABLE'].includes(answer.ai_summary.status)) {
    supplement.append(node('p', 'note query-ai-fallback', 'AI 요약이 서버 검증을 통과하지 못해 원장 계산 결과만 표시합니다.'));
  }
  const scope = [];
  if (status) scope.push(status.textContent);
  if (delivery && answer.calculation?.actual_delivery_feasibility === 'UNKNOWN') scope.push('납기 판단 보류');
  scope.push(...provenance);
  if (answer.status === 'UNKNOWN' || answer.status === 'PARTIAL') scope.push('미확인은 부족 0이나 제조·납기 확정을 뜻하지 않습니다.');
  if (scope.length) supplement.append(node('p', 'note query-scope-note', [...new Set(scope)].join(' · ')));
  message.append(verdict);
  const details = node('details', 'query-details');
  details.append(node('summary', '', '상세 근거·조건·조회 기준'));
  if (workflow && steps.children.length) {
    for (const step of steps.children) step.className = 'done';
    details.append(steps);
  }
  if (summary.children.length) details.append(summary);
  if (text(answer.conclusion)) details.append(node('p', 'query-conclusion', answer.conclusion));
  if (delivery) {
    const metrics = deliveryMetrics(sectionData);
    if (metrics) details.append(metrics);
    const flow = evidenceFlow(answer, evidenceEdges, labelFor, onEvidence, onReplay);
    if (flow) details.append(flow);
  }
  const sections = delivery ? deliverySections(sectionData) : node('div', 'query-sections');
  if (!delivery) for (const section of sectionData) sections.append(sectionCard(section));
  if (sections.children.length) details.append(sections);

  const meta = answer.meta || {};
  const basis = node('div', 'query-basis');
  if (answer.agent_execution) {
    const execution = answer.agent_execution;
    const mode = execution.live_calls > 0 ? `실제 모델 기능 선택 ${execution.live_calls}회`
      : execution.fake_calls > 0 ? `Fake 모델 기능 선택 ${execution.fake_calls}회`
      : '직접 기능 선택 · 모델 호출 없음';
    basis.append(node('p', '', `${mode} · 수량·부족 답변은 서버 원장 계산 · 원장 변경 없음`));
    basis.append(node('p', 'query-snapshot', `실행 · ${text(execution.execution_id)}`));
  }
  basis.append(node('p', '', `기준 시점 · ${text(meta.as_of) || text(meta.effective_at) || '미확인'}`));
  if (text(meta.known_as_of)) basis.append(node('p', '', `확인 시점 · ${meta.known_as_of}`));
  const snapshot = text(meta.snapshot_id) || text(meta.read_snapshot_id);
  if (snapshot) basis.append(node('p', 'query-snapshot', `스냅샷 · ${snapshot}`));
  details.append(basis);
  const evidence = node('div', 'evidence-path');
  const ids = [...new Set((Array.isArray(evidenceIds) ? evidenceIds : []).filter(id => typeof id === 'string' && id))];
  if (ids.length) evidence.append(node('span', 'query-evidence-count', `거친 노드 ${ids.length}개`));
  const preview = evidencePreview(ids, evidenceNodes);
  const evidenceChip = id => {
    const chip = action(labelFor(id), () => onEvidence(id), 'chip');
    chip.dataset.evidenceId = id;
    return chip;
  };
  preview.visible.forEach(id => evidence.append(evidenceChip(id)));
  if (preview.remaining.length) {
    const more = node('details', 'query-more-evidence');
    const rest = node('div', 'query-evidence-remainder');
    rest.append(...preview.remaining.map(evidenceChip));
    more.append(node('summary', '', `전체 근거 ${ids.length}개 보기 · 나머지 ${preview.remaining.length}개`), rest);
    evidence.append(more);
  }
  supplement.append(evidence);

  const footer = node('div', 'answer-footer');
  const liveCalls = answer.agent_execution?.live_calls ?? answer.model_calls ?? 0;
  footer.append(node('span', '', `원장 v${meta.ledger_version ?? '미확인'} · 그래프 v${meta.projection_version ?? '미확인'} · 모델 호출 ${liveCalls}회`));
  const replayStatus = node('span', 'query-replay-status');
  replayStatus.setAttribute('aria-live', 'polite');
  const cancel = action('재생 중단', guard(() => { playVersion++; onCancel(); setReplayState('cancelled'); }));
  cancel.hidden = true;
  if (ids.length) footer.append(action('그래프에서 보기', onReplay));
  footer.append(cancel, replayStatus); supplement.append(footer, details);
  message.append(supplement);
  let playVersion = 0;

  const total = runs.reduce((sum, run) => sum + run.characters.length, 0);
  const duration = Math.min(8000 - stepDuration, Math.max(300, total * 18));
  let cardEnd = 500;
  const cardTypingBudget = Math.max(300, 8000 - 500 - cards.length * 350);
  for (const card of cards) {
    card.total = card.runs.reduce((sum, run) => sum + run.characters.length, 0);
    card.start = cardEnd; card.typingStart = card.start + 350;
    card.end = card.typingStart + Math.min(Math.max(120, card.total * 18), cardTypingBudget * card.total / Math.max(1, total));
    cardEnd = card.end;
  }
  let displayedStep = -1;
  function drawSteps(active) {
    if (disposed || displayedStep === active) return;
    displayedStep = active;
    stepNodes.forEach((step, index) => {
      const state = index < active ? 'done' : index === active ? 'active' : 'pending';
      step.className = state; step.dataset.displayState = state;
      step.setAttribute('aria-current', state === 'active' ? 'step' : 'false');
    });
  }
  function drawRuns(selectedRuns, count) {
    if (disposed) return;
    for (const run of selectedRuns) {
      const shown = Math.min(run.characters.length, Math.max(0, count));
      if (shown !== run.shown) { run.span.textContent = shown === run.characters.length ? run.full : run.characters.slice(0, shown).join(''); run.shown = shown; }
      count -= run.characters.length;
    }
  }
  const draw = count => drawRuns(runs, count);
  function drawCards(elapsed) {
    for (const card of cards) {
      const active = elapsed >= card.start, done = elapsed >= card.end;
      card.element.hidden = !active;
      const state = done ? 'done' : active ? 'active' : 'pending';
      if (card.element.dataset.displayState !== state) {
        card.element.dataset.displayState = state;
        card.element.setAttribute('aria-current', state === 'active' ? 'step' : 'false');
      }
      drawRuns(card.runs, done ? card.total : Math.floor(card.total * Math.max(0, (elapsed - card.typingStart) / (card.end - card.typingStart))));
    }
  }
  function clearFrame() {
    if (frame !== null) { const pending = frame; frame = null; try { cancelAnimationFrame(pending); } catch { /* Finish/dispose still release our callback ownership. */ } }
  }
  function clearHook() {
    hookVersion++; hookWaiting = false;
    if (hookTimer !== null) { clearTimeout(hookTimer); hookTimer = null; }
  }
  function startHook() {
    hookStarted = true; hookWaiting = true;
    message.dataset.answerDisplayState = 'graph';
    typingStatus.textContent = '근거 경로 표시 중';
    progress();
    const version = ++hookVersion;
    const resume = () => {
      if (disposed || typingComplete || version !== hookVersion || !hookWaiting) return;
      clearHook(); bodyStartedAt = null;
      try { frame = requestAnimationFrame(tick); } catch { finish(); }
    };
    // The host has a bounded replay; this local cap also prevents a lost hook
    // from leaving a confirmed answer indefinitely hidden. Never call the hook
    // synchronously while the host is assigning the returned presentation.
    hookTimer = setTimeout(resume, 30000);
    Promise.resolve().then(() => {
      if (!disposed && !typingComplete && version === hookVersion) return beforeBody();
    }).then(resume, resume);
  }
  function finish() {
    if (disposed || typingComplete) return;
    clearFrame(); clearHook();
    drawSteps(stepNodes.length);
    try { if (workflow) drawCards(Infinity); else draw(total); }
    catch { visibleBody.replaceChildren(...paragraphs.map((value, index) => node('p', index ? 'query-paragraph' : 'answer', value))); }
    typingComplete = true; showAll.hidden = true;
    message.dataset.answerDisplayState = 'complete';
    if (typing) {
      visibleBody.setAttribute('aria-hidden', 'false');
      visibleBody.setAttribute('aria-busy', 'false');
      typingStatus.textContent = '답변 표시 완료';
    }
    supplement.hidden = false;
    progress();
    settle({ completed: true });
  }
  function dispose() {
    if (disposed) return;
    clearFrame(); clearHook(); disposed = true; playVersion++; settle({ completed: false, disposed: true });
  }
  function tick(time) {
    frame = null;
    if (disposed || typingComplete) return;
    try {
      if (!Number.isFinite(time)) { finish(); return; }
      if (startedAt === null) startedAt = time;
      const elapsed = Math.max(0, time - startedAt);
      if (workflow) {
        drawCards(elapsed);
        progress();
        if (elapsed >= cardEnd) finish(); else frame = requestAnimationFrame(tick);
        return;
      }
      if (elapsed < stepDuration) {
        drawSteps(Math.min(stepNodes.length - 1, Math.floor(elapsed / (stepDuration / stepNodes.length))));
        progress();
        frame = requestAnimationFrame(tick); return;
      }
      drawSteps(stepNodes.length);
      if (mode === 'graph' && typeof beforeBody === 'function') {
        if (!hookStarted) { startHook(); return; }
        if (hookWaiting) return;
        if (bodyStartedAt === null) bodyStartedAt = time;
      }
      if (message.dataset.answerDisplayState !== 'typing') {
        message.dataset.answerDisplayState = 'typing';
        typingStatus.textContent = '서버에서 확인된 답변 표시 중';
      }
      const bodyElapsed = hookStarted ? time - bodyStartedAt : elapsed - stepDuration;
      const count = Math.floor(total * Math.min(1, Math.max(0, bodyElapsed / duration)));
      draw(count);
      progress();
      if (count >= total) finish(); else frame = requestAnimationFrame(tick);
    } catch { finish(); }
  }
  if (typing && total) {
    try {
      draw(0); drawSteps(stepDuration ? 0 : stepNodes.length);
      if (workflow) drawCards(0);
      typingStatus.textContent = workflow || stepDuration ? '확인한 조회 과정 표시 중' : '서버에서 확인된 답변 표시 중';
      frame = requestAnimationFrame(tick);
    }
    catch { finish(); }
  } else finish();

  function setReplayState(state) {
    if (disposed) return;
    cancel.hidden = state !== 'playing';
    replayStatus.textContent = {
      playing: `${typingComplete ? '답변 표시 완료 · ' : ''}근거 경로 재생 중`, complete: '근거 경로 재생 완료',
      cancelled: '근거 재생 중단 · 답변과 기준 버전은 유지됩니다.',
      unavailable: '근거를 재생하지 못했습니다. 답변은 표시된 조회 기준을 유지합니다.',
    }[state] || '';
  }
  return {
    finish, dispose, finished,
    async play(replay, isCurrent = () => true) {
      if (disposed) return;
      const version = ++playVersion;
      setReplayState('playing');
      try {
        const result = await replay();
        if (version === playVersion) setReplayState(isCurrent() && result?.completed ? 'complete' : 'cancelled');
      } catch {
        if (version === playVersion) setReplayState(isCurrent() ? 'unavailable' : 'cancelled');
      }
    },
  };
}
