/**
 * Radial hop-distance view of the server snapshot graph.
 *
 * Every object sits on the ring of its hop distance (shortest path over
 * recorded relations, either direction) from a centre object, by default the
 * delivery round. Objects with no path to the centre sit on a dashed outer
 * ring. The view only reads the snapshot: no object, relation, direction or
 * evidence is added, merged or dropped, and positions are presentation only.
 */

const NS = 'http://www.w3.org/2000/svg';
const AREA_OF = { ScenarioRun: 'plan', ScenarioProfile: 'plan', ScenarioAssumption: 'plan', ProductionPlan: 'plan',
  Customer: 'dem', ContractLine: 'dem', DeliveryRound: 'dem', ProductionJob: 'dem', Demand: 'dem', ProductionReport: 'dem',
  FGReceipt: 'dem', FinishedGoodsReceipt: 'dem', Shipment: 'dem', Arrival: 'dem', ArrivalRecord: 'dem', BoardUnit: 'dem',
  InstallationEvent: 'dem', ConsumptionEvent: 'dem', Allocation: 'dem', LegacyCohort: 'dem',
  ProductConfiguration: 'bom', ProductModel: 'bom', Revision: 'bom', BOMSnapshot: 'bom', BOMLine: 'bom', BOMApplication: 'bom',
  InternalItem: 'inv', PartLot: 'inv', InventoryReservation: 'inv', Reservation: 'inv', ReservationEvent: 'inv',
  PartsReceiptLine: 'inv', InspectionResult: 'inv', IncomingInspection: 'inv',
  Supplier: 'pro', SupplierOffer: 'pro', SupplierQuote: 'pro', Quotation: 'pro', PurchaseRequest: 'pro', PurchaseOrder: 'pro',
  PurchaseOrderLine: 'pro', SupplyConfirmation: 'pro', Receipt: 'pro',
  SourceDocument: 'ev', SourceSnapshot: 'ev', SourceRow: 'ev', DocumentVersion: 'ev', ApprovalGrant: 'ev', Evidence: 'ev' };
const AREAS = [['dem', '수요·이행'], ['bom', 'BOM·구성'], ['inv', '품목·재고'], ['pro', '조달'], ['plan', '가정·계획'], ['ev', '근거·문서']];
// Korean names for ring summaries; an unknown class keeps its schema name.
const KIND_KO = { BoardUnit: '보드 실물', InstallationEvent: '장착 사건', ApprovalGrant: '승인 기록', DocumentVersion: '문서 버전',
  BOMLine: 'BOM 행', SourceRow: 'BOM 원문 행', BOMApplication: '적용 판단', SupplierOffer: '공급사 견적', ConsumptionEvent: '소비 사건',
  Shipment: '출하', ArrivalRecord: '도착 기록', FinishedGoodsReceipt: '완성품 입고', ProductionReport: '생산 실적', Evidence: '근거',
  PartLot: '부품 LOT', InternalItem: '사내 품목', Allocation: '배정', PurchaseOrderLine: '발주 행', PartsReceiptLine: '입하 행',
  InspectionResult: '수입검사', ReservationEvent: '예약', LegacyCohort: '기초 보드 묶음', Supplier: '공급사' };
const KEY_TYPES = new Set(['Customer', 'ContractLine', 'DeliveryRound', 'ProductionJob', 'ProductConfiguration', 'InternalItem',
  'PartLot', 'Supplier', 'BOMSnapshot', 'PurchaseOrder', 'PurchaseRequest']);

const idOf = node => node.node_id || node.id;
const typeOf = node => node.type || node.source_type || node.attributes?.kind_label || String(node.ontology_type || '').split('#').pop() || '';
const labelOf = node => node.display_id || node.label || node.attributes?.display || idOf(node);
const ends = edge => [edge.source_node_id || edge.source, edge.target_node_id || edge.target];
const edgeIdOf = edge => edge.edge_id || edge.id;
const TAU = Math.PI * 2;

