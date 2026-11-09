/** Read-only A-Box view. Data, identities and evidence arrive through the view API. */
import { layoutGraph, routeEdges, fitGraphBounds, interpolateView } from './graph-layout.js';

const NS = 'http://www.w3.org/2000/svg';
const STAGE = { x: 0, y: 0, width: 1400, height: 840 };
const TYPES = [['design', '설계·기준'], ['doc', '문서'], ['event', '사건'], ['phys', '실물(LOT)']];

function html(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function svgNode(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function button(label, onClick, className = '') {
  const node = html('button', className, label);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
}

function shortLabel(value) {
  const text = String(value || '');
  return text.length > 24 ? `${text.slice(0, 21)}…` : text;
}

function makeShape(type, attrs = {}) {
  const a = { class: 'mks-node-shape', ...attrs };
  if (type === 'design') return svgNode('rect', { ...a, x: -21, y: -21, width: 42, height: 42, rx: 5 });
  if (type === 'doc') return svgNode('rect', { ...a, x: -16.38, y: -16.38, width: 32.76, height: 32.76, rx: 3, transform: 'rotate(45)' });
  if (type === 'event') return svgNode('circle', { ...a, r: 21 });
  if (type === 'phys') {
    const points = Array.from({ length: 6 }, (_, i) => `${22.05 * Math.cos(i * Math.PI / 3)},${22.05 * Math.sin(i * Math.PI / 3)}`).join(' ');
    return svgNode('polygon', { ...a, points });
  }
  return svgNode('rect', { ...a, x: -30, y: -14, width: 60, height: 28, rx: 14 });
}

export function mountGraph(container, { bundle, onSelect = () => {}, onAsk = () => {}, onBom = () => {} } = {}) {
  const graph = bundle?.graph?.data || { nodes: [], edges: [] };
  const meta = bundle?.graph?.meta || {};
  const presentation = new Map((Array.isArray(bundle?.presentation?.nodes) ? bundle.presentation.nodes : []).map(node => [node.id, node]));
  const lanes = { Customer: 'customer', Supplier: 'supplier', ContractLine: 'contract_line', DeliveryRound: 'delivery_round', ProductionJob: 'production_job', ProductConfiguration: 'configuration', InternalItem: 'item', PartLot: 'lot', InventoryReservation: 'reservation', Reservation: 'reservation', Demand: 'demand', SupplierQuote: 'quotation', Quotation: 'quotation', SourceDocument: 'source_document', BOMSnapshot: 'bom_snapshot', BOMLine: 'bom_line', ProductionReport: 'production_report', FGReceipt: 'fg_receipt', Shipment: 'shipment', Arrival: 'arrival', ProductModel: 'product_model', Revision: 'revision', BoardUnit: 'board_unit', PurchaseRequest: 'purchase_request', PurchaseOrder: 'purchase_order', Receipt: 'receipt', IncomingInspection: 'incoming_inspection', SupplyConfirmation: 'supply_confirmation' };
  const shapeFor = type => ['Customer', 'Supplier'].includes(type) ? 'party' : ['PartLot', 'BoardUnit'].includes(type) ? 'phys' : /Event$|Report$|Receipt$|Arrival$|Shipment$/.test(type) ? 'event' : ['InternalItem', 'ProductConfiguration', 'ProductModel', 'Revision'].includes(type) ? 'design' : 'doc';
  const originalNodes = (Array.isArray(graph.nodes) ? graph.nodes : []).map((raw, index, all) => {
    const node = raw.node_id ? raw : { ...raw, node_id: raw.id, node_type: shapeFor(raw.type || ''), entity_ref: { entity_type: lanes[raw.type] || raw.type, entity_id: raw.id }, attributes: { kind_label: raw.type, display: raw.label || raw.display_id, properties: raw.attributes || {}, badge: meta.synthetic ? '합성 초기 이관' : undefined } };
    const visual = presentation.get(node.node_id);
    if (!visual) {
      if (meta.scope !== 'full_schema') return node;
      const columns = Math.max(6, Math.ceil(Math.sqrt(all.length * 1.3)));
      return { ...node, display_id: node.display_id || node.label, position: node.position || { x: 110 + (index % columns) * 210, y: 110 + Math.floor(index / columns) * 150 } };
    }
    return {
      ...node,
      position: Number.isFinite(visual.x) && Number.isFinite(visual.y) ? { x: visual.x, y: visual.y } : node.position,
      node_type: ['design', 'doc', 'event', 'phys', 'party'].includes(visual.shape) ? visual.shape : node.node_type,
      state_tone: visual.tone ?? node.state_tone,
      label: visual.label ?? node.label,
      attributes: { ...node.attributes, kind_label: visual.kind ?? node.attributes?.kind_label, badge: visual.badge ?? node.attributes?.badge },
    };
  });
  const originalEdges = (Array.isArray(graph.edges) ? graph.edges : []).map(edge => edge.edge_id ? edge : { ...edge, edge_id: edge.id, source_node_id: edge.source, target_node_id: edge.target, relation_type: edge.predicate, evidence_refs: edge.evidence ? [edge.evidence] : [] });
  let layout = layoutGraph(originalNodes, { schema: meta.scope === 'full_schema' });
  let positions = layout.nodes;
  let nodeById = new Map(positions.map(node => [node.node_id, node]));
  const edges = originalEdges.filter(edge => nodeById.has(edge.source_node_id) && nodeById.has(edge.target_node_id));
  const edgeById = new Map(edges.map(edge => [edge.edge_id, edge]));
  let routes = routeEdges(edges, positions);
  let evidenceFocus = [];
  let evidenceRelations = [];
  let fitTarget = null;
  const selectedTypes = new Set(['design', 'doc', 'event', 'phys', 'party']);
  let selected = null;
  let highlightedNodes = new Set();
  let highlightedEdges = new Set();
  let view = { ...STAGE };
  let frame = 0;
  let destroyed = false;
  let replayToken = 0;
  let replaying = false;

  const canvas = html('section', 'mks-graph');
  canvas.setAttribute('aria-label', '2.5D 업무 관계 그래프');
  canvas.dataset.layout = 'layered-25d';
  const svg = svgNode('svg', { class: 'mks-graph-svg', viewBox: '0 0 1400 840', role: 'group', 'aria-label': '업무 객체와 관계. 노드를 선택하면 상세 근거를 봅니다.' });
  const legend = html('div', 'mks-graph-legend');
  legend.setAttribute('aria-label', '노드 종류 필터');
  for (const [type, label] of TYPES) {
    const filter = button('', () => {
      selectedTypes.has(type) ? selectedTypes.delete(type) : selectedTypes.add(type);
      filter.setAttribute('aria-pressed', String(selectedTypes.has(type)));
      render();
    }, 'mks-legend-filter');
    filter.dataset.nodeType = type;
    filter.setAttribute('aria-pressed', 'true');
    filter.title = `${label} 표시 전환`;
    filter.append(html('i', `mks-legend-${type}`), document.createTextNode(label));
    legend.append(filter);
  }
  legend.append(html('span', 'mks-legend-bad', '■ 부족'), html('span', 'mks-legend-warn', '■ 확인 필요'), html('span', '', '┄ 초안'));
  const tools = html('div', 'mks-graph-tools');
  const search = html('input', 'mks-graph-search');
  search.type = 'search'; search.placeholder = '객체 찾기'; search.setAttribute('aria-label', '전체 그래프 객체 찾기');
  search.addEventListener('keydown', event => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    const query = search.value.trim().toLocaleLowerCase();
    if (!query) return;
    const found = positions.find(node => [node.node_id, node.display_id, node.label].some(value => String(value || '').toLocaleLowerCase().includes(query)));
    if (found) focus(found.node_id);
    else { status.hidden = false; status.textContent = '현재 그래프에서 일치하는 객체를 찾지 못했습니다.'; }
  });
  const restoreEvidence = all => {
    if (!evidenceFocus.length) return;
    replayToken++;
    replaying = false;
    for (const type of ['design', 'doc', 'event', 'phys', 'party']) selectedTypes.add(type);
    highlightedNodes = new Set(evidenceFocus); highlightedEdges = new Set(evidenceRelations);
    setLayout(evidenceFocus); fitBounds(all ? layout.evidenceBounds : layout.focusBounds); render();
  };
  const evidenceButton = button('핵심 흐름', () => restoreEvidence(false), 'mks-graph-button');
  const allEvidenceButton = button('전체 근거', () => restoreEvidence(true), 'mks-graph-button');
  evidenceButton.hidden = true;
  allEvidenceButton.hidden = true;
  tools.append(search, evidenceButton, allEvidenceButton, button('BOM 등록', onBom, 'mks-graph-button'), button('전체 보기', reset, 'mks-graph-button'));
  const detail = html('aside', 'mks-graph-detail');
  detail.setAttribute('aria-label', '선택 객체 상세');
  detail.hidden = true;
  const status = html('div', 'mks-graph-status');
  status.setAttribute('role', 'status');
  status.hidden = true;
  if (meta.projection_state && meta.projection_state !== 'ready' && meta.projection_state !== 'READY' && meta.projection_version !== meta.ledger_version) {
    status.textContent = `그래프 동기화 대기 · 원장 v${meta.ledger_version ?? '미확인'} / 그래프 v${meta.projection_version ?? '미확인'}`;
    status.hidden = false;
  }
  const scope = html('div', 'mks-graph-scope', meta.scope_label || `${meta.scope === 'full_schema' ? '전체 온톨로지 · T-Box / SHACL' : '서버 원장'} · 객체 ${originalNodes.length} · 관계 ${originalEdges.length}`);
  scope.dataset.nodeCount = String(originalNodes.length);
  scope.dataset.edgeCount = String(originalEdges.length);
  scope.setAttribute('role', 'note');
  const guide = html('div', 'mks-graph-guide', '2.5D 업무별 배치 · 화살표는 서버 관계 방향');
  canvas.append(svg, legend, status, tools, detail, scope, guide);
  container.replaceChildren(canvas);

  function updateView(next, animate = false) {
    if (!['x', 'y', 'width', 'height'].every(key => Number.isFinite(next[key])) || next.width <= 0 || next.height <= 0) return;
    cancelAnimationFrame(frame);
    const start = { ...view };
    const startAt = performance.now();
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const tick = now => {
      if (destroyed) return;
      const progress = !animate || reduceMotion ? 1 : Math.max(0, Math.min(1, (now - startAt) / 420));
      view = interpolateView(start, next, progress);
      svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.width} ${view.height}`);
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  }

  function aspect() {
    const rect = svg.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 ? rect.width / rect.height : STAGE.width / STAGE.height;
  }

  function setLayout(ids = []) {
    layout = layoutGraph(originalNodes, { evidenceIds: ids, schema: meta.scope === 'full_schema' });
    positions = layout.nodes;
    nodeById = new Map(positions.map(node => [node.node_id, node]));
    routes = routeEdges(edges, positions);
    guide.textContent = layout.focusing
      ? `핵심 ${layout.coreCount}개 · 행 근거 ${layout.rowCount}개 객체 (아래층) · 전체 ${originalNodes.length}개 유지`
      : '2.5D 업무별 배치 · 화살표는 서버 관계 방향';
    canvas.dataset.evidenceFocus = String(layout.focusing);
  }

  function fitBounds(bounds) {
    fitTarget = { ...bounds };
    updateView(fitGraphBounds(bounds, aspect()), true);
  }

  function fitEvidence() { fitBounds(layout.focusBounds); }

  function fit(ids) {
    const found = ids.map(id => nodeById.get(id)).filter(Boolean);
    if (!found.length) return;
    const left = Math.min(...found.map(node => node.x)) - 135;
    const right = Math.max(...found.map(node => node.x)) + 135;
    const top = Math.min(...found.map(node => node.y)) - 100;
    const bottom = Math.max(...found.map(node => node.y)) + 100;
    fitBounds({ x: left, y: top, width: right - left, height: bottom - top });
  }

  function showDetail(id) {
    const node = nodeById.get(id);
    if (!node) { detail.hidden = true; return; }
    const attributes = node.attributes || {};
    detail.replaceChildren(html('div', 'mks-detail-kind', attributes.kind_label || node.entity_ref?.entity_type || node.node_type), html('h4', '', node.display_id || node.node_id));
    detail.append(html('div', 'mks-detail-description', `${attributes.display || node.label || ''}${attributes.badge ? ` · ${attributes.badge}` : ''}`));
    if (attributes.properties && typeof attributes.properties === 'object') {
      const properties = html('dl', 'mks-detail-properties');
      for (const [name, value] of Object.entries(attributes.properties)) properties.append(html('dt', '', name), html('dd', '', value == null ? '미확인' : typeof value === 'object' ? JSON.stringify(value) : value));
      detail.append(properties);
    }
    if (node.evidence) {
      const evidence = html('dl', 'mks-detail-properties');
      for (const [name, value] of Object.entries(node.evidence)) evidence.append(html('dt', '', name), html('dd', '', value == null ? '미확인' : typeof value === 'object' ? JSON.stringify(value) : value));
      detail.append(evidence);
    }
    const relations = html('div', 'mks-detail-relations');
    for (const edge of edges.filter(edge => edge.source_node_id === id || edge.target_node_id === id)) {
      const outgoing = edge.source_node_id === id;
      const other = outgoing ? edge.target_node_id : edge.source_node_id;
      const chip = button(nodeById.get(other)?.display_id || nodeById.get(other)?.label || other, () => focus(other), 'mks-detail-chip');
      chip.title = `${outgoing ? '→' : '←'} ${edge.label || edge.relation_type} · ${edge.edge_id}`;
      chip.setAttribute('aria-label', `${other}, ${outgoing ? '나가는' : '들어오는'} 관계 ${edge.label || edge.relation_type}`);
      chip.dataset.edgeId = edge.edge_id;
      relations.append(chip);
    }
    detail.append(relations, html('div', 'mks-detail-note', `${node.node_id} · ${meta.scope === 'full_schema' ? `온톨로지 ${meta.ontology_version || '미확인'}` : `그래프 v${meta.projection_version ?? '미확인'}`}`));
    detail.hidden = false;
  }

  function showEdge(edge) {
    replayToken++;
    replaying = false;
    svg.classList.remove('mks-replaying');
    detail.replaceChildren(html('div', 'mks-detail-kind', '관계'), html('h4', '', edge.relation_type), html('div', 'mks-detail-description', edge.label || ''));
    detail.append(html('p', 'mks-detail-description', `${edge.source_node_id} → ${edge.target_node_id}`));
    const properties = html('dl', 'mks-detail-properties');
    for (const [name, value] of [['관계 ID', edge.edge_id], ['상태', edge.state], ['근거 수', edge.evidence_refs?.length ?? null], ['원장 버전', edge.source_version]]) properties.append(html('dt', '', name), html('dd', '', value ?? '미확인'));
    detail.append(properties);
    detail.hidden = false;
  }

  function render() {
    for (const filter of legend.querySelectorAll('button')) filter.setAttribute('aria-pressed', String(selectedTypes.has(filter.dataset.nodeType)));
    const fragment = document.createDocumentFragment();
    const defs = svgNode('defs');
    const arrow = svgNode('marker', { id: 'mks-relation-arrow', viewBox: '0 0 10 10', refX: 8, refY: 5, markerWidth: 5, markerHeight: 5, orient: 'auto-start-reverse' });
    arrow.append(svgNode('path', { d: 'M 0 1 L 9 5 L 0 9 z', fill: 'context-stroke' })); defs.append(arrow);
    const planes = svgNode('g', { class: 'mks-planes', 'aria-hidden': 'true' });
    for (const layer of layout.layers) {
      const { x, y, width: w, height: h, depth: d } = layer;
      const plane = svgNode('g', { class: `mks-plane mks-plane-${layer.id}`, 'data-layer-id': layer.id });
      plane.append(svgNode('path', { class: 'mks-plane-side', d: `M ${x} ${y + h} l ${d} ${d} h ${w} v ${-h} l ${-d} ${-d} v ${h} Z` }),
        svgNode('rect', { class: 'mks-plane-face', x, y, width: w, height: h, rx: 14 }),
        svgNode('text', { class: 'mks-plane-title', x: x + 20, y: y + 29 }, `${layer.label} · ${layer.count}`));
      planes.append(plane);
    }
    const edgeLayer = svgNode('g', { class: 'mks-edges' });
    const nodeLayer = svgNode('g', { class: 'mks-nodes' });
    for (const edge of edges) {
      const source = nodeById.get(edge.source_node_id);
      const target = nodeById.get(edge.target_node_id);
      if (!selectedTypes.has(source.node_type) || !selectedTypes.has(target.node_type)) continue;
      const group = svgNode('g', { class: `mks-edge${['candidate', 'draft'].includes(edge.state) ? ` mks-${edge.state}` : ''}${highlightedEdges.has(edge.edge_id) ? ' mks-hit' : ''}`, 'data-edge-id': edge.edge_id });
      if (source.layoutLayer === 'source-rows' || target.layoutLayer === 'source-rows') group.classList.add('mks-row-evidence');
      const title = svgNode('title', {}, `${source.node_id} → ${target.node_id}: ${edge.relation_type} (${edge.label || ''})`);
      const route = routes.get(edge.edge_id);
      const line = svgNode('path', { class: 'mks-edge-line', d: route.d, 'marker-end': 'url(#mks-relation-arrow)' });
      const hitArea = svgNode('path', { class: 'mks-edge-hit-area', d: route.d, tabindex: 0, role: 'button', 'aria-label': `${source.node_id}에서 ${target.node_id}, ${edge.relation_type}` });
      const label = svgNode('text', { x: route.x, y: route.y, 'text-anchor': 'middle' }, edge.label || edge.relation_type);
      hitArea.addEventListener('click', event => { event.stopPropagation(); showEdge(edge); });
      hitArea.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); showEdge(edge); } });
      group.append(title, line, hitArea, label);
      edgeLayer.append(group);
    }
    for (const node of positions) {
      if (!selectedTypes.has(node.node_type)) continue;
      const attributes = node.attributes || {};
      const tone = ['bad', 'warn', 'ok', 'info'].includes(node.state_tone) ? node.state_tone : null;
      const group = svgNode('g', { class: `mks-node${tone ? ` mks-tone-${tone}` : ''}${node.state === 'candidate' || node.state === 'draft' ? ' mks-draft' : ''}${highlightedNodes.has(node.node_id) ? ' mks-hit' : ''}${selected === node.node_id ? ' mks-selected' : ''}`, transform: `translate(${node.x},${node.y})`, tabindex: 0, role: 'button', 'aria-label': `${attributes.kind_label || node.node_type} ${node.node_id}`, 'data-node-id': node.node_id });
      const title = svgNode('title', {}, `${node.display_id || node.node_id} · ${node.label || ''}`);
      const ring = node.node_type === 'party' ? svgNode('rect', { class: 'mks-node-ring', x: -30, y: -14, width: 60, height: 28, rx: 14 }) : svgNode('circle', { class: 'mks-node-ring', r: 22 });
      const labelY = node.node_type === 'party' ? 28 : 36;
      const shadow = svgNode('ellipse', { class: 'mks-node-shadow', cx: 6, cy: 27, rx: 24, ry: 8 });
      group.append(title, shadow, ring, makeShape(node.node_type), svgNode('text', { class: 'mks-node-id', y: labelY, 'text-anchor': 'middle' }, shortLabel(node.display_id || node.node_id)), svgNode('text', { class: 'mks-node-label', y: labelY + 14, 'text-anchor': 'middle' }, shortLabel(node.label)));
      if (attributes.badge) group.append(svgNode('text', { class: 'mks-node-badge', y: labelY + 27, 'text-anchor': 'middle' }, attributes.badge));
      group.addEventListener('click', event => { event.stopPropagation(); if (!dragMoved) select(node.node_id); });
      group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(node.node_id); } });
      nodeLayer.append(group);
    }
    fragment.append(defs, planes, edgeLayer, nodeLayer);
    svg.replaceChildren(fragment);
    svg.classList.toggle('mks-dimming', highlightedNodes.size > 0);
    svg.classList.toggle('mks-replaying', replaying);
    if (selected) showDetail(selected);
  }

  function select(id) {
    if (!nodeById.has(id)) return;
    replayToken++;
    replaying = false;
    selected = id;
    render();
    onSelect(nodeById.get(id));
  }

  function focus(id) {
    if (!nodeById.has(id)) return;
    selectedTypes.add(nodeById.get(id).node_type);
    select(id);
    fit([id]);
  }

  function reset() {
    replayToken++;
    replaying = false;
    selected = null;
    highlightedNodes.clear();
    highlightedEdges.clear();
    for (const type of ['design', 'doc', 'event', 'phys', 'party']) selectedTypes.add(type);
    for (const filter of legend.querySelectorAll('button')) filter.setAttribute('aria-pressed', 'true');
    detail.hidden = true;
    setLayout();
    if (positions.length) fitEvidence();
    else updateView({ ...STAGE }, true);
    render();
  }

  /** Replay only explicitly supplied evidence; this never fetches, infers or writes. */
  async function replay(evidence = {}) {
    const token = ++replayToken;
    replaying = true;
    const ids = evidence.nodeIds || evidence.evidence_node_ids || [];
    const suppliedEdges = evidence.edgeIds || [];
    const hops = evidence.hops || evidence.graph_path || [];
    const resolved = suppliedEdges.filter(id => edgeById.has(id));
    for (const hop of hops) {
      if (!Array.isArray(hop) || hop.length < 2) continue;
      // Preserve identity, direction and predicate: ambiguous parallel hops need an edge ID.
      const matching = edges.filter(edge => edge.source_node_id === hop[0] && edge.target_node_id === hop[1]);
      if (matching.length === 1) resolved.push(matching[0].edge_id);
    }
    const requestedNodes = new Set(ids.filter(id => nodeById.has(id)));
    highlightedNodes = new Set();
    highlightedEdges = new Set();
    for (const hop of hops) for (const id of hop) if (nodeById.has(id)) requestedNodes.add(id);
    for (const id of resolved) {
      const edge = edgeById.get(id);
      requestedNodes.add(edge.source_node_id);
      requestedNodes.add(edge.target_node_id);
    }
    for (const id of requestedNodes) selectedTypes.add(nodeById.get(id).node_type);
    evidenceFocus = [...requestedNodes];
    evidenceRelations = [...new Set(resolved)];
    evidenceButton.hidden = !evidenceFocus.length;
    allEvidenceButton.hidden = !evidenceFocus.length;
    if (evidenceFocus.length) { setLayout(evidenceFocus); fitEvidence(); }
    // Establish the complete camera frame once; reveal evidence without camera jumps.
    const firstEdge = edgeById.get(resolved[0]);
    if (firstEdge) highlightedNodes.add(firstEdge.source_node_id);
    render();
    const animated = evidenceRelations.filter(id => {
      const edge = edgeById.get(id);
      return nodeById.get(edge.source_node_id).layoutLayer !== 'source-rows'
        && nodeById.get(edge.target_node_id).layoutLayer !== 'source-rows';
    }).slice(0, 16);
    for (const id of animated) {
      if (destroyed || token !== replayToken) return {completed:false};
      if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) await new Promise(resolve => setTimeout(resolve, 240));
      if (destroyed || token !== replayToken) return {completed:false};
      const edge = edgeById.get(id);
      highlightedEdges.add(id);
      highlightedNodes.add(edge.source_node_id);
      highlightedNodes.add(edge.target_node_id);
      render();
    }
    if (destroyed || token !== replayToken) return {completed:false};
    highlightedNodes = requestedNodes;
    highlightedEdges = new Set(evidenceRelations);
    replaying = false;
    render();
    return {completed:true};
  }

  let drag = null;
  let dragMoved = false;
  svg.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    dragMoved = false;
    drag = { x: event.clientX, y: event.clientY, view: { ...view } };
  });
  const movePointer = event => {
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) < 5 && !dragMoved) return;
    dragMoved = true;
    const rect = svg.getBoundingClientRect();
    const scale = Math.min(rect.width / drag.view.width, rect.height / drag.view.height);
    if (!(scale > 0)) return;
    fitTarget = null;
    updateView({ ...drag.view, x: drag.view.x - dx / scale, y: drag.view.y - dy / scale });
  };
  const endPointer = () => { drag = null; };
  window.addEventListener('pointermove', movePointer);
  window.addEventListener('pointerup', endPointer);
  svg.addEventListener('wheel', event => {
    event.preventDefault();
    replayToken++;
    replaying = false;
    svg.classList.remove('mks-replaying');
    const factor = event.deltaY > 0 ? 1.12 : 1 / 1.12;
    const width = Math.max(280, Math.min(Math.max(4200, positions.length * 220), view.width * factor));
    const height = width / aspect();
    fitTarget = null;
    updateView({ x: view.x + (view.width - width) / 2, y: view.y + (view.height - height) / 2, width, height });
  }, { passive: false });
  svg.addEventListener('click', event => {
    if (dragMoved || event.target !== svg) return;
    selected = null;
    highlightedNodes.clear();
    highlightedEdges.clear();
    detail.hidden = true;
    render();
  });
  canvas.addEventListener('keydown', event => { if (event.key === 'Escape') reset(); });
  const resize = () => { if (!destroyed && fitTarget) updateView(fitGraphBounds(fitTarget, aspect())); };
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);
  render();
  if (positions.length) fitEvidence();
  return {
    focus,
    replay,
    cancelReplay() { replayToken++; replaying = false; svg.classList.remove('mks-replaying'); },
    reset,
    resize,
    destroy() {
      destroyed = true;
      replayToken++;
      cancelAnimationFrame(frame);
      resizeObserver.disconnect();
      window.removeEventListener('pointermove', movePointer);
      window.removeEventListener('pointerup', endPointer);
      canvas.remove();
    },
  };
}
