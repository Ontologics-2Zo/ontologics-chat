// 디지털 트윈 2 (#twin2): 팀이 다듬은 디지털 트윈 화면(공정 색·옆 이름표·트럭·질의 근거 공정 정보)을
// 기존 #twin 탭과 분리해 그대로 쓴다. 클래스 접두사만 twin2- 로 바꿨고, 데이터는 같은 서버 snapshot이다.
// MK-S2 디지털 트윈 탭.
// 표시 계층만 담당한다. 노드·관계·수량·사건은 모두 /api/mks2/snapshot 과 /api/mks2/ontology 응답에서 읽고,
// 화면에 고정한 업무 값이나 시뮬레이션 값은 쓰지 않는다. 원장에 없는 값은 '미확인'으로 표시한다.
// 많은 BOM 행 같은 같은 종류 노드는 '외 n' 묶음으로 축약해 보여 줄 뿐, 원장·T-Box는 바꾸지 않는다.
import * as THREE from './vendor/three/three.module.min.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from './vendor/three/CSS2DRenderer.js';
import { materialAlerts, changeText } from './material_watch.js';

const element = (tag, className = '', text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
};
export const formatTwinValue = value => value === null || value === undefined || value === '' ? '미확인'
  : typeof value === 'number' ? Number.isFinite(value) ? value.toLocaleString('ko-KR', { maximumSignificantDigits: 21 }) : '미확인' : String(value);
const fmt = formatTwinValue;
const isNum = value => typeof value === 'number' && Number.isFinite(value);
const arrayCount = value => Array.isArray(value) ? value.length : null;
function decimal(value) {
  const raw = typeof value === 'number' && Number.isFinite(value) ? fmt(value).replaceAll(',', '') : value;
  if (typeof raw !== 'string' || raw.length > 256 || !/^\d+(?:\.\d+)?$/.test(raw)) return null;
  const [whole, fraction = ''] = raw.split('.');
  return { value: BigInt(whole + fraction), scale: 10n ** BigInt(fraction.length) };
}
export function twinMaterialStatus(item) {
  const required = decimal(item?.required_qty), available = decimal(item?.available_qty), shortage = decimal(item?.shortage_qty);
  const unknown = { k: 'warn', text: '소요 미확인' };
  if (!required || !available || !shortage) return unknown;
  const short = available.value * required.scale < required.value * available.scale;
  if (item.quantity_status === 'SHORTAGE' && short && shortage.value > 0n) return { k: 'bad', text: `부족 ${fmt(item.shortage_qty)}` };
  if (item.quantity_status === 'COVERED' && !short && shortage.value === 0n) return { k: 'ok', text: '계산상 충족' };
  return unknown;
}
export function twinMaterialSummary(requirements = {}) {
  if (requirements.state === 'BLOCKED_BOM') return { k: 'warn', text: 'BOM 없음 · 계산 보류' };
  const items = Array.isArray(requirements.items) ? requirements.items : [];
  const statuses = items.map(twinMaterialStatus), short = statuses.filter(item => item.k === 'bad').length;
  if (short) return { k: 'bad', text: `부족 ${short}품목` };
  const complete = requirements.state === 'CALCULATED' && requirements.full_coverage === true
    && requirements.unknown_shortage_count === 0 && requirements.unresolved_count === 0
    && statuses.length > 0 && statuses.every(item => item.k === 'ok');
  return complete ? { k: 'ok', text: '계산상 충족' } : { k: 'warn', text: items.length ? '부분 계산 · 미확인 있음' : '계산 대상 미확인' };
}
export function twinPromiseStatus(value) {
  return isNum(value) && value >= 0 ? value > 0 ? { text: '진행 중', tone: 'info' }
    : { text: '원장상 완료', tone: 'ok' } : { text: '미확인', tone: 'warn' };
}
const TYPE_ALIASES = { supplier: 'Supplier', quotation: 'SupplierOffer', item: 'InternalItem', lot: 'PartLot',
  production_job: 'ProductionJob', configuration: 'ProductConfiguration', bom: 'BOMSnapshot', bom_line: 'BOMLine',
  bom_application: 'BOMApplication', customer: 'Customer', contract_line: 'ContractLine', delivery_round: 'DeliveryRound',
  source_document: 'SourceDocument', source_snapshot: 'SourceSnapshot', source_row: 'SourceRow',
  approval: 'ApprovalGrant', document_version: 'DocumentVersion' };
export function twinNodeType(node) {
  const type = node.type || node.attributes?.kind_label || node.entity_ref?.entity_type || 'Unknown';
  return Object.hasOwn(TYPE_ALIASES, type) ? TYPE_ALIASES[type] : type;
}
const EVENT_NAMES = { OPENING_BALANCE_IMPORTED: '합성 기초 원장 이관', BomRegistered: 'BOM 원본 등록 반영',
  ProcurementPosted: '구매 흐름 문서 반영', EvidenceDocumentPosted: '근거 문서 반영' };
export function twinRecentEvents(events, ledgerVersion) {
  if (!Array.isArray(events)) return [];
  return events.filter(event => Number.isSafeInteger(event?.ledger_version) && event.ledger_version > 0
      && event.ledger_version <= ledgerVersion).map((event, index) => ({ event, index }))
    .sort((a, b) => b.event.ledger_version - a.event.ledger_version || a.index - b.index).slice(0, 5)
    .map(({ event }) => ({ ...event, summary: typeof event.summary === 'string' && event.summary.trim()
      ? event.summary : Object.hasOwn(EVENT_NAMES, event.event_type) ? EVENT_NAMES[event.event_type] : '원장 사건' }));
}
export function twinEventTime(value) {
  const match = typeof value === 'string' && /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match) return '—';
  const [year, month, day, hour, minute, second] = match.slice(1).map(Number), date = new Date(value);
  if (month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate()
      || hour > 23 || minute > 59 || second > 59 || !Number.isFinite(date.getTime())) return '—';
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date);
}
export function twinOntologyCounts(ontology = {}) {
  return Object.fromEntries(['classes', 'relations', 'shapes'].map(key => {
    const count = ontology.counts?.[key] ?? ontology.meta?.counts?.[key];
    return [key, Array.isArray(ontology[key]) ? ontology[key].length : Number.isSafeInteger(count) && count >= 0 ? count : null];
  }));
}
export function twinOriginNote(meta = {}, requirements = {}) {
  const inventory = meta.data_origins?.inventory === 'mixed_sources' ? '합성 기초 재고 + 개발 입하·검사'
    : meta.data_origins?.inventory === 'synthetic_opening_balance' ? '합성 기초 재고' : '재고 출처 미확인';
  return `${meta.synthetic === true ? '합성 초기 이관' : '기초 이관 출처 미확인'} · ${requirements.bom_id ? '실제 BOM 원문 등록' : 'BOM 원문 미등록'} · ${inventory}. 원문 등록은 제조 기술검수 승인이 아니며 제조 가능성은 미확인입니다.${requirements.has_demo_assumptions ? ' 적용 수량·품목은 시연 가정입니다.' : ''}`;
}

// 원장 노드 종류 → 현장. 목록에 없는 종류(근거·승인·원본 등)는 가운데 '근거·기준' 판에 둔다.
const ZONE_OF = {
  Supplier: 'sup', SupplierOffer: 'sup',
  PurchaseRequest: 'sup', PurchaseOrder: 'sup', PurchaseOrderLine: 'sup', SupplierConfirmation: 'sup',
  InternalItem: 'mat', PartLot: 'mat',
  PartsReceiptLine: 'mat', InspectionResult: 'mat',
  ProductionJob: 'line', ProductConfiguration: 'line', BOMSnapshot: 'line', BOMLine: 'line', BOMApplication: 'line',
  Customer: 'cust', ContractLine: 'cust', DeliveryRound: 'cust',
};
const FACILITIES = [
  { key: 'sup', name: '공급·견적', module: '구매', pos: [-66, -4], size: [18, 14], h: 6 },
  { key: 'mat', name: '자재 창고', module: '자재·재고', pos: [-38, -24], size: [20, 16], h: 8 },
  { key: 'line', name: '생산라인', module: '생산', pos: [-2, -26], size: [32, 12], h: 4 },
  { key: 'fg', name: '완제품 창고', module: '완성품 입고', pos: [32, -22], size: [18, 16], h: 7 },
  { key: 'ship', name: '출하장', module: '출하·운송', pos: [44, 12], size: [16, 13], h: 5 },
  { key: 'cust', name: '고객사', module: '계약·납품', pos: [6, 26], size: [24, 15], h: 10 },
];
const CORE = { key: 'core', name: '근거·승인·기준', pos: [-32, 18] };
// 공정 구역 표시: 번호와 색(twin.css의 --tw-proc-* 토큰). 이름표는 건물 옆(l·r)으로 띄운다.
const PROC_NO = { sup: '01', mat: '02', line: '03', fg: '04', ship: '05', cust: '06' };
const TAG_SIDE = { sup: 'l', mat: 'r', line: 'r', fg: 'l', ship: 'l', cust: 'r' };
const TAG_FRONT = { fg: true, ship: true };
const FLOWS = [['sup', 'mat'], ['mat', 'line'], ['line', 'fg'], ['fg', 'ship'], ['ship', 'cust']];
const SHAPE_OF = { design: 'std', doc: 'doc', party: 'party', phys: 'real', event: 'evt' };
const SHAPE_NAME = { std: '설계·기준', doc: '문서', party: '주체', real: '실물(LOT)', evt: '사건', agg: '축약 묶음' };
const AGG_LIMIT = 6;
// With many ledger nodes the node name labels cover the scene, so they start
// hidden; selected and answer-evidence nodes keep their label (CSS .on).
export const NAMES_AUTO_LIMIT = 40;
export const namesShownByDefault = shownCount => shownCount <= NAMES_AUTO_LIMIT;
const edgeSource = edge => edge.source_node_id || edge.source;
const edgeTarget = edge => edge.target_node_id || edge.target;
const edgeId = edge => edge.edge_id || edge.id;
const edgePredicate = edge => edge.relation_type || edge.ontology_predicate || edge.predicate || '';
export function prepareTwinGraph(ledgerNodes, ledgerEdges) {
  const shown = [], displayOf = Object.create(null), byZoneType = new Map();
  for (const node of ledgerNodes) {
    const type = twinNodeType(node), zone = Object.hasOwn(ZONE_OF, type) ? ZONE_OF[type] : 'core';
    const key = JSON.stringify([zone, type]);
    if (!byZoneType.has(key)) byZoneType.set(key, { zone, type, nodes: [] });
    byZoneType.get(key).nodes.push(node);
  }
  for (const { zone, type, nodes: list } of byZoneType.values()) {
    const visible = list.length > AGG_LIMIT ? list.slice(0, AGG_LIMIT - 1) : list;
    for (const node of visible) {
      const id = node.node_id || node.id;
      displayOf[id] = id;
      shown.push({ id, zone, type, node, shape: SHAPE_OF[node.node_type] || 'std',
        title: node.attributes?.display || node.label || id, label: node.label || node.display_id || id });
    }
    if (list.length > visible.length) {
      const rest = list.slice(visible.length), aggId = `agg:${zone}:${type}`;
      for (const node of rest) displayOf[node.node_id || node.id] = aggId;
      shown.push({ id: aggId, zone, type, aggregate: true, members: rest.map(node => node.node_id || node.id), shape: 'agg',
        title: `${type} 외 ${rest.length}개`, label: `${type} ×${rest.length}` });
    }
  }
  const edgeMap = new Map();
  for (const edge of ledgerEdges) {
    const source = displayOf[edgeSource(edge)], target = displayOf[edgeTarget(edge)], predicate = edgePredicate(edge);
    if (!source || !target || !edgeId(edge) || !predicate) continue;
    const key = JSON.stringify([source, target, predicate]);
    if (!edgeMap.has(key)) edgeMap.set(key, { id: key, source, target, predicate,
      label: edge.label || predicate, ledgerIds: [], ledgerEdges: [], internal: source === target });
    edgeMap.get(key).ledgerIds.push(edgeId(edge));
    edgeMap.get(key).ledgerEdges.push(edge);
  }
  return { shown, displayOf, shownEdges: [...edgeMap.values()] };
}
export function twinHighlight(meta, prepared, input = {}) {
  const fields = [['ledgerVersion', 'ledger_version'], ['projectionVersion', 'projection_version'],
    ['snapshotId', 'snapshot_id'], ['companyId', 'company_id'], ['identityRevision', 'identity_revision']];
  if (fields.some(([request, current]) => input[request] == null || input[request] !== meta[current])) return null;
  const nodeIds = Array.isArray(input.nodeIds) ? input.nodeIds : [], edgeIds = new Set(Array.isArray(input.edgeIds) ? input.edgeIds : []);
  const nodes = new Set(nodeIds.map(id => prepared.displayOf[id]).filter(Boolean)), edges = new Set(), matchedIds = new Set();
  for (const edge of prepared.shownEdges) {
    const matched = edge.ledgerIds.filter(id => edgeIds.has(id));
    if (!matched.length) continue;
    edges.add(edge.id); nodes.add(edge.source); nodes.add(edge.target);
    matched.forEach(id => matchedIds.add(id));
  }
  return { nodes, edges, ledgerEdgeIds: matchedIds };
}
export function twinRelationEvidence(prepared, displayId) {
  return prepared.shownEdges.filter(edge => edge.source === displayId || edge.target === displayId)
    .flatMap(edge => edge.ledgerEdges.map(value => ({ id: edgeId(value), source: edgeSource(value),
      predicate: edgePredicate(value), target: edgeTarget(value), internal: edge.internal })));
}
// 질의 근거 노드가 놓인 공정과 노드 수. 많은 순(같으면 공정 순서)으로, 가운데 '근거·기준' 판은 제외한다.
export function twinAnswerZones(prepared, nodeIds) {
  const order = FACILITIES.map(f => f.key), count = new Map();
  for (const id of nodeIds || []) {
    const zone = prepared.shown.find(n => n.id === id)?.zone;
    if (zone && zone !== 'core') count.set(zone, (count.get(zone) || 0) + 1);
  }
  return new Map([...count].sort((a, b) => b[1] - a[1] || order.indexOf(a[0]) - order.indexOf(b[0])));
}
const KEY_LABEL = {
  item_id: '품목', lot_id: 'LOT', on_hand_qty: '보유', reserved_other_qty: '타 수요 예약', available_qty: '가용',
  unit: '단위', scope: '범위', location: '위치', good_qty: '양품', defect_qty: '불량', pending_judgement_qty: '판정대기',
  shipped_qty: '출하', arrived_qty: '도착', quantity: '수량', qty: '수량', supplier_id: '공급사', price: '단가',
  unit_price: '단가', lead_time_days: '리드타임(일)', moq: 'MOQ', round_id: '회차', job_id: '작업지시',
  required_qty: '소요', planned_required_qty: '투입 기준 소요', shortage_qty: '부족', planned_shortage_qty: '투입 기준 부족',
  state: '상태', quantity_per_unit: '1대당', status: '상태', recorded_at: '기록 시각', effective_at: '유효 시각',
};