/** Hop distance of every object from the centre over recorded relations in either direction. */
export function hopRings(nodes, edges, centerId) {
  const ids = new Set(nodes.map(idOf));
  const next = new Map([...ids].map(id => [id, []]));
  for (const edge of edges) {
    const [a, b] = ends(edge);
    if (!ids.has(a) || !ids.has(b) || a === b) continue;
    next.get(a).push(b); next.get(b).push(a);
  }
  const hop = new Map();
  if (ids.has(centerId)) {
    hop.set(centerId, 0);
    const queue = [centerId];
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i];
      for (const other of next.get(id)) if (!hop.has(other)) { hop.set(other, hop.get(id) + 1); queue.push(other); }
    }
  }
  const rings = [];
  for (const [id, h] of hop) (rings[h] ||= []).push(id);
  const unreachable = [...ids].filter(id => !hop.has(id));
  return { hop, rings, unreachable, next };
}

function circularMean(angles) {
  let x = 0, y = 0;
  for (const a of angles) { x += Math.cos(a); y += Math.sin(a); }
  return x === 0 && y === 0 ? null : Math.atan2(y, x);
}

/**
 * Ring radius grows with hop and with the number of objects on the ring, so
 * objects never overlap. Within a ring, objects keep the angular order of the
 * objects one hop inside that they connect to, then spread evenly.
 */
export function radialLayout(nodes, edges, centerId, { gap = 120, spacing = 15 } = {}) {
  const byId = new Map(nodes.map(node => [idOf(node), node]));
  const { hop, rings, unreachable, next } = hopRings(nodes, edges, centerId);
  const angle = new Map(), radius = [0];
  const order = id => [typeOf(byId.get(id)), labelOf(byId.get(id)), id].join('\u0000');
  for (let h = 1; h < rings.length; h++) {
    const ring = rings[h];
    radius[h] = Math.max(radius[h - 1] + gap * (h === 1 ? 1 : .8), ring.length * spacing / TAU);
    const target = new Map(ring.map(id => [id, circularMean(next.get(id).filter(other => hop.get(other) === h - 1 && angle.has(other)).map(other => angle.get(other)))]));
    const sorted = [...ring].sort((a, b) => {
      const ta = target.get(a) ?? 0, tb = target.get(b) ?? 0;
      return ta - tb || order(a).localeCompare(order(b));
    });
    const step = TAU / sorted.length;
    // Rotate the even spacing to sit as close as possible to the targets.
    const base = h === 1 ? -Math.PI / 2 : circularMean(sorted.map((id, i) => (target.get(id) ?? 0) - i * step)) ?? 0;
    sorted.forEach((id, i) => angle.set(id, base + i * step));
  }
  const outer = (radius[radius.length - 1] || 0) + gap;
  const lost = [...unreachable].sort((a, b) => order(a).localeCompare(order(b)));
  lost.forEach((id, i) => angle.set(id, -Math.PI / 2 + i * TAU / Math.max(1, lost.length)));
  const positions = new Map();
  for (const node of nodes) {
    const id = idOf(node), h = hop.get(id);
    const r = h === undefined ? outer : radius[h];
    const a = angle.get(id) ?? 0;
    positions.set(id, { x: h === 0 ? 0 : r * Math.cos(a), y: h === 0 ? 0 : r * Math.sin(a), hop: h ?? null });
  }
  return { positions, rings, unreachable, radius, outer: lost.length ? outer : null };
}

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function html(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function button(label, onClick, className = 'mks-graph-button') {
  const node = html('button', className, label); node.type = 'button'; node.addEventListener('click', onClick); return node;
}

export function mountRadial(container, { snapshot, onSelect = () => {} } = {}) {
  const data = snapshot?.graph?.data || { nodes: [], edges: [] };
  const nodes = Array.isArray(data.nodes) ? data.nodes : [], edges = Array.isArray(data.edges) ? data.edges : [];
  const byId = new Map(nodes.map(node => [idOf(node), node]));
  const roundId = idOf(nodes.find(node => typeOf(node) === 'DeliveryRound' && labelOf(node) === snapshot?.summary?.delivery_round_id)
    || nodes.find(node => typeOf(node) === 'DeliveryRound') || nodes[0] || {});
  let centerId = roundId, selected = null, destroyed = false, token = 0, timers = [];
  let evidenceNodes = new Set(), evidenceEdges = new Set(), shownNodes = new Set(), shownEdges = new Set();
  let view = null, fullView = null, drag = null, dragged = false, pending = null;

  const root = html('section', 'mks-radial');
  root.setAttribute('aria-label', '홉 거리 그래프');
  const head = html('div', 'mks-radial-head');
  const stats = html('div', 'mks-radial-stats');
  const tools = html('div', 'mks-radial-tools');
  const centerChip = html('span', 'mks-radial-center');
  const recenter = button('선택 객체를 중심으로', () => { if (selected) setCenter(selected); });
  const backToRound = button('회차를 중심으로', () => setCenter(roundId));
  const clear = button('근거 지우기', () => { cancel(); evidenceNodes = new Set(); evidenceEdges = new Set(); shownNodes = new Set(); shownEdges = new Set(); paint(); });
  const fitButton = button('전체 보기', () => setView(fullView));
  tools.append(centerChip, recenter, backToRound, clear, fitButton);
  head.append(stats, tools);
  const canvas = svg('svg', { class: 'mks-radial-svg', role: 'group', 'aria-label': '중심 객체에서 관계 단계별로 펼친 원장 그래프' });
  const legend = html('div', 'mks-radial-legend');
  for (const [area, label] of AREAS) legend.append(html('span', `mks-radial-key mks-area-${area}`, label));
  legend.append(html('span', 'mks-radial-key mks-radial-path', '질의 근거 경로'));
  const note = html('div', 'mks-radial-note', '노드 생략 없음 · 서버 원장 관계만 사용 · 고리 = 중심에서의 관계 단계');
  root.append(head, canvas, legend, note);
  container.replaceChildren(root);

  let layout, elements;
  function build() {
    layout = radialLayout(nodes, edges, centerId);
    const { positions, rings, radius, outer } = layout;
    const ringLayer = svg('g', { class: 'mks-radial-rings' });
    for (let h = 1; h < rings.length; h++) ringLayer.append(svg('circle', { r: radius[h], class: 'mks-radial-ring' }));
    if (outer) ringLayer.append(svg('circle', { r: outer, class: 'mks-radial-ring mks-radial-ring-lost' }));
    const ringLabels = svg('g', { class: 'mks-radial-ring-labels' });
    rings.forEach((ring, h) => {
      if (!h) return;
      const counts = new Map();
      for (const id of ring) counts.set(typeOf(byId.get(id)), (counts.get(typeOf(byId.get(id))) || 0) + 1);
      const [top, count] = [...counts].sort((a, b) => b[1] - a[1])[0];
      const a = -Math.PI * .72, r = radius[h];
      ringLabels.append(svg('text', { x: r * Math.cos(a), y: r * Math.sin(a), 'text-anchor': 'end' }, `${h}단계 · ${ring.length} · ${KIND_KO[top] || top} ${count}`));
    });
    if (outer) ringLabels.append(svg('text', { x: outer * Math.cos(-Math.PI * .72), y: outer * Math.sin(-Math.PI * .72), 'text-anchor': 'end' }, `연결 없음 · ${layout.unreachable.length}`));
    const layers = { edgeDim: svg('g', { class: 'mks-radial-dim' }), edge: svg('g'), nodeDim: svg('g', { class: 'mks-radial-dim' }), node: svg('g'), label: svg('g', { class: 'mks-radial-labels' }) };
    const edgeItems = [];
    for (const edge of edges) {
      const [a, b] = ends(edge), pa = positions.get(a), pb = positions.get(b);
      if (!pa || !pb) continue;
      // BOM row and source-row relations come in dozens; they draw thinner, as in the project graph.
      const row = [a, b].some(id => ['SourceRow', 'BOMLine'].includes(typeOf(byId.get(id))));
      const line = svg('line', { x1: pa.x, y1: pa.y, x2: pb.x, y2: pb.y, class: `mks-radial-edge${row ? ' mks-radial-row' : ''}`, 'data-edge-id': edgeIdOf(edge) });
      line.append(svg('title', {}, `${labelOf(byId.get(a))} → ${labelOf(byId.get(b))}: ${edge.predicate || edge.label || ''}`));
      edgeItems.push({ id: edgeIdOf(edge), line, layer: null });
    }
    const nodeItems = [];
    for (const node of nodes) {
      const id = idOf(node), p = positions.get(id), type = typeOf(node);
      const g = svg('g', { class: `mks-radial-node mks-area-${AREA_OF[type] || 'ev'}${KEY_TYPES.has(type) ? ' mks-radial-key-node' : ''}`, transform: `translate(${p.x},${p.y})`, tabindex: 0, role: 'button', 'data-node-id': id, 'aria-label': `${type} ${labelOf(node)} · ${p.hop === null ? '연결 없음' : `${p.hop}단계`}` });
      g.append(svg('title', {}, `${labelOf(node)} · ${KIND_KO[type] || type} · ${p.hop === null ? '중심과 연결 없음' : `${p.hop}단계`}`));
      g.append(svg('circle', { class: `mks-radial-dot${id === centerId ? ' mks-radial-dot-center' : ''}` }));
      g.addEventListener('click', event => { event.stopPropagation(); if (!dragged) select(id); });
      g.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(id); } });
      const label = svg('text', { x: p.x, y: p.y, dy: id === centerId ? '-1.4em' : '-0.9em', 'text-anchor': 'middle', class: id === centerId ? 'mks-radial-label mks-radial-label-center' : 'mks-radial-label' }, labelOf(node));
      nodeItems.push({ id, g, label, layer: null, key: KEY_TYPES.has(type) });
    }
    const extent = (outer || radius[radius.length - 1] || 200) + 90;
    fullView = { x: -extent, y: -extent, width: extent * 2, height: extent * 2 };
    canvas.replaceChildren(ringLayer, ringLabels, layers.edgeDim, layers.edge, layers.nodeDim, layers.node, layers.label);
    elements = { layers, edgeItems, nodeItems };
    stats.replaceChildren(...[[nodes.length, '객체'], [edges.length, '관계'], [rings.length - 1, '최대 단계']].map(([n, label]) => {
      const item = html('span', 'mks-radial-stat'); item.append(html('b', '', n), document.createTextNode(` ${label}`)); return item;
    }));
    centerChip.textContent = `중심 ${labelOf(byId.get(centerId) || {})}`;
    setView(fullView);
    paint();
  }

  function paint() {
    if (!elements) return;
    const { layers, edgeItems, nodeItems } = elements, focus = shownNodes.size > 0;
    const move = (items, layerFor) => {
      if (!items.some(item => item.layer !== layerFor(item))) return;
      for (const item of items) { item.layer = layerFor(item); item.layer.append(item.line || item.g); }
    };
    for (const item of edgeItems) item.line.classList.toggle('mks-radial-hit', shownEdges.has(item.id));
    for (const item of nodeItems) {
      item.g.classList.toggle('mks-radial-hit', shownNodes.has(item.id));
      item.g.classList.toggle('mks-radial-selected', item.id === selected);
    }
    move(edgeItems, item => focus && !shownEdges.has(item.id) ? layers.edgeDim : layers.edge);
    move(nodeItems, item => focus && !shownNodes.has(item.id) && item.id !== centerId ? layers.nodeDim : layers.node);
    // Labels: centre and selection always; evidence of a kind that appears at most three
    // times (30 boards would only overlap); key objects when nothing is highlighted.
    const perKind = new Map();
    for (const id of shownNodes) { const kind = typeOf(byId.get(id)); perKind.set(kind, (perKind.get(kind) || 0) + 1); }
    const labels = nodeItems.filter(item => item.id === centerId || item.id === selected
      || (shownNodes.has(item.id) && (item.key || perKind.get(typeOf(byId.get(item.id))) <= 3)) || (!focus && item.key));
    layers.label.replaceChildren(...labels.map(item => item.label));
    canvas.classList.toggle('mks-radial-focus', focus);
    recenter.disabled = !selected || selected === centerId;
    backToRound.disabled = centerId === roundId;
    clear.disabled = !evidenceNodes.size;
  }

  function setView(next) {
    if (!next) return;
    view = { ...next };
    canvas.setAttribute('viewBox', `${view.x} ${view.y} ${view.width} ${view.height}`);
    scaleUnits();
  }
  // Labels and dots keep their on-screen size at any zoom: --u is graph units per screen pixel.
  function scaleUnits() {
    const rect = canvas.getBoundingClientRect?.() || {}, scale = Math.min(rect.width / view.width, rect.height / view.height);
    canvas.style?.setProperty('--u', String(scale > 0 && Number.isFinite(scale) ? 1 / scale : 1));
  }
  function setCenter(id) {
    if (!byId.has(id) || destroyed) return;
    cancel(); centerId = id; build();
    shownNodes = new Set(evidenceNodes); shownEdges = new Set(evidenceEdges); paint();
  }
  function select(id) {
    if (!byId.has(id)) return;
    selected = id; paint(); onSelect(byId.get(id));
  }
  function cancel() {
    token++; timers.forEach(clearTimeout); timers = [];
    const resolve = pending; pending = null; resolve?.({ completed: false });
  }

  /** Highlights the answer's own evidence ring by ring from the centre outward. Never fetches or infers. */
  function replay({ nodeIds = [], edgeIds = [] } = {}) {
    if (destroyed) return Promise.resolve({ completed: false });
    cancel();
    const mine = token;
    evidenceNodes = new Set(nodeIds.filter(id => byId.has(id)));
    evidenceEdges = new Set(edgeIds.filter(id => elements?.edgeItems.some(item => item.id === id)));
    const hopOf = id => layout.positions.get(id)?.hop ?? Infinity;
    const stages = [...new Set([...evidenceNodes].map(hopOf))].sort((a, b) => a - b);
    const reduced = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const edgeHop = id => { const edge = edges.find(e => edgeIdOf(e) === id); return edge ? Math.max(...ends(edge).map(hopOf)) : Infinity; };
    const reveal = limit => {
      shownNodes = new Set([...evidenceNodes].filter(id => hopOf(id) <= limit));
      shownEdges = new Set([...evidenceEdges].filter(id => edgeHop(id) <= limit));
      paint();
    };
    if (reduced || stages.length <= 1) { reveal(Infinity); return Promise.resolve({ completed: true }); }
    // cancel() resolves the pending promise, so the answer text is never left waiting.
    return new Promise(resolve => {
      pending = resolve;
      stages.forEach((limit, i) => timers.push(setTimeout(() => {
        if (mine !== token || destroyed) return;
        reveal(limit);
        if (i === stages.length - 1) { pending = null; resolve({ completed: true }); }
      }, 420 * (i + 1))));
    });
  }

  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    if (!view) return;
    const k = event.deltaY > 0 ? 1.12 : 1 / 1.12, width = Math.max(120, Math.min(fullView.width * 2, view.width * k)), height = width * view.height / view.width;
    setView({ x: view.x + (view.width - width) / 2, y: view.y + (view.height - height) / 2, width, height });
  }, { passive: false });
  canvas.addEventListener('pointerdown', event => { if (event.button === 0) { dragged = false; drag = { x: event.clientX, y: event.clientY, view: { ...view } }; } });
  const movePointer = event => {
    if (!drag) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!dragged && Math.abs(dx) + Math.abs(dy) < 5) return;
    dragged = true;
    const rect = canvas.getBoundingClientRect(), scale = Math.min(rect.width / drag.view.width, rect.height / drag.view.height);
    if (scale > 0) setView({ ...drag.view, x: drag.view.x - dx / scale, y: drag.view.y - dy / scale });
  };
  const endPointer = () => { drag = null; };
  window.addEventListener('pointermove', movePointer);
  window.addEventListener('pointerup', endPointer);
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { if (view) scaleUnits(); }) : null;
  observer?.observe(canvas);

  build();
  return {
    replay,
    cancelReplay() { cancel(); },
    focus(id) { if (byId.has(id)) select(id); },
    resize() { if (view) scaleUnits(); },
    destroy() {
      destroyed = true; cancel(); observer?.disconnect();
      window.removeEventListener('pointermove', movePointer);
      window.removeEventListener('pointerup', endPointer);
      root.remove();
    },
  };
}