function tokens() {
  const css = getComputedStyle(document.documentElement);
  const t = name => css.getPropertyValue(name).trim() || '#888888';
  return {
    bg: t('--bg'), surface: t('--surface'), line: t('--line'), line2: t('--line2'), fg: t('--fg'), fg3: t('--fg3'),
    accent: t('--accent'), ok: t('--ok'), okSoft: t('--ok-soft'), warn: t('--warn'), warnSoft: t('--warn-soft'),
    bad: t('--bad'), badSoft: t('--bad-soft'), edge: t('--edge'), node: t('--node'), nodeLine: t('--node-line'),
    grid: t('--grid'), v1: t('--v1'), v2: t('--v2'), v3: t('--v3'), v4: t('--v4'), vgray: t('--vgray'), raised: t('--raised'),
    proc: Object.fromEntries(FACILITIES.map(f => [f.key, t(`--tw-proc-${f.key}`)])),
    truck: t('--tw-truck'), tyre: t('--tw-tyre'), steel: t('--tw-steel'), glass: t('--tw-glass'), lamp: t('--tw-lamp'),
    dark: document.documentElement.getAttribute('data-theme') === 'dark',
  };
}

/* ---------- 원장 이력 재생 (화면 연출만; 원장·질의 변경 없음) ---------- */
// Confirmed ledger events from BOM registration onward, each mapped to the
// process zone it belongs to. Quantities come from the posted documents in the
// same snapshot; the running arrival total ends at the current ledger value.
const REPLAY_KIND = { QUOTE: ['sup', '견적'], PR: ['sup', '구매 요청'], PO: ['sup', '내부 발주'], SUPPLY_CONFIRMATION: ['sup', '공급확인 · 모의'],
  RECEIPT: ['mat', '입하'], IQC: ['mat', '수입검사'], CANCEL: ['sup', '잔여 취소'], PRODUCTION: ['line', '생산 실적'],
  FG_RECEIPT: ['fg', '완성품 입고'], SHIPMENT: ['ship', '출하'], ARRIVAL: ['cust', '고객 도착'],
  SCENARIO: ['core', '일정 가정'], SUBSTITUTION: ['core', '대체 적용 승인'] };
const COHORT_STAGE = { FG_RECEIPT: ['fg', '기초 보드 입고'], SHIPMENT: ['ship', '기초 보드 출하'], ARRIVAL: ['cust', '기초 보드 도착'] };
function replayDocLabel(kind, id) {
  const prefix = { QUOTE: 'QT', PR: 'PR', PO: 'PO', SUPPLY_CONFIRMATION: 'SC', RECEIPT: 'RC', IQC: 'IQC', CANCEL: 'CX', PRODUCTION: 'PD',
    FG_RECEIPT: 'FG', SHIPMENT: 'SH', ARRIVAL: 'AR', SCENARIO: 'SCN', COHORT: 'CH', SUBSTITUTION: 'SB' }[kind] || kind;
  return `${prefix}-${String(id || '').replace(/[^0-9a-z]/gi, '').slice(0, 4).toUpperCase()}`;
}
// Planned input boards = ceil(remaining good boards / plan yield), the same
// exact ceiling as the server's material requirement (service/materials.py).
export function plannedInputQty(remaining, planYield) {
  const m = /^(\d+)(?:\.(\d{1,6}))?$/.exec(String(planYield ?? ''));
  if (!m || !Number.isSafeInteger(remaining) || remaining < 0) return null;
  const den = 10 ** (m[2]?.length || 0), num = Number(m[1] + (m[2] || ''));
  if (!num || num > den) return null;
  return Math.floor((remaining * den + num - 1) / num);
}

/** Shortage of one requirement item at a point in time (planned-input basis). */
function itemShortageAt(item, planYield, remaining, available) {
  const perUnit = Number(item?.quantity_per_unit), extra = Number(item?.substitute_qty ?? 0);
  const planned = plannedInputQty(remaining, planYield);
  if (planned == null || !Number.isFinite(perUnit) || !Number.isFinite(extra) || !Number.isFinite(available)) return null;
  const required = planned * perUnit, have = available + extra;
  return { item_id: item.item_id, item_name: typeof item.item_name === 'string' ? item.item_name : '', required, available: have,
    shortage: Math.max(required - have, 0) };
}

export function buildTwinReplay(snapshot) {
  const events = (Array.isArray(snapshot?.events) ? snapshot.events : []).filter(e => e && Number.isSafeInteger(e.ledger_version))
    .slice().sort((a, b) => a.ledger_version - b.ledger_version);
  const start = events.findIndex(e => e.event_type === 'BomRegistered');
  if (start < 0) return null;
  const docs = new Map(((snapshot.procurement || {}).documents || []).map(d => [d.id, d]));
  const steps = [];
  for (const e of events.slice(start)) {
    let zone = 'core', title = '', detail = '', arrived = 0;
    const delta = {};
    const add = (key, value) => { if (Number.isFinite(value) && value) delta[key] = (delta[key] || 0) + value; };
    if (e.event_type === 'BomRegistered') { zone = 'line'; title = 'BOM 원본 등록 반영'; detail = '소요 계산 시작'; }
    else if (e.event_type === 'InventoryReserved' || e.reservation) {
      const r = e.reservation || {}; zone = 'mat'; title = `다른 주문 예약 ${r.demand_ref || ''}`.trim();
      if (r.item_id === snapshot.summary?.item_id) { add('reserved_qty', Number(r.quantity)); add('available_qty', Number(r.available_after) - Number(r.available_before)); }
      detail = `${r.item_id || ''} ${r.quantity ?? ''}개 · 가용 ${r.available_before ?? '?'}→${r.available_after ?? '?'}`.trim();
    } else if (e.document_kind && e.document_ref?.id) {
      const doc = docs.get(e.document_ref.id), c = doc?.content || {};
      const map = e.document_kind === 'COHORT' ? COHORT_STAGE[c.stage] : REPLAY_KIND[e.document_kind];
      if (!map) continue;
      [zone, title] = map; title = `${title} ${replayDocLabel(e.document_kind, e.document_ref.id)}`;
      const boards = Array.isArray(c.board_serials) ? c.board_serials.length : 0;
      detail = boards ? `${boards}대` : e.document_kind === 'IQC' && c.passed_qty != null ? `합격 ${c.passed_qty}개 · 가용 반영`
        : c.quantity != null ? `${c.quantity}${e.document_kind === 'COHORT' ? '대' : '개'}` : c.confirmed_qty != null ? `${c.confirmed_qty}개` : '';
      if (e.document_kind === 'ARRIVAL') arrived = boards;
      if (e.document_kind === 'COHORT' && c.stage === 'ARRIVAL') arrived = Number(c.quantity) || 0;
      // Same-snapshot quantity moves, so the zone tags can show each point in time.
      const moved = e.document_kind === 'COHORT' ? Number(c.quantity) || 0 : boards;
      const stage = e.document_kind === 'COHORT' ? c.stage : e.document_kind;
      const focus = snapshot.summary?.item_id, item = c.resolved?.item_id;
      if (e.document_kind === 'RECEIPT' && item === focus) add('on_hand_qty', Number(c.quantity));
      if (e.document_kind === 'IQC' && (item === focus || c.resolved?.item_id == null)) add('available_qty', Number(c.passed_qty));
      if (e.document_kind === 'PRODUCTION') {
        add('good_qty', moved); add('production_remaining_qty', -moved); add('good_not_received_qty', moved);
        if (item === focus) { const used = Number(c.resolved?.consumed_qty); add('on_hand_qty', -used); add('available_qty', -used); }
      }
      if (stage === 'FG_RECEIPT') { add('fg_received_qty', moved); add('good_not_received_qty', -moved); add('warehouse_remaining_qty', moved); }
      if (stage === 'SHIPMENT') { add('shipped_qty', moved); add('warehouse_remaining_qty', -moved); add('transit_qty', moved); }
      if (stage === 'ARRIVAL') { add('arrived_qty', moved); add('transit_qty', -moved); add('promise_remaining_qty', -moved); }
    } else continue;
    steps.push({ version: e.ledger_version, at: e.recorded_at, zone, title, detail, arrived, delta });
  }
  if (!steps.length) return null;
  const summary = snapshot.summary || {}, promise = Number(summary.promise_qty ?? summary.promised_qty);
  const total = steps.reduce((sum, step) => sum + step.arrived, 0);
  let arrived = Number(summary.arrived_qty) - total;
  for (const step of steps) { arrived += step.arrived; step.arrivedTotal = Number.isFinite(arrived) ? arrived : null;
    step.remaining = Number.isFinite(arrived) && Number.isFinite(promise) ? promise - arrived : null; }
  // Zone values at each step: current summary minus the moves still ahead.
  const keys = [...new Set(steps.flatMap(step => Object.keys(step.delta)))];
  const ahead = Object.fromEntries(keys.map(key => [key, steps.reduce((sum, step) => sum + (step.delta[key] || 0), 0)]));
  for (const step of steps) {
    for (const key of keys) ahead[key] -= step.delta[key] || 0;
    step.state = Object.fromEntries(keys.filter(key => Number.isFinite(Number(summary[key]))).map(key => [key, Number(summary[key]) - ahead[key]]));
  }
  // The focus part (summary.item_id) at each step: required, available and shortage.
  const requirements = snapshot.material_requirements || {};
  const focus = requirements.state !== 'BLOCKED_BOM' && Array.isArray(requirements.items)
    ? requirements.items.find(item => item.item_id === summary.item_id) : null;
  for (const step of steps) {
    const remaining = step.state.production_remaining_qty ?? Number(summary.production_remaining_qty);
    const available = step.state.available_qty ?? Number(summary.available_qty);
    step.material = focus ? itemShortageAt(focus, requirements.basis?.plan_yield, remaining, available) : null;
  }
  return { steps, startArrived: Number(summary.arrived_qty) - total, promise: Number.isFinite(promise) ? promise : null };
}

export function mountTwin(container, { snapshot, ontology, labelFor = id => id, onSelect = () => {}, onAsk = () => {}, onGraph = () => {} } = {}) {
  container.replaceChildren();
  const summary = snapshot.summary || {};
  const meta = snapshot.meta || {};
  const requirements = snapshot.material_requirements || {};
  const ledgerNodes = snapshot.graph?.data?.nodes || [];
  const ledgerEdges = snapshot.graph?.data?.edges || [];
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  /* ---------- 화면 뼈대 ---------- */
  const view = element('div', 'twin2-view');
  const legend = element('div', 'twin2-legend');
  legend.setAttribute('aria-hidden', 'true');
  for (const [cls, text] of [['', '설계·기준'], ['s-doc', '문서'], ['s-party', '주체'], ['s-real', '실물(LOT)'], ['s-evt', '사건'], ['s-bad', '부족'], ['s-warn', '확인 필요'], ['s-agg', '축약 묶음']]) {
    const span = element('span'); span.append(element('i', cls), document.createTextNode(text)); legend.append(span);
  }
  const tools = element('aside', 'twin2-tools'); tools.setAttribute('aria-label', '디지털 트윈 보기 설정');
  const side = element('aside', 'twin2-side'); side.setAttribute('aria-label', '원장 진행 현황');
  const detail = element('section', 'twin2-detail'); detail.hidden = true; detail.setAttribute('aria-live', 'polite');
  const tip = element('div', 'twin2-tip'); tip.hidden = true;
  container.append(view, legend, tools, side, detail);
  view.append(tip);

  /* ---------- 원장 그래프 → 표시 노드 (축약 포함) ---------- */
  const prepared = prepareTwinGraph(ledgerNodes, ledgerEdges);
  const { shown, displayOf, shownEdges } = prepared;
  const namesOn = namesShownByDefault(shown.filter(n => !n.aggregate).length);
  const shownById = Object.fromEntries(shown.map(n => [n.id, n]));

  /* ---------- 원장 값으로 정하는 노드 상태 ---------- */
  const reqItems = requirements.items || [];
  // Shortage alerts and recent changes (same snapshot; see material_watch.js).
  const alerts = materialAlerts(snapshot), alertRings = [];
  const alertFor = n => alerts.find(a => a.item_entity_id === n.id || a.item_id === n.label) || null;
  function statusOf(n) {
    if (n.aggregate) return null;
    if (n.type === 'InternalItem') {
      if (requirements.state === 'BLOCKED_BOM') return { k: 'warn', text: 'BOM 없음 · 계산 보류' };
      const item = reqItems.find(row => row.item_entity_id === n.id || row.item_id === n.label);
      if (!item) return null;
      return twinMaterialStatus(item);
    }
    if (n.type === 'DeliveryRound' && isNum(summary.promise_remaining_qty) && summary.promise_remaining_qty > 0) return { k: 'warn', text: `남은 약속 ${fmt(summary.promise_remaining_qty)}` };
    if (n.type === 'ProductionJob' && isNum(summary.pending_judgement_qty) && summary.pending_judgement_qty > 0) return { k: 'warn', text: `판정대기 ${fmt(summary.pending_judgement_qty)}` };
    if (n.type === 'ProductConfiguration') {
      if (summary.bom_status && summary.bom_status !== 'POSTED_SOURCE') return { k: 'warn', text: 'BOM 미등록' };
      if (requirements.state === 'PARTIAL') return { k: 'warn', text: `미확인 ${fmt(requirements.unknown_shortage_count)}행` };
    }
    return null;
  }

  /* ---------- three.js ---------- */
  let T = tokens();
  // Process-zone floor strength. Dark mode uses neon process colors, so the floor and rim show more of them.
  const padOpacity = on => on ? (T.dark ? 0.42 : 0.26) : (T.dark ? 0.24 : 0.13);
  const rimOpacity = on => on ? 1 : (T.dark ? 0.95 : 0.7);
  let renderer, labels, scene, camera, controls;
  const themed = [];
  const tc = key => typeof key === 'function' ? key(T) : T[key];
  const tm = (material, key) => { themed.push({ material, key }); material.color.set(tc(key)); return material; };
  const mix = (a, b, k) => '#' + new THREE.Color(a).lerp(new THREE.Color(b), k).getHexString();
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch (error) {
    view.append(element('div', 'twin2-fallback', '이 브라우저에서는 3D를 그릴 수 없습니다. 오른쪽 진행 현황과 질의는 그대로 쓸 수 있습니다.'));
  }
  const nodes3d = {}, facilityHits = [], facilityPads = {}, facilityTags = {}, edges3d = [], flowDots = [], pulses = [];
  const tagAnchors = [], truckRig = { truck: null, halo: null, road: null, yaw: null, t: 0 };
  const nodePos = {};
  if (renderer) {
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    view.append(renderer.domElement);
    labels = new CSS2DRenderer(); labels.domElement.className = namesOn ? 'twin2-labels' : 'twin2-labels hide-names'; view.append(labels.domElement);
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(40, 1, 0.5, 700);
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.maxPolarAngle = Math.PI * 0.49; controls.minDistance = 30; controls.maxDistance = 240;
    scene.add(new THREE.HemisphereLight(0xffffff, 0xb7bfcc, 0.9));
    const sun = new THREE.DirectionalLight(0xffffff, 0.5); sun.position.set(40, 90, 30); scene.add(sun);

    const floor = new THREE.Mesh(new THREE.PlaneGeometry(190, 130), tm(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.55 }), 'raised'));
    floor.rotation.x = -Math.PI / 2; scene.add(floor);
    const grid = new THREE.GridHelper(190, 38); grid.position.y = 0.02;
    grid.material.vertexColors = false; grid.material.transparent = true; grid.material.opacity = 0.9;
    tm(grid.material, 'line'); scene.add(grid);

    const box = (w, h, d, op, fillKey = 'surface', edgeKey = 'nodeLine') => {
      const group = new THREE.Group();
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), tm(new THREE.MeshStandardMaterial({ roughness: 0.85, transparent: op < 1, opacity: op }), fillKey));
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), tm(new THREE.LineBasicMaterial({}), edgeKey));
      group.add(mesh, edges); group.position.y = h / 2; return group;
    };
    for (const f of FACILITIES) {
      const group = new THREE.Group(); group.position.set(f.pos[0], 0, f.pos[1]);
      const [w, d] = f.size;
      // 공정 색: 반투명은 유지하고 바닥·테두리·건물에 같은 계열 색을 옅게 섞는다.
      const pc = t => t.proc[f.key], fillC = t => mix(t.surface, t.proc[f.key], t.dark ? 0.55 : 0.3);
      const edgeC = t => mix(t.nodeLine, t.proc[f.key], 0.8), solidC = t => mix(t.surface, t.proc[f.key], t.dark ? 0.62 : 0.38);
      const pad = new THREE.Mesh(new THREE.PlaneGeometry(w, d), tm(new THREE.MeshBasicMaterial({ transparent: true, opacity: padOpacity(false), depthWrite: false }), pc));
      pad.rotation.x = -Math.PI / 2; pad.position.y = 0.05;
      const rim = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(w, 0.01, d)), tm(new THREE.LineBasicMaterial({ transparent: true, opacity: rimOpacity(false) }), pc));
      rim.position.y = 0.06;
      const stripe = new THREE.Mesh(new THREE.PlaneGeometry(w, 0.9), tm(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85, depthWrite: false }), pc));
      stripe.rotation.x = -Math.PI / 2; stripe.position.set(0, 0.07, d / 2 - 0.45);
      const bw = w + 1.6, bd = d + 1.6;
      const border = new THREE.Line(new THREE.BufferGeometry().setFromPoints([[-bw / 2, -bd / 2], [bw / 2, -bd / 2], [bw / 2, bd / 2], [-bw / 2, bd / 2], [-bw / 2, -bd / 2]].map(([x, z]) => new THREE.Vector3(x, 0.06, z))),
        tm(new THREE.LineDashedMaterial({ dashSize: 0.9, gapSize: 0.7, transparent: true, opacity: 0.55 }), pc));
      border.computeLineDistances();
      facilityPads[f.key] = { pad: pad.material, rim: rim.material };
      group.add(pad, rim, stripe, border);
      if (f.key === 'line') {
        group.add(box(30, 1, 3.4, 1, solidC, edgeC));
        for (const x of [-9, 0, 9]) { const m = box(4.4, 3.2, 5.4, 0.6, fillC, edgeC); m.position.x = x; m.position.y += 1; group.add(m); }
      } else if (f.key === 'fg') {
        for (const z of [-4.5, 0, 4.5]) { const r = box(14, 6, 2.2, 0.35, fillC, edgeC); r.position.z = z; group.add(r); }
      } else if (f.key === 'ship') {
        const dock = box(9, f.h, 8, 0.5, fillC, edgeC); dock.position.x = -2.5; group.add(dock);
      } else {
        group.add(box(w - 4, f.h, d - 4, 0.5, fillC, edgeC));
      }
      const hit = new THREE.Mesh(new THREE.BoxGeometry(w, f.h + 2, d), new THREE.MeshBasicMaterial({ visible: false }));
      hit.position.y = (f.h + 2) / 2; hit.userData.facility = f.key; group.add(hit); facilityHits.push(hit);
      const anchor = element('div', `twin2-anc ${TAG_SIDE[f.key]}`), tag = element('div', 'twin2-tag');
      const top = element('div', 'top'), name = element('span', 'n'), no = element('i', '', PROC_NO[f.key]);
      name.append(no, document.createTextNode(f.name), element('small', '', f.module));
      const pill = element('span', 'pill mute'); top.append(name, pill);
      const value = element('span', 'v'), sub = element('span', 's'), bar = element('span', 'b'), fill = element('i');
      const info = element('div', 'twin2-ainfo'); info.hidden = true;
      bar.append(fill); tag.append(top, value, sub, bar, info); anchor.append(tag);
      facilityTags[f.key] = { pill, value, sub, bar, fill, info, anchor };
      const label = new CSS2DObject(anchor); group.add(label);
      const lo = { key: f.key, label, anchor, tag, w, d, h: f.h, front: !!TAG_FRONT[f.key], side: TAG_SIDE[f.key], ov: {}, nx: 0 };
      tagAnchors.push(lo); setTagSide(lo, lo.side);
      scene.add(group);
    }
    // 물건이 움직이는 방향 (장식). 수량·속도 의미 없음.
    const dotTexture = (() => {
      const c = document.createElement('canvas'); c.width = c.height = 64;
      const x = c.getContext('2d'), g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
      g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.45, 'rgba(255,255,255,.85)'); g.addColorStop(1, 'rgba(255,255,255,0)');
      x.fillStyle = g; x.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
    })();
    const dot = (key, size) => { const s = new THREE.Sprite(tm(new THREE.SpriteMaterial({ map: dotTexture, transparent: true, depthWrite: false }), key)); s.scale.set(size, size, 1); return s; };
    const facPos = Object.fromEntries(FACILITIES.map(f => [f.key, f.pos]));
    for (const [a, b] of FLOWS) {
      const A = new THREE.Vector3(facPos[a][0], 0.3, facPos[a][1]), B = new THREE.Vector3(facPos[b][0], 0.3, facPos[b][1]);
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([A, B]), tm(new THREE.LineDashedMaterial({ dashSize: 1.2, gapSize: 1, transparent: true, opacity: 0.6 }), 'v2'));
      line.computeLineDistances(); scene.add(line);
      const curve = new THREE.LineCurve3(A, B);
      for (const t0 of [0, 0.5]) { const d = dot('v2', 1.6); scene.add(d); flowDots.push({ d, curve, t: t0 }); }
    }
    // 출하장 ↔ 고객사를 오가는 트럭 (장식). 실제 운송 사건·위치·속도가 아니다.
    {
      const shipPos = facPos.ship, custPos = facPos.cust;
      const road = new THREE.CatmullRomCurve3([new THREE.Vector3(shipPos[0] - 4, 0.4, shipPos[1] + 4), new THREE.Vector3((shipPos[0] + custPos[0]) / 2 + 6, 0.4, (shipPos[1] + custPos[1]) / 2 + 8), new THREE.Vector3(custPos[0] + 12, 0.4, custPos[1] + 2)]);
      const pts = road.getPoints(40).map(v => new THREE.Vector3(v.x, 0.08, v.z));
      const shipC = t => t.proc.ship;
      scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), tm(new THREE.LineBasicMaterial({ transparent: true, opacity: 0.35 }), shipC)));
      const band = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 40, 1.1, 6, false), tm(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.09, depthWrite: false }), shipC));
      band.scale.y = 0.05; band.position.y = 0.04; scene.add(band);
      const truck = new THREE.Group();
      const part = (w, h, d, key, x, y, z) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), tm(new THREE.MeshStandardMaterial({ roughness: 0.6 }), key)); m.position.set(x, y + h / 2, z); truck.add(m); return m; };
      const edgeT = t => mix(t.proc.ship, '#000000', 0.18);
      part(8.4, 0.35, 2.2, 'steel', 0.1, 0.4, 0); part(0.25, 0.45, 2.7, 'steel', 4.2, 0.35, 0);
      const wheel = new THREE.CylinderGeometry(0.62, 0.62, 0.5, 20), hub = new THREE.CylinderGeometry(0.28, 0.28, 0.52, 14);
      for (const x of [-2.9, -1.6, 2.85]) for (const z of [-1.22, 1.22]) {
        const wh = new THREE.Mesh(wheel, tm(new THREE.MeshStandardMaterial({ roughness: 0.9 }), 'tyre')); wh.rotation.x = Math.PI / 2; wh.position.set(x, 0.22, z); truck.add(wh);
        const hb = new THREE.Mesh(hub, tm(new THREE.MeshStandardMaterial({ roughness: 0.4 }), 'steel')); hb.rotation.x = Math.PI / 2; hb.position.set(x, 0.22, z); truck.add(hb);
      }
      const cargo = box(5.6, 3, 2.7, 1, t => mix(t.truck, t.proc.ship, t.dark ? 0.42 : 0.3), edgeT); cargo.position.set(-1, cargo.position.y + 0.75, 0); truck.add(cargo);
      const strip = box(5.62, 0.32, 2.74, 1, shipC, shipC); strip.position.set(-1, 2.05, 0); truck.add(strip);
      part(0.05, 2.7, 0.06, edgeT, -3.82, 0.9, 0);
      part(1.9, 1.5, 2.6, shipC, 2.95, 0.75, 0); part(1.6, 1.25, 2.5, shipC, 2.75, 2.25, 0);
      part(0.06, 0.95, 2.2, 'glass', 3.57, 2.4, 0);
      for (const z of [-1.26, 1.26]) part(1.0, 0.75, 0.04, 'glass', 2.85, 2.55, z);
      for (const z of [-0.9, 0.9]) part(0.06, 0.3, 0.5, 'lamp', 3.92, 1.3, z);
      const halo = new THREE.Mesh(new THREE.CircleGeometry(4.2, 40), tm(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.16, depthWrite: false }), shipC));
      halo.rotation.x = -Math.PI / 2; halo.position.y = -0.36; halo.scale.set(1.25, 0.8, 1); truck.add(halo);
      scene.add(truck); Object.assign(truckRig, { truck, halo, road });
      truck.position.copy(road.getPoint(0));
    }

    /* 온톨로지(원장 그래프) 층: 현장별 판 위 격자 */
    const ontoGroup = new THREE.Group(), plateGroup = new THREE.Group(), edgeGroup = new THREE.Group();
    scene.add(ontoGroup, plateGroup, edgeGroup);
    const centers = { core: CORE.pos, ...facPos };
    for (const zone of Object.keys(centers)) {
      const list = shown.filter(n => n.zone === zone);
      if (!list.length) continue;
      const core = zone === 'core', cols = Math.min(list.length, core ? 6 : 4), rows = Math.ceil(list.length / cols), sp = 7, y = core ? 36 : 26;
      const [cx, cz] = centers[zone];
      list.forEach((n, i) => {
        const c = i % cols, r = Math.floor(i / cols), inRow = r === rows - 1 ? list.length - r * cols : cols;
        nodePos[n.id] = new THREE.Vector3(cx + (c - (inRow - 1) / 2) * sp, y, cz + (r - (rows - 1) / 2) * sp);
      });
      const plate = new THREE.Mesh(new THREE.BoxGeometry(cols * sp + 1, 0.2, rows * sp + 3), tm(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.05, depthWrite: false }), 'accent'));
      plate.position.set(cx, y - 2, cz + 0.8); plateGroup.add(plate);
      const rim = new THREE.LineSegments(new THREE.EdgesGeometry(plate.geometry), tm(new THREE.LineBasicMaterial({ transparent: true, opacity: 0.8 }), 'line'));
      rim.position.copy(plate.position); plateGroup.add(rim);
      if (!core) {
        const f = FACILITIES.find(item => item.key === zone);
        const beam = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(cx, y - 2.1, cz), new THREE.Vector3(cx, f.h, cz)]),
          tm(new THREE.LineDashedMaterial({ dashSize: 0.8, gapSize: 0.8, transparent: true, opacity: 0.7 }), 'fg3'));
        beam.computeLineDistances(); plateGroup.add(beam);
      }
    }
    const GEO = { std: new THREE.BoxGeometry(2.1, 2.1, 2.1), doc: new THREE.OctahedronGeometry(1.65), evt: new THREE.SphereGeometry(1.3, 24, 16),
      real: new THREE.CylinderGeometry(1.4, 1.4, 1.5, 6), party: new THREE.CapsuleGeometry(0.9, 1.6, 4, 16), agg: new THREE.BoxGeometry(2.6, 1.2, 2.6) };
    for (const n of shown) {
      const fill = new THREE.MeshStandardMaterial({ roughness: 0.6, transparent: true });
      const geometry = GEO[n.shape] || GEO.std;
      const mesh = new THREE.Mesh(geometry, fill);
      if (n.shape === 'party') mesh.rotation.z = Math.PI / 2;
      mesh.position.copy(nodePos[n.id]); mesh.userData.node = n.id; ontoGroup.add(mesh);
      const hull = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ side: THREE.BackSide, transparent: true }));
      hull.scale.setScalar(1.13); mesh.add(hull);
      let dash = null;
      if (n.aggregate) { dash = new THREE.LineSegments(new THREE.EdgesGeometry(geometry), new THREE.LineDashedMaterial({ dashSize: 0.35, gapSize: 0.3, transparent: true })); dash.computeLineDistances(); dash.scale.setScalar(1.06); mesh.add(dash); }
      const label = element('div', 'twin2-nl'), em = element('em');
      label.append(element('b', '', n.label), element('span', '', n.aggregate ? n.title : n.title === n.label ? n.type : n.title), em);
      const obj = new CSS2DObject(label); obj.position.copy(nodePos[n.id]); obj.position.y -= 1.8; ontoGroup.add(obj);
      nodes3d[n.id] = { n, mesh, fill, hull, dash, label, em, status: statusOf(n), on: false, dim: false };
      const st = nodes3d[n.id].status;
      if (st) { em.textContent = st.text; em.className = st.k === 'bad' ? 'bad' : st.k === 'ok' ? 'ok' : ''; }
      // Items that need sourcing stand out: a pulsing ring, a pillar and a label that stays
      // visible when node names are hidden. A fresh change shows before → after.
      const alert = n.type === 'InternalItem' ? alertFor(n) : null;
      if (alert && (alert.state === 'short' || alert.change)) {
        const key = alert.state === 'short' ? 'bad' : 'ok', pos = nodePos[n.id];
        const ring = new THREE.Mesh(new THREE.RingGeometry(2.1, 2.6, 48), tm(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false }), key));
        ring.rotation.x = -Math.PI / 2; ring.position.copy(pos); ring.position.y -= 1.25; ontoGroup.add(ring); alertRings.push(ring);
        if (alert.state === 'short') {
          const pillar = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, 6, 12), tm(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85 }), 'bad'));
          pillar.position.copy(pos); pillar.position.y += 4.3; ontoGroup.add(pillar);
        }
        const tag = element('div', `twin2-alert ${alert.state === 'short' ? 'short' : 'ok'}`);
        tag.append(element('b', '', alert.state === 'short' ? `${alert.item_id} 부족 ${fmt(alert.shortage)}` : `${alert.item_id} 부족 없음`));
        if (alert.change) {
          const chip = element('span', `twin2-change ${alert.change.kind}`, changeText(alert.change));
          chip.style.animationDuration = `${Math.max(1, Math.round(alert.change.remainingMs / 1000))}s`; tag.append(chip);
        }
        const label = new CSS2DObject(tag); label.position.copy(pos); label.position.y += alert.state === 'short' ? 8.4 : 3.2; ontoGroup.add(label);
      }
    }
    for (const e of shownEdges) {
      // Internal/own relations remain in the detail evidence, without adding
      // new geometry to the supplied scene's aggregate representation.
      if (e.internal) continue;
      const a = nodePos[e.source], b = nodePos[e.target];
      if (!a || !b) continue;
      const mid = a.clone().lerp(b, 0.5); mid.y += 2 + a.distanceTo(b) * 0.08;
      const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
      const material = new THREE.LineBasicMaterial({ transparent: true });
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(28)), material);
      edgeGroup.add(line); edges3d.push({ e, curve, line, material });
    }
    for (let i = 0; i < 40; i++) { const d = dot('accent', 1.4); d.visible = false; scene.add(d); pulses.push({ d, edge: null, t: 0 }); }
    var layers = { site: [], onto: [ontoGroup, plateGroup], edge: [edgeGroup] };
    scene.children.forEach(child => { if (!child.isLight && ![ontoGroup, plateGroup, edgeGroup].includes(child) && !pulses.some(p => p.d === child)) layers.site.push(child); });
  }

  /* ---------- 강조 상태 ---------- */
  const state = { sel: null, facility: null, highlight: null, answerZones: null, answerMain: null };
  function activeSets() {
    if (state.sel) {
      const edges = new Set(shownEdges.filter(e => e.source === state.sel || e.target === state.sel).map(e => e.id));
      const nodes = new Set([state.sel]); shownEdges.forEach(e => { if (edges.has(e.id)) { nodes.add(e.source); nodes.add(e.target); } });
      return { nodes, edges };
    }
    if (state.highlight) return state.highlight;
    if (state.facility) {
      const nodes = new Set(shown.filter(n => n.zone === state.facility).map(n => n.id));
      return { nodes, edges: new Set(shownEdges.filter(e => nodes.has(e.source) || nodes.has(e.target)).map(e => e.id)) };
    }
    return null;
  }
  function styleNode(item) {
    const st = item.status;
    const tone = st ? ({ bad: [T.badSoft, T.bad], warn: [T.warnSoft, T.warn], ok: [T.okSoft, T.ok] }[st.k]) : null;
    item.fill.color.set(tone ? tone[0] : item.n.shape === 'real' ? T.vgray : T.node);
    item.fill.opacity = item.dim ? 0.2 : item.n.aggregate ? 0.6 : 1;
    item.hull.material.color.set(item.on ? T.accent : tone ? tone[1] : T.nodeLine);
    item.hull.material.opacity = item.dim ? 0.15 : 1;
    item.hull.visible = !item.n.aggregate || item.on;
    if (item.dash) { item.dash.visible = !item.on; item.dash.material.color.set(T.fg3); item.dash.material.opacity = item.dim ? 0.2 : 1; }
  }
  function apply() {
    if (!renderer) return;
    const act = activeSets();
    for (const item of Object.values(nodes3d)) {
      item.on = act ? act.nodes.has(item.n.id) : false; item.dim = !!act && !item.on;
      item.mesh.scale.setScalar(item.n.id === state.sel ? 1.4 : item.on ? 1.15 : 1);
      item.label.classList.toggle('dim', item.dim); item.label.classList.toggle('on', item.on);
      styleNode(item);
    }
    const list = [];
    for (const item of edges3d) {
      const on = act ? act.edges.has(item.e.id) : false;
      item.material.color.set(on ? T.accent : T.edge);
      item.material.opacity = act ? (on ? 1 : 0.05) : 0.55;
      if (on) list.push(item);
    }
    for (const [key, pad] of Object.entries(facilityPads)) { const on = state.facility === key || state.answerMain === key; pad.pad.opacity = padOpacity(on); pad.rim.opacity = rimOpacity(on); }
    showAnswerInfo();
    pulses.forEach((p, i) => { p.edge = i < list.length * 2 ? list[i % list.length] : null; p.t = (i / Math.max(1, list.length * 2)) % 1; p.d.visible = !!p.edge; });
    dirty = true;
  }
  function recolor() {
    T = tokens();
    for (const { material, key } of themed) material.color.set(tc(key));
    paintProcess();
    apply();
  }

  /* ---------- 왼쪽 도구 ---------- */
  const shownLedger = shown.filter(n => !n.aggregate).length, aggregated = shown.filter(n => n.aggregate).reduce((sum, n) => sum + n.members.length, 0);
  const { classes, relations, shapes } = twinOntologyCounts(ontology);
  function toggleButton(text, initial, action) {
    const b = element('button', 'btn sm', text); b.type = 'button'; b.setAttribute('aria-pressed', String(initial));
    b.addEventListener('click', () => { const v = b.getAttribute('aria-pressed') !== 'true'; b.setAttribute('aria-pressed', String(v)); action(v); });
    return b;
  }
  const rowLayers = element('div', 'twin2-row');
  rowLayers.append(
    toggleButton('현장', true, v => { layers?.site.forEach(o => { o.visible = v; }); dirty = true; }),
    toggleButton('원장 그래프', true, v => { layers?.onto.forEach(o => { o.visible = v; }); dirty = true; }),
    toggleButton('관계선', true, v => { layers?.edge.forEach(o => { o.visible = v; }); dirty = true; }),
    toggleButton('이름표', namesOn, v => { labels?.domElement.classList.toggle('hide-names', !v); dirty = true; }));
  const rowView = element('div', 'twin2-row');
  const viewButton = (text, fn) => { const b = element('button', 'btn sm', text); b.type = 'button'; b.addEventListener('click', fn); return b; };
  rowView.append(viewButton('전체 보기', () => fly(HOME.pos, HOME.target)), viewButton('위에서', () => fly(new THREE.Vector3(0.1, 175, 18), new THREE.Vector3(-4, 0, 2))));
  const scope = element('div', 'twin2-scope');
  const line = (label, value) => { const p = element('span'); p.append(document.createTextNode(label + ' '), element('b', '', value)); return p; };
  scope.append(line('원장', `v${meta.ledger_version ?? '?'}`), line('그래프', `v${meta.projection_version ?? '?'}`),
    line('표시 노드', `${shownLedger} / ${ledgerNodes.length}`), line('묶음으로 축약', `${aggregated}개`),
    line('노드 이름표', namesOn ? '모두 표시' : '노드가 많아 숨김 · 선택·질의 근거만 표시'),
    line('자재 부족', `${alerts.filter(a => a.state === 'short').length}품목 (계획 투입 기준)`),
    line('전체 T-Box', `클래스 ${fmt(classes)} · 관계 ${fmt(relations)} · shape ${fmt(shapes)}`));
  const hlBox = element('div', 'twin2-hl'); hlBox.hidden = true;
  const procLegend = element('div', 'twin2-proc');
  tools.append(element('h3', '', '표시'), rowLayers, rowView, element('h3', '', '공정 구역'), procLegend, element('h3', '', '조회 기준'), scope, hlBox,
    element('p', 'twin2-note', '같은 종류가 많은 노드는 묶음으로 줄여 보여 줍니다. 원장과 전체 온톨로지는 바뀌지 않습니다. 전체 T-Box는 온톨로지 · 질의 탭의 전체 온톨로지에서 봅니다.'));

  /* ---------- 오른쪽: 원장 진행 현황 ---------- */
  const sideToggle = element('button', 'btn sm twin2-side-toggle', '현황 접기'); sideToggle.type = 'button';
  const cards = element('div'); cards.style.display = 'flex'; cards.style.flexDirection = 'column'; cards.style.gap = '8px';
  sideToggle.addEventListener('click', () => { cards.hidden = !cards.hidden; sideToggle.textContent = cards.hidden ? '현황 펼치기' : '현황 접기'; });
  side.append(sideToggle, cards);
  const card = (title, ref) => { const c = element('div', 'twin2-card'), h = element('div', 'h', title); if (ref) h.append(element('span', 'ref', ref)); c.append(h); cards.append(c); return c; };
  const big = (value, unit) => { const b = element('div', 'twin2-big', fmt(value)); if (unit) b.append(element('small', '', unit)); return b; };
  const sub = parts => { const p = element('div', 'twin2-sub'); parts.forEach(([label, value], i) => { if (i) p.append(document.createTextNode(' · ')); p.append(document.createTextNode(label + ' '), element('b', '', fmt(value))); }); return p; };
  const bar = (segments, total) => {
    const wrap = element('div', 'twin2-bar'); const keys = element('div', 'twin2-keys');
    if (!isNum(total) || total <= 0) return [wrap, keys];
    for (const [label, value, color] of segments) {
      if (!isNum(value)) continue;
      const i = element('i'); i.style.width = `${Math.max(0, value) / total * 100}%`; i.style.background = color; wrap.append(i);
      const k = element('span', '', `${label} ${fmt(value)}`); k.style.setProperty('--c', color); keys.append(k);
    }
    return [wrap, keys];
  };
  {
    const c = card('납품 · 남은 약속', `${summary.delivery_round_id ?? ''} · 도착기한 ${summary.arrival_due_date ?? '미확인'}`);
    c.append(big(summary.promise_remaining_qty, `/ 약속 ${fmt(summary.promise_qty)}대`));
    const [b, k] = bar([['도착', summary.arrived_qty, 'var(--v1)'], ['운송 중', summary.transit_qty, 'var(--v2)'], ['창고', summary.warehouse_remaining_qty, 'var(--v3)'],
      ['입고 대기', summary.good_not_received_qty, 'var(--v4)'], ['남은 생산', summary.production_remaining_qty, 'var(--vtrack)']], summary.promise_qty);
    c.append(b, k);
  }
  {
    const good = summary.good_qty, defect = summary.defect_qty, pending = summary.pending_judgement_qty;
    const judged = isNum(good) && isNum(defect) ? good + defect : null;
    const c = card('생산실적', summary.production_job_id);
    c.append(big(judged ? +(defect / judged * 100).toFixed(2) : null, '% 불량률 (판정분)'));
    const [b, k] = bar([['양품', good, 'var(--ok)'], ['불량', defect, 'var(--bad)'], ['판정대기', pending, 'var(--warn)']], isNum(judged) && isNum(pending) ? judged + pending : null);
    c.append(b, k, sub([['남은 생산', summary.production_remaining_qty]]));
  }
  {
    const materialState = twinMaterialSummary(requirements);
    const c = card('자재 소요', requirements.bom_id ? `BOM ${requirements.bom_id}` : '');
    const head = element('div'); head.append(element('span', `pill ${materialState.k}`, materialState.text)); c.append(head);
    const basis = requirements.basis || {};
    c.append(sub([['남은 생산', basis.production_remaining_qty], ['투입 기준(가정)', basis.planned_input_qty]]));
    const short = element('div', 'twin2-short');
    for (const item of reqItems) {
      const status = twinMaterialStatus(item);
      short.append(element('span', status.k, `${item.item_id} ${status.text}${item.conditional ? ' · 시연 가정' : ''}`));
    }
    if (reqItems.length) c.append(short);
    c.append(sub([['BOM 행', requirements.row_count], ['미확인 행', requirements.unknown_shortage_count]]));
  }
  {
    const c = card('재고', summary.item_id || '단일 품목 아님');
    c.append(big(summary.available_qty, '가용'), sub([['보유', summary.on_hand_qty], ['타 수요 예약', summary.reserved_qty]]));
  }
  {
    const events = twinRecentEvents(snapshot.events, meta.ledger_version);
    const c = card('원장 사건', `${fmt(snapshot.event_count)}건`);
    const list = element('ol', 'twin2-feed');
    for (const event of events) {
      const li = element('li'), time = element('span', 't', twinEventTime(event.recorded_at));
      time.title = typeof event.recorded_at === 'string' ? event.recorded_at : '기록 시각 미확인';
      li.append(time, element('span', 'v', `v${event.ledger_version ?? '?'}`), element('span', 'x', event.summary));
      list.append(li);
    }
    if (!events.length) list.append(element('li', '', Array.isArray(snapshot.events) ? '기록된 사건이 없습니다.' : '사건 조회 상태 미확인'));
    c.append(list, element('p', 'twin2-note', '새 원장 버전이 생기면 위 바의 "새 원장 조회"로 다시 읽습니다.'));
  }
  card('표시하지 않는 값', '').append(element('p', 'twin2-note', `${twinOriginNote(meta, requirements)} 설비 가동률·현장 좌표·미래 일정은 미확인입니다. 건물 배치와 바닥의 움직이는 점은 개념도 장식이며 실제 시설이나 물류 사건이 아닙니다.`));

  /* ---------- 현장 태그 (원장 값) ---------- */
  const setTag = (key, { value, sub: text, pill, tone = 'mute', pct = null }) => {
    const tag = facilityTags[key]; if (!tag) return;
    tag.value.textContent = value; tag.sub.textContent = text; tag.pill.textContent = pill; tag.pill.className = `pill ${tone}`;
    tag.bar.hidden = pct === null; if (pct !== null) tag.fill.style.width = `${Math.max(0, Math.min(1, pct)) * 100}%`;
  };
  // The focus part as the server computed it now (planned-input basis).
  const focusReq = requirements.state !== 'BLOCKED_BOM' ? (requirements.items || []).find(item => item.item_id === summary.item_id) : null;
  const liveMaterial = focusReq && isNum(Number(focusReq.planned_required_qty)) && focusReq.planned_shortage_qty != null
    ? { item_id: focusReq.item_id, required: Number(focusReq.planned_required_qty), available: Number(focusReq.available_qty), shortage: Number(focusReq.planned_shortage_qty) } : null;
  function paintTags(summary, replaying = false, material = liveMaterial) {
    if (!renderer) return;
    const quotes = snapshot.quotations, subs = snapshot.substitutes;
    setTag('sup', { value: `기초 견적 ${fmt(arrayCount(quotes))}건`, sub: `대체 후보 ${fmt(arrayCount(subs))}건`, pill: '조회', tone: 'info' });
    const materialState = twinMaterialSummary(requirements);
    if (material && isNum(material.shortage)) {
      // Name the part and how full it is: available against the planned requirement.
      setTag('mat', { value: material.shortage > 0 ? `${material.item_id} 부족 ${fmt(material.shortage)}` : `${material.item_id} 충족`,
        sub: `가용 ${fmt(material.available)} / 필요 ${fmt(material.required)} · 보유 ${fmt(summary.on_hand_qty)} · 예약 ${fmt(summary.reserved_qty)}`,
        pill: replaying ? (material.shortage > 0 ? '재생 · 부족' : '재생 · 충족') : materialState.text,
        tone: replaying ? (material.shortage > 0 ? 'bad' : 'ok') : materialState.k,
        pct: material.required > 0 ? material.available / material.required : 1 });
    } else setTag('mat', { value: `가용 ${fmt(summary.available_qty)}`, sub: `보유 ${fmt(summary.on_hand_qty)} · 예약 ${fmt(summary.reserved_qty)}`,
      pill: replaying ? '재생 시점' : materialState.text, tone: replaying ? 'mute' : materialState.k });
    const made = isNum(summary.good_qty) && isNum(summary.production_remaining_qty) && summary.good_qty + summary.production_remaining_qty > 0 ? summary.good_qty / (summary.good_qty + summary.production_remaining_qty) : null;
    // Defect and pending-judgement counts are not replayed, so replay shows good boards only.
    if (replaying) setTag('line', { value: `남은 생산 ${fmt(summary.production_remaining_qty)}`, sub: `양품 ${fmt(summary.good_qty)}`, pill: '재생 시점', pct: made });
    else setTag('line', { value: `남은 생산 ${fmt(summary.production_remaining_qty)}`, sub: `양품 ${fmt(summary.good_qty)} · 불량 ${fmt(summary.defect_qty)} · 판정대기 ${fmt(summary.pending_judgement_qty)}`,
      pill: summary.pending_judgement_qty > 0 ? '판정대기' : '원장 기준', tone: summary.pending_judgement_qty > 0 ? 'warn' : 'mute', pct: made });
    setTag('fg', { value: `입고 대기 ${fmt(summary.good_not_received_qty)}`, sub: `완성품 입고 ${fmt(summary.fg_received_qty)} · 창고 ${fmt(summary.warehouse_remaining_qty)}`, pill: replaying ? '재생 시점' : '원장 기준' });
    setTag('ship', { value: `운송 중 ${fmt(summary.transit_qty)}`, sub: `출하 ${fmt(summary.shipped_qty)} · 도착 ${fmt(summary.arrived_qty)}`, pill: replaying ? '재생 시점' : '원장 기준',
      pct: isNum(summary.shipped_qty) && isNum(summary.promise_qty) && summary.promise_qty ? summary.shipped_qty / summary.promise_qty : null });
    setTag('cust', { value: `${fmt(summary.arrived_qty)} / ${fmt(summary.promise_qty)}`, sub: `남은 약속 ${fmt(summary.promise_remaining_qty)} · 도착기한 ${summary.arrival_due_date ?? '미확인'}`,
      pill: twinPromiseStatus(summary.promise_remaining_qty).text, tone: twinPromiseStatus(summary.promise_remaining_qty).tone,
      pct: isNum(summary.arrived_qty) && summary.promise_qty ? summary.arrived_qty / summary.promise_qty : null });
  }
  paintTags(summary);

  /* ---------- 공정 색 · 질의 근거 공정의 현재 원장 값 ---------- */
  function paintProcess() {
    for (const f of FACILITIES) facilityTags[f.key]?.anchor.style.setProperty('--pc', T.proc[f.key]);
    procLegend.replaceChildren(...FACILITIES.map(f => { const s = element('span'), i = element('i'); i.style.background = T.proc[f.key]; s.append(i, document.createTextNode(`${PROC_NO[f.key]} ${f.name}`)); return s; }));
  }
  // showFacility의 KPI와 같은 원장 값만 쓴다. 질의 답변의 문장이나 모델 출력에서 수량을 만들지 않는다.
  const FACILITY_ROWS = {
    sup: () => [['기초 견적', arrayCount(snapshot.quotations)], ['대체 후보', arrayCount(snapshot.substitutes)]],
    mat: () => [['보유', summary.on_hand_qty], ['타 수요 예약', summary.reserved_qty], ['가용', summary.available_qty], ['자재 소요', twinMaterialSummary(requirements).text]],
    line: () => [['양품', summary.good_qty], ['불량', summary.defect_qty], ['판정대기', summary.pending_judgement_qty], ['남은 생산', summary.production_remaining_qty]],
    fg: () => [['완성품 입고', summary.fg_received_qty], ['입고 대기', summary.good_not_received_qty], ['창고', summary.warehouse_remaining_qty]],
    ship: () => [['출하', summary.shipped_qty], ['운송 중', summary.transit_qty], ['도착', summary.arrived_qty]],
    cust: () => [['약속', summary.promise_qty], ['도착', summary.arrived_qty], ['남은 약속', summary.promise_remaining_qty], ['도착기한', summary.arrival_due_date]],
  };
  let infoKey = null;
  function showAnswerInfo() {
    const zones = state.answerZones, key = zones ? [...zones.keys()].join(',') : '';
    if (key !== infoKey) { infoKey = key; tagAnchors.forEach(lo => { lo.nx = 0; lo.tag.style.translate = ''; lo.ov = {}; }); }
    for (const f of FACILITIES) {
      const tag = facilityTags[f.key]; if (!tag) continue;
      const count = zones?.get(f.key), main = state.answerMain === f.key;
      tag.anchor.classList.toggle('ans', !!count); tag.anchor.classList.toggle('main', !!count && main);
      tag.anchor.classList.toggle('mute', !!zones && !count);
      tag.info.hidden = !count; tag.info.replaceChildren();
      if (!count) continue;
      tag.info.append(element('span', 'h', `${main ? '질의 근거 · 현재 원장' : '관련 공정'} · 근거 노드 ${count}`));
      for (const [label, value] of FACILITY_ROWS[f.key]()) { const r = element('span', 'r'); r.append(element('span', '', label), element('b', '', fmt(value))); tag.info.append(r); }
    }
    dirty = true;
  }
  function setTagSide(lo, side) {
    lo.side = side; lo.anchor.classList.remove('l', 'r'); lo.anchor.classList.add(side);
    lo.label.position.set(side === 'l' ? -lo.w / 2 : lo.w / 2, lo.front ? 0.3 : lo.h * 0.7, lo.front ? lo.d / 2 : -lo.d / 2);
  }
  // 이름표가 화면 밖·왼쪽 도구 뒤로 가면 반대쪽으로 옮기고, 펼친 이름표끼리 겹치면 주 공정이 아닌 쪽을 옆으로 민다.
  function fitTags() {
    const vr = view.getBoundingClientRect(), floating = el => !el.hidden && getComputedStyle(el).position === 'absolute';
    const left = floating(tools) ? Math.max(vr.left, tools.getBoundingClientRect().right) + 6 : vr.left + 6;
    const right = floating(side) && !cards.hidden ? Math.min(vr.right, side.getBoundingClientRect().left) - 6 : vr.right - 6;
    for (const lo of tagAnchors) {
      const t = lo.tag.getBoundingClientRect(); if (!t.width) continue;
      const over = lo.side === 'r' ? Math.max(0, t.right - right) : Math.max(0, left - t.left), other = lo.side === 'r' ? 'l' : 'r';
      lo.ov[lo.side] = over;
      if (over > 0 && (lo.ov[other] == null || lo.ov[other] < over)) { setTagSide(lo, other); dirty = true; } else if (over === 0) lo.ov[other] = null;
    }
    const rank = lo => lo.anchor.classList.contains('main') ? 2 : lo.anchor.classList.contains('ans') ? 1 : 0;
    for (let i = 0; i < tagAnchors.length; i++) for (let j = i + 1; j < tagAnchors.length; j++) {
      const A = tagAnchors[i], B = tagAnchors[j], a = A.tag.getBoundingClientRect(), b = B.tag.getBoundingClientRect();
      if (!a.width || !b.width || !(a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom)) continue;
      const moveA = rank(A) < rank(B), mv = moveA ? A : B, st = moveA ? b : a, m = moveA ? a : b;
      const toRight = st.right - m.left + 8, toLeft = m.right - st.left + 8;
      mv.nx += m.right + toRight < right ? toRight : m.left - toLeft > left ? -toLeft : toRight; mv.tag.style.translate = `${mv.nx}px 0`; dirty = true;
    }
  }
  if (renderer) paintProcess();

  /* ---------- 상세 (노드 · ERP 형식) ---------- */
  function table(rows) {
    const wrap = element('div', 'twin2-table');
    if (!Array.isArray(rows) || !rows.length) { wrap.append(element('p', 'twin2-note', Array.isArray(rows) ? '원장에 기록이 없습니다.' : '조회 자료 미확인')); wrap.style.padding = '6px 8px'; return wrap; }
    const keys = [...new Set(rows.flatMap(row => Object.keys(row).filter(k => row[k] === null || typeof row[k] !== 'object')))].slice(0, 7);
    const t = element('table'), head = element('tr');
    keys.forEach(k => head.append(element('th', '', KEY_LABEL[k] || k)));
    const thead = element('thead'); thead.append(head); t.append(thead);
    const body = element('tbody');
    for (const row of rows.slice(0, 50)) {
      const tr = element('tr');
      keys.forEach(k => { const v = row[k]; tr.append(element('td', isNum(v) ? 'n' : '', fmt(v))); });
      body.append(tr);
    }
    t.append(body); wrap.append(t);
    if (rows.length > 50) wrap.append(element('p', 'twin2-note', `외 ${rows.length - 50}행`));
    return wrap;
  }
  function openDetail(children) {
    detail.replaceChildren();
    const close = element('button', 'btn sm close', '닫기'); close.type = 'button';
    close.addEventListener('click', () => { clearSelection(); });
    detail.append(close, ...children); detail.hidden = false; detail.scrollTop = 0;
  }
  function chip(id) {
    const b = element('button', 'chip', shownById[id]?.label || labelFor(id)); b.type = 'button';
    b.addEventListener('click', () => select(id, true)); return b;
  }
  function showNode(id) {
    const n = shownById[id]; if (!n) return;
    const parts = [element('div', 'k', `${n.type} · ${SHAPE_NAME[n.shape] || ''} · ${n.zone === 'core' ? CORE.name : FACILITIES.find(f => f.key === n.zone)?.name}`), element('h4', '', n.title)];
    if (n.aggregate) {
      parts.push(element('div', 'id', `원장 노드 ${n.members.length}개를 한 묶음으로 표시`));
      const list = element('div', 'twin2-chips'); n.members.forEach(m => { const b = element('button', 'chip', labelFor(m)); b.type = 'button'; b.addEventListener('click', () => onGraph(m)); list.append(b); });
      parts.push(list, element('p', 'twin2-note', '노드를 누르면 온톨로지 · 질의 탭에서 그 노드를 엽니다.'));
    } else {
      parts.push(element('div', 'id', id));
      const st = nodes3d[id]?.status;
      if (st) { const p = element('div'); p.append(element('span', `pill ${st.k}`, st.text)); parts.push(p); }
      const props = n.node.attributes?.properties || {};
      const entries = Object.entries(props).filter(([, v]) => v === null || typeof v !== 'object').slice(0, 12);
      if (entries.length) { const dl = element('dl', 'twin2-kv'); for (const [k, v] of entries) dl.append(element('dt', '', KEY_LABEL[k] || k), element('dd', '', fmt(v))); parts.push(dl); }
      const out = shownEdges.filter(e => e.source === id), inn = shownEdges.filter(e => e.target === id);
      if (out.length) { parts.push(element('div', 'k', `이 노드에서 나가는 관계 ${out.length}`)); const c = element('div', 'twin2-chips'); out.slice(0, 16).forEach(e => { c.append(element('span', 'twin2-note', e.label), chip(e.target)); }); parts.push(c); }
      if (inn.length) { parts.push(element('div', 'k', `이 노드로 들어오는 관계 ${inn.length}`)); const c = element('div', 'twin2-chips'); inn.slice(0, 16).forEach(e => { c.append(chip(e.source), element('span', 'twin2-note', e.label)); }); parts.push(c); }
      const actions = element('div', 'twin2-row');
      const toGraph = element('button', 'btn sm', '온톨로지 · 질의에서 보기'); toGraph.type = 'button'; toGraph.addEventListener('click', () => onGraph(id));
      const toContext = element('button', 'btn sm pri', '질의 맥락으로 지정'); toContext.type = 'button';
      toContext.addEventListener('click', () => { onSelect(id); onAsk(''); });
      actions.append(toGraph, toContext); parts.push(actions);
    }
    const evidence = twinRelationEvidence(prepared, id);
    if (evidence.length) {
      const disclosure = element('details'), list = element('ol', 'twin2-note');
      disclosure.append(element('summary', '', `원장 관계 ${evidence.length}건 · 묶음 내부·자기 관계 포함`));
      for (const edge of evidence) list.append(element('li', '', `${edge.id} · ${edge.source} → ${edge.predicate} → ${edge.target}${edge.internal ? ' · 표시 묶음 내부/자기 관계' : ''}`));
      disclosure.append(list); parts.push(disclosure);
    }
    openDetail(parts);
  }
  function showFacility(key) {
    const f = FACILITIES.find(item => item.key === key);
    const bar = element('div', 'twin2-erp'); bar.append(element('b', '', 'ERP 형식'), document.createTextNode(`${f.module} · ${f.name}`));
    const parts = [bar, element('h4', '', f.name)];
    const kpis = (list) => { const g = element('div', 'twin2-kpis'); for (const [l, v] of list) { const d = element('div'); d.append(element('small', '', l), element('b', '', fmt(v))); g.append(d); } return g; };
    if (key === 'sup') parts.push(kpis([['기초 견적', arrayCount(snapshot.quotations)], ['대체 후보', arrayCount(snapshot.substitutes)], ['매핑 후보', arrayCount(snapshot.mapping_candidates)]]),
      element('div', 'k', '기초 견적'), table(snapshot.quotations), element('div', 'k', '대체 후보'), table(snapshot.substitutes));
    if (key === 'mat') parts.push(kpis([['보유', summary.on_hand_qty], ['타 수요 예약', summary.reserved_qty], ['가용', summary.available_qty]]),
      element('div', 'k', '재고 기록'), table(snapshot.inventory), element('div', 'k', '자재 소요 (BOM 반영 품목)'), table(reqItems));
    if (key === 'line') parts.push(kpis([['양품', summary.good_qty], ['불량', summary.defect_qty], ['판정대기', summary.pending_judgement_qty]]),
      element('div', 'k', '생산실적'), table(snapshot.production), sub([['BOM 상태', summary.bom_status], ['기술 검수', summary.technical_review], ['제조 가능성', summary.actual_manufacturing_feasibility]]));
    if (key === 'fg') parts.push(kpis([['완성품 입고', summary.fg_received_qty], ['입고 대기', summary.good_not_received_qty], ['창고', summary.warehouse_remaining_qty]]));
    if (key === 'ship') parts.push(kpis([['출하', summary.shipped_qty], ['운송 중', summary.transit_qty], ['도착', summary.arrived_qty]]), element('div', 'k', '출하 기록'), table(snapshot.shipments));
    if (key === 'cust') parts.push(kpis([['약속', summary.promise_qty], ['도착', summary.arrived_qty], ['남은 약속', summary.promise_remaining_qty]]), element('div', 'k', '납품 회차'), table(snapshot.rounds));
    const bound = shown.filter(n => n.zone === key);
    if (bound.length) { parts.push(element('div', 'k', `이 현장에 묶인 원장 노드 ${bound.length}`)); const c = element('div', 'twin2-chips'); bound.forEach(n => c.append(chip(n.id))); parts.push(c); }
    parts.push(element('p', 'twin2-note', `조회 원장 v${meta.ledger_version ?? '?'} 값을 ERP 표 형식으로 보여 줍니다. ${twinOriginNote(meta, requirements)} 실제 ERP 연동이나 쓰기 기능은 아닙니다.`));
    openDetail(parts);
  }

  /* ---------- 선택 · 카메라 ---------- */
  const HOME = { pos: new THREE.Vector3(98, 104, 124), target: new THREE.Vector3(-4, 10, 2) };
  let flight = null, dirty = true, active = false, raf = 0, last = performance.now(), fitTick = 0;
  function fly(pos, target) {
    if (!renderer) return;
    flight = { p0: camera.position.clone(), t0: controls.target.clone(), p1: pos.clone(), t1: target.clone(), k: 0 };
    if (reduce) { camera.position.copy(pos); controls.target.copy(target); flight = null; }
    dirty = true;
  }
  function flyNear(point, dist, height) {
    const dir = camera.position.clone().sub(controls.target).setY(0).normalize();
    if (dir.lengthSq() < 0.01) dir.set(0.6, 0, 0.8);
    const to = point.clone().add(dir.multiplyScalar(dist)); to.y = point.y + height;
    fly(to, point);
  }
  function select(id, move) {
    state.sel = id; state.facility = null; state.highlight = null; state.answerZones = state.answerMain = null; hlBox.hidden = true; apply(); showNode(id);
    if (!shownById[id]?.aggregate) onSelect(id);
    if (move && nodePos[id]) flyNear(nodePos[id], 34, 18);
  }
  function selectFacility(key, move) {
    state.facility = key; state.sel = null; state.highlight = null; state.answerZones = state.answerMain = null; hlBox.hidden = true; apply(); showFacility(key);
    const f = FACILITIES.find(item => item.key === key);
    if (move) flyNear(new THREE.Vector3(f.pos[0], 10, f.pos[1]), 48, 34);
  }
  function clearSelection() { state.sel = null; state.facility = null; detail.hidden = true; apply(); }

  /* ---------- 마우스 ---------- */
  const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
  let down = null;
  function pick(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    ptr.x = ((event.clientX - rect.left) / rect.width) * 2 - 1; ptr.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    ray.setFromCamera(ptr, camera);
    const at = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    if (layers.onto[0].visible) { const hits = ray.intersectObjects(Object.values(nodes3d).map(item => item.mesh), false); if (hits.length) return { ...at, node: hits[0].object.userData.node }; }
    if (layers.site[0]?.visible !== false) { const hits = ray.intersectObjects(facilityHits, false); if (hits.length) return { ...at, facility: hits[0].object.userData.facility }; }
    return null;
  }
  const listeners = [];
  const on = (target, type, fn, opts) => { target.addEventListener(type, fn, opts); listeners.push([target, type, fn, opts]); };
  if (renderer) {
    on(renderer.domElement, 'pointerdown', e => { down = [e.clientX, e.clientY]; });
    on(renderer.domElement, 'pointerup', e => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) { down = null; return; }
      down = null; const hit = pick(e);
      if (!hit) { if (state.sel || state.facility) clearSelection(); return; }
      if (hit.node) select(hit.node, true); else selectFacility(hit.facility, true);
    });
    on(renderer.domElement, 'pointermove', e => {
      if (e.buttons) { tip.hidden = true; return; }
      const hit = pick(e); renderer.domElement.style.cursor = hit ? 'pointer' : 'grab';
      if (!hit) { tip.hidden = true; return; }
      tip.hidden = false; tip.style.left = `${hit.x}px`; tip.style.top = `${hit.y}px`;
      tip.textContent = hit.node ? `${shownById[hit.node].label} · ${shownById[hit.node].type}` : `${FACILITIES.find(f => f.key === hit.facility).name} · ERP 형식 보기`;
    });
    on(renderer.domElement, 'pointerleave', () => { tip.hidden = true; });
    controls.addEventListener('change', () => { dirty = true; });
  }

  /* ---------- 크기 · 그리기 ---------- */
  function resize() {
    if (!renderer) return;
    const w = view.clientWidth, h = view.clientHeight; if (!w || !h) return;
    renderer.setSize(w, h, false); labels.setSize(w, h);
    renderer.domElement.style.width = `${w}px`; renderer.domElement.style.height = `${h}px`;
    camera.aspect = w / h; camera.updateProjectionMatrix(); dirty = true;
  }
  const observer = new ResizeObserver(resize); observer.observe(view);
  const themeObserver = new MutationObserver(recolor);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  const ease = k => k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
  /* ---------- 원장 이력 재생 ---------- */
  const replay = buildTwinReplay(snapshot);
  const rp = { i: -1, timer: null, speed: 1, playing: false, token: null, tokenMesh: null };
  const ZONE_XZ = Object.fromEntries([...FACILITIES.map(f => [f.key, f.pos]), [CORE.key, CORE.pos]]);
  const replayBar = element('div', 'twin2-replay');
  if (replay) {
    const top = element('div', 'row'), playB = element('button', 'btn sm pri', '▶ 이력 재생'), firstB = element('button', 'btn sm', '⏮'), stopB = element('button', 'btn sm', '끝내기');
    const speedS = element('select', 'twin2-replay-speed'); for (const v of [1, 2, 4]) { const o = element('option', '', `${v}×`); o.value = String(v); speedS.append(o); }
    const slider = element('input'); slider.type = 'range'; slider.min = '0'; slider.max = String(replay.steps.length - 1); slider.value = '0';
    [playB, firstB, stopB].forEach(b => { b.type = 'button'; });
    const count = element('span', 'cnt', `${replay.steps.length}건 · v${replay.steps[0].version}–v${replay.steps.at(-1).version}`);
    top.append(playB, firstB, slider, speedS, stopB, count);
    const card = element('div', 'card'); card.hidden = true;
    replayBar.append(top, card); view.append(replayBar);
    // Card line: the part filling up, e.g. "IM-0016 · 가용 15 / 필요 43 · 부족 28" with a bar and the change.
    const materialLine = (now, before) => {
      const box = element('div', 'mat');
      if (!now) return box;
      box.classList.toggle('short', now.shortage > 0);
      box.append(element('span', 'nm', `${now.item_id}${now.item_name ? ' ' + now.item_name : ''}`),
        element('span', 'q', `가용 ${fmt(now.available)} / 필요 ${fmt(now.required)} · ${now.shortage > 0 ? `부족 ${fmt(now.shortage)}` : '충족'}`));
      const bar = element('i', 'bar'), fill = element('i'); bar.append(fill);
      fill.style.width = `${Math.max(0, Math.min(1, now.required > 0 ? now.available / now.required : 1)) * 100}%`; box.append(bar);
      if (before && before.shortage !== now.shortage) {
        const kind = now.shortage === 0 ? 'resolved' : now.shortage > before.shortage ? 'worse' : 'better';
        box.append(element('span', `twin2-change ${kind}`, changeText({ before: before.shortage, after: now.shortage, kind })));
      }
      return box;
    };
    // Marker on the part's node in the graph while replaying.
    const itemNode = Object.values(nodes3d).find(o => o.n.type === 'InternalItem' && (o.n.id === focusReq?.item_entity_id || o.n.label === focusReq?.item_id));
    let marker = null;
    if (renderer && itemNode) {
      const pos = nodePos[itemNode.n.id], tag = element('div', 'twin2-alert'), group = itemNode.mesh.parent || scene;
      const ring = key => {
        const mesh = new THREE.Mesh(new THREE.RingGeometry(2.1, 2.6, 48), tm(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false }), key));
        mesh.rotation.x = -Math.PI / 2; mesh.position.copy(pos); mesh.position.y -= 1.25; mesh.visible = false; group.add(mesh); return mesh;
      };
      marker = { tag, b: element('b'), label: new CSS2DObject(tag), bad: ring('bad'), ok: ring('ok') };
      tag.append(marker.b); marker.label.position.copy(pos); marker.label.position.y += 3.2;
      marker.label.visible = false; group.add(marker.label);
    }
    function showItemMarker(material) {
      if (!marker) return;
      const short = Boolean(material) && material.shortage > 0;
      marker.label.visible = Boolean(material); marker.bad.visible = short; marker.ok.visible = Boolean(material) && !short;
      if (material) {
        marker.tag.className = `twin2-alert ${short ? 'short' : 'ok'}`;
        marker.b.textContent = short ? `${material.item_id} 부족 ${fmt(material.shortage)}` : `${material.item_id} 충족 ✓`;
      }
      dirty = true;
    }
    const restore = () => {
      for (const f of FACILITIES) { const tag = facilityTags[f.key]; tag?.anchor.classList.remove('replay', 'mute'); if (facilityPads[f.key]) facilityPads[f.key].pad.opacity = padOpacity(false); }
      paintTags(summary); showItemMarker(null);
      if (rp.tokenMesh) rp.tokenMesh.visible = false; rp.token = null; dirty = true;
    };
    const show = i => {
      const step = replay.steps[i], prev = replay.steps[i - 1]; rp.i = i; slider.value = String(i);
      paintTags({ ...summary, ...step.state }, true, step.material);
      showItemMarker(step.material);
      for (const f of FACILITIES) {
        const on = f.key === step.zone, tag = facilityTags[f.key];
        tag?.anchor.classList.toggle('replay', on); tag?.anchor.classList.toggle('mute', !on);
        if (facilityPads[f.key]) facilityPads[f.key].pad.opacity = on ? (T.dark ? 0.6 : 0.5) : 0.07;
      }
      card.hidden = false; card.replaceChildren(
        element('b', '', `v${step.version} · ${step.title}`),
        element('span', 'z', `${step.zone === 'core' ? CORE.name : FACILITIES.find(f => f.key === step.zone)?.name}${step.detail ? ' · ' + step.detail : ''}`),
        materialLine(step.material, prev?.material),
        element('span', 'm', step.arrivedTotal == null ? '' : `도착 누계 ${fmt(step.arrivedTotal)}대 · 남은 약속 ${fmt(step.remaining)}대`),
        element('small', '', `${i + 1} / ${replay.steps.length} · 원장 사건 재생 · 원장은 바뀌지 않습니다`));
      if (renderer && !reduce && prev && prev.zone !== step.zone) {
        if (!rp.tokenMesh) { rp.tokenMesh = new THREE.Mesh(new THREE.SphereGeometry(0.9, 20, 14), tm(new THREE.MeshBasicMaterial({}), 'accent')); scene.add(rp.tokenMesh); }
        const a = ZONE_XZ[prev.zone], b = ZONE_XZ[step.zone];
        rp.token = { from: new THREE.Vector3(a[0], 2, a[1]), to: new THREE.Vector3(b[0], 2, b[1]), t: 0 }; rp.tokenMesh.visible = true;
      }
      dirty = true;
    };
    const stop = () => { rp.playing = false; clearTimeout(rp.timer); playB.textContent = '▶ 이력 재생'; };
    const tick = () => {
      if (!rp.playing) return;
      if (rp.i >= replay.steps.length - 1) { stop(); return; }
      show(rp.i + 1); rp.timer = setTimeout(tick, 1700 / rp.speed);
    };
    playB.addEventListener('click', () => {
      if (rp.playing) { stop(); return; }
      if (rp.i >= replay.steps.length - 1) rp.i = -1;
      rp.playing = true; playB.textContent = '⏸ 멈춤'; tick();
    });
    firstB.addEventListener('click', () => { stop(); show(0); });
    stopB.addEventListener('click', () => { stop(); rp.i = -1; card.hidden = true; restore(); });
    speedS.addEventListener('change', () => { rp.speed = Number(speedS.value) || 1; });
    slider.addEventListener('input', () => { stop(); show(Number(slider.value)); });
    rp.stop = () => { stop(); clearTimeout(rp.timer); };
  }
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min((now - last) / 1000, 0.1); last = now;
    if (!renderer || !active || document.hidden || !view.clientWidth) return;
    if (!reduce) {
      flowDots.forEach(o => { o.t = (o.t + dt * 0.1) % 1; o.d.position.copy(o.curve.getPoint(o.t)); });
      if (rp.token && rp.tokenMesh) { rp.token.t = Math.min(1, rp.token.t + dt * 1.4); const p = rp.token.from.clone().lerp(rp.token.to, rp.token.t); p.y += Math.sin(rp.token.t * Math.PI) * 6; rp.tokenMesh.position.copy(p); if (rp.token.t >= 1) { rp.tokenMesh.visible = false; rp.token = null; } }
      if (alertRings.length) { const k = 1 + 0.2 * (Math.sin(now / 280) + 1) / 2; alertRings.forEach(r => r.scale.setScalar(k)); }
      pulses.forEach(p => { if (!p.edge) return; p.t = (p.t + dt * 0.45) % 1; p.d.position.copy(p.edge.curve.getPoint(p.t)); });
      if (truckRig.truck) {
        // 오고 갈 때 모두 앞으로 달리도록 움직이는 방향을 바라보고, 양 끝에서 부드럽게 돈다.
        truckRig.t += dt * 0.25; const s = Math.sin(truckRig.t), tt = (s + 1) / 2, back = Math.cos(truckRig.t) < 0, road = truckRig.road;
        const ahead = road.getPoint(back ? Math.max(0, tt - 0.02) : Math.min(1, tt + 0.02)), behind = road.getPoint(back ? Math.min(1, tt + 0.02) : Math.max(0, tt - 0.02));
        const dir = ahead.sub(behind), want = Math.atan2(-dir.z, dir.x);
        if (truckRig.yaw === null) truckRig.yaw = want;
        const turn = Math.atan2(Math.sin(want - truckRig.yaw), Math.cos(want - truckRig.yaw));
        truckRig.yaw += turn * Math.min(1, dt * 3.2);
        truckRig.truck.position.copy(road.getPoint(tt)); truckRig.truck.rotation.set(0, truckRig.yaw, 0);
        truckRig.halo.material.opacity = 0.12 + 0.06 * (Math.sin(now / 1000 * 1.6) + 1) / 2;
      }
      dirty = true;
    }
    if (++fitTick % 20 === 0) fitTags();
    if (flight) {
      flight.k = Math.min(1, flight.k + dt / 1.1); const e = ease(flight.k);
      camera.position.lerpVectors(flight.p0, flight.p1, e); controls.target.lerpVectors(flight.t0, flight.t1, e);
      if (flight.k >= 1) flight = null; dirty = true;
    }
    controls.update();
    if (dirty) { renderer.render(scene, camera); labels.render(scene, camera); dirty = false; }
  }
  if (renderer) { camera.position.copy(HOME.pos); controls.target.copy(HOME.target); apply(); }
  raf = requestAnimationFrame(frame);

  return {
    setActive(value) { active = !!value; if (active) { resize(); dirty = true; } },
    resize,
    highlight(input = {}) {
      const highlighted = twinHighlight(meta, prepared, input);
      if (!highlighted) return false;
      const { nodes, edges: edgeSet, ledgerEdgeIds } = highlighted;
      state.sel = null; state.facility = null; detail.hidden = true;
      state.highlight = nodes.size ? { nodes, edges: edgeSet } : null;
      const zones = twinAnswerZones(prepared, nodes);
      state.answerZones = zones.size ? zones : null; state.answerMain = zones.size ? [...zones.keys()][0] : null;
      hlBox.replaceChildren();
      if (state.highlight) {
        hlBox.append(document.createTextNode(`질의 근거 표시 ${nodes.size}개 · 원장 관계 ${ledgerEdgeIds.size}개 (묶음 내부 포함)`));
        const clear = element('button', 'btn sm', '지우기'); clear.type = 'button';
        clear.addEventListener('click', () => { state.highlight = null; state.answerZones = state.answerMain = null; hlBox.hidden = true; apply(); });
        hlBox.append(clear); hlBox.hidden = false;
      } else hlBox.hidden = true;
      apply();
      return true;
    },
    reset() { state.highlight = null; state.answerZones = state.answerMain = null; hlBox.hidden = true; clearSelection(); if (renderer) fly(HOME.pos, HOME.target); },
    destroy() {
      rp.stop?.();
      cancelAnimationFrame(raf); observer.disconnect(); themeObserver.disconnect();
      for (const [target, type, fn, opts] of listeners) target.removeEventListener(type, fn, opts);
      if (renderer) {
        controls.dispose();
        scene.traverse(o => { o.geometry?.dispose?.(); [].concat(o.material || []).forEach(m => { m.map?.dispose?.(); m.dispose?.(); }); });
        renderer.dispose();
      }
      container.replaceChildren();
    },
  };
}
