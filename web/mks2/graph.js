/** Read-only A-Box view. Data, identities and evidence arrive through the view API. */
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

/** Stable semantic lanes reproduce the reference layout without fixture-specific IDs. */
function positionNodes(nodes, edges) {
  const nodesById = new Map(nodes.map(node => [node.node_id, node]));
  const slots = new Map();
  const count = new Map();
  const occurrence = type => {
    const index = count.get(type) || 0;
    count.set(type, index + 1);
    return index;
  };
  const predicateOf = edge => edge.predicate || edge.label || edge.relation_type?.split(/[#/:]/).at(-1);
  const targetOf = (id, predicate) => edges.some(edge => edge.target_node_id === id && predicateOf(edge) === predicate);
  const sourceOf = (id, predicate) => edges.some(edge => edge.source_node_id === id && predicateOf(edge) === predicate);
  for (const node of nodes) {
    const id = node.node_id;
    const type = node.entity_ref?.entity_type || node.node_type;
    let point;
    const provided = node.position;
    if (provided && Number.isFinite(provided.x) && Number.isFinite(provided.y)) {
      point = [provided.x, provided.y];
    } else {
      switch (type) {
        case 'customer': point = [90, 110 + 150 * occurrence(type)]; break;
        case 'contract_line': point = [260, 110 + 150 * occurrence(type)]; break;
        case 'delivery_round':
          point = targetOf(id, 'HAS_ROUND') || sourceOf(id, 'roundOfContractLine') ? [260, 300] : [90, 450 + 170 * occurrence('other-round')]; break;
        case 'production_job': point = [470, 300 + 170 * occurrence(type)]; break;
        case 'production_report': point = [430, 130 + 150 * occurrence(type)]; break;
        case 'fg_receipt': point = [470, 470 + 150 * occurrence(type)]; break;
        case 'shipment': point = targetOf(id, 'ROUND_SHIPMENT') ? [90, 750] : [360 + 220 * occurrence(type), 600]; break;
        case 'arrival': point = [360 + 220 * occurrence(type), 740]; break;
        case 'configuration': point = [690, 300 + 160 * occurrence(type)]; break;
        case 'item':
          if (targetOf(id, 'SUBSTITUTE_CANDIDATE')) point = [900, 40];
          else if (targetOf(id, 'QUOTES_ITEM') || sourceOf(id, 'HAS_LOT') || targetOf(id, 'offersItem') || targetOf(id, 'lotOf')) point = [900, 160];
          else { const index = occurrence('other-item'); point = [720 + (index % 2) * 180, 450 + Math.floor(index / 2) * 140]; }
          break;
        case 'lot': point = [1100, 160 + 170 * occurrence(type)]; break;
        case 'reservation': point = [1100, 40 + 120 * occurrence(type)]; break;
        case 'demand': point = [1300, 40 + 120 * occurrence(type)]; break;
        case 'quotation': point = [1110, 560 + 105 * occurrence(type)]; break;
        case 'supplier': point = [1300, 560 + 105 * occurrence(type)]; break;
        case 'bom_snapshot': point = [575, 400 + 120 * occurrence(type)]; break;
        case 'bom_line': point = [690, 160 + 290 * occurrence(type)]; break;
        case 'revision': point = [575, 125 + 100 * occurrence(type)]; break;
        case 'source_document': point = [455, 40 + 100 * occurrence(type)]; break;
        case 'purchase_request': point = [800, 240 + 110 * occurrence(type)]; break;
        case 'purchase_order': point = [1110, 380 + 110 * occurrence(type)]; break;
        case 'receipt': point = [900, 570 + 110 * occurrence(type)]; break;
        default: {
          const index = occurrence('other');
          point = [180 + (index % 6) * 200, 850 + Math.floor(index / 6) * 140];
        }
      }
    }
    slots.set(id, { ...node, x: point[0], y: point[1] });
  }
  // Align a supplier with its quote; arrival nodes follow their own shipment.
  for (const edge of edges) {
    const source = slots.get(edge.source_node_id);
    const target = slots.get(edge.target_node_id);
    if (!source || !target) continue;
    if (['QUOTED_BY', 'offerFromSupplier'].includes(predicateOf(edge)) && nodesById.get(target.node_id)?.entity_ref?.entity_type === 'supplier') target.y = source.y;
    if (predicateOf(edge) === 'ARRIVED_AS') {
      target.x = source.x === 90 ? 215 : source.x;
      target.y = source.y === 750 ? 760 : source.y + 140;
    }
  }
  return [...slots.values()];
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
  const lanes = { Customer: 'customer', Supplier: 'supplier', ContractLine: 'contract_line', DeliveryRound: 'delivery_round', ProductionJob: 'production_job', ProductConfiguration: 'configuration', InternalItem: 'item', PartLot: 'lot', InventoryReservation: 'reservation', Reservation: 'reservation', Demand: 'demand', SupplierQuote: 'quotation', Quotation: 'quotation', SourceDocument: 'source_document', BOMSnapshot: 'bom_snapshot', BOMLine: 'bom_line', ProductionReport: 'production_report', FGReceipt: 'fg_receipt', Shipment: 'shipment', Arrival: 'arrival' };
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
  const positions = positionNodes(originalNodes, originalEdges);
  const nodeById = new Map(positions.map(node => [node.node_id, node]));
  const edges = originalEdges.filter(edge => nodeById.has(edge.source_node_id) && nodeById.has(edge.target_node_id));
  const edgeById = new Map(edges.map(edge => [edge.edge_id, edge]));
  const selectedTypes = new Set(['design', 'doc', 'event', 'phys', 'party']);
  let selected = null;
  let highlightedNodes = new Set();
  let highlightedEdges = new Set();
  let view = { ...STAGE };
  let frame = 0;
  let destroyed = false;
  let replayToken = 0;

  const canvas = html('section', 'mks-graph');
  canvas.setAttribute('aria-label', 'A-Box 업무 관계 그래프');
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
  tools.append(search, button('BOM 등록', onBom, 'mks-graph-button'), button('전체 보기', reset, 'mks-graph-button'));
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
  canvas.append(svg, legend, status, tools, detail, scope);
  container.replaceChildren(canvas);

  function updateView(next, animate = false) {
    cancelAnimationFrame(frame);
    const start = { ...view };
    const startAt = performance.now();
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const tick = now => {
      if (destroyed) return;
      const progress = !animate || reduceMotion ? 1 : Math.min(1, (now - startAt) / 420);
      const weight = 1 - (1 - progress) ** 3;
      for (const key of ['x', 'y', 'width', 'height']) view[key] = start[key] + (next[key] - start[key]) * weight;
      svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.width} ${view.height}`);
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  }

  function fit(ids) {
    const found = ids.map(id => nodeById.get(id)).filter(Boolean);
    if (!found.length) return;
    const left = Math.min(...found.map(node => node.x)) - 110;
    const right = Math.max(...found.map(node => node.x)) + 110;
    const top = Math.min(...found.map(node => node.y)) - 70;
    const bottom = Math.max(...found.map(node => node.y)) + 90;
    let width = Math.max(560, right - left);
    let height = Math.max(340, bottom - top);
    const ratio = 1400 / 840;
    if (width / height > ratio) height = width / ratio;
    else width = height * ratio;
    updateView({ x: (left + right - width) / 2, y: (top + bottom - height) / 2, width, height }, true);
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
    const edgeLayer = svgNode('g', { class: 'mks-edges' });
    const nodeLayer = svgNode('g', { class: 'mks-nodes' });
    for (const edge of edges) {
      const source = nodeById.get(edge.source_node_id);
      const target = nodeById.get(edge.target_node_id);
      if (!selectedTypes.has(source.node_type) || !selectedTypes.has(target.node_type)) continue;
      const group = svgNode('g', { class: `mks-edge${['candidate', 'draft'].includes(edge.state) ? ` mks-${edge.state}` : ''}${highlightedEdges.has(edge.edge_id) ? ' mks-hit' : ''}`, 'data-edge-id': edge.edge_id });
      const title = svgNode('title', {}, `${source.node_id} → ${target.node_id}: ${edge.relation_type} (${edge.label || ''})`);
      const line = svgNode('line', { x1: source.x, y1: source.y, x2: target.x, y2: target.y });
      const hitArea = svgNode('line', { class: 'mks-edge-hit-area', x1: source.x, y1: source.y, x2: target.x, y2: target.y, tabindex: 0, role: 'button', 'aria-label': `${source.node_id}에서 ${target.node_id}, ${edge.relation_type}` });
      const vertical = Math.abs(source.y - target.y) > Math.abs(source.x - target.x);
      const label = svgNode('text', { x: (source.x + target.x) / 2 + (vertical ? 9 : 0), y: (source.y + target.y) / 2 + (vertical ? 4 : -6), 'text-anchor': vertical ? 'start' : 'middle' }, edge.label || edge.relation_type);
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
      const title = svgNode('title', {}, node.label || node.node_id);
      const ring = node.node_type === 'party' ? svgNode('rect', { class: 'mks-node-ring', x: -30, y: -14, width: 60, height: 28, rx: 14 }) : svgNode('circle', { class: 'mks-node-ring', r: 22 });
      const labelY = node.node_type === 'party' ? 28 : 36;
      group.append(title, ring, makeShape(node.node_type), svgNode('text', { class: 'mks-node-id', y: labelY, 'text-anchor': 'middle' }, node.display_id || node.node_id), svgNode('text', { class: 'mks-node-label', y: labelY + 14, 'text-anchor': 'middle' }, node.label));
      if (attributes.badge) group.append(svgNode('text', { class: 'mks-node-badge', y: labelY + 27, 'text-anchor': 'middle' }, attributes.badge));
      group.addEventListener('click', event => { event.stopPropagation(); if (!dragMoved) select(node.node_id); });
      group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(node.node_id); } });
      nodeLayer.append(group);
    }
    fragment.append(edgeLayer, nodeLayer);
    svg.replaceChildren(fragment);
    svg.classList.toggle('mks-dimming', highlightedNodes.size > 0);
    if (selected) showDetail(selected);
  }

  function select(id) {
    if (!nodeById.has(id)) return;
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
    selected = null;
    highlightedNodes.clear();
    highlightedEdges.clear();
    for (const type of ['design', 'doc', 'event', 'phys', 'party']) selectedTypes.add(type);
    for (const filter of legend.querySelectorAll('button')) filter.setAttribute('aria-pressed', 'true');
    detail.hidden = true;
    if (positions.length) fit(positions.map(node => node.node_id));
    else updateView({ ...STAGE }, true);
    render();
  }

  /** Replay only explicitly supplied evidence; this never fetches, infers or writes. */
  async function replay(evidence = {}) {
    const token = ++replayToken;
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
    const firstEdge = edgeById.get(resolved[0]);
    if (firstEdge) {
      highlightedNodes.add(firstEdge.source_node_id);
      fit([firstEdge.source_node_id, firstEdge.target_node_id]);
    }
    render();
    for (const id of [...new Set(resolved)]) {
      if (destroyed || token !== replayToken) return {completed:false};
      if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) await new Promise(resolve => setTimeout(resolve, 520));
      if (destroyed || token !== replayToken) return {completed:false};
      const edge = edgeById.get(id);
      highlightedEdges.add(id);
      highlightedNodes.add(edge.source_node_id);
      highlightedNodes.add(edge.target_node_id);
      render();
      fit([...highlightedNodes]);
    }
    if (destroyed || token !== replayToken) return {completed:false};
    highlightedNodes = requestedNodes;
    render();
    if (highlightedNodes.size) fit([...highlightedNodes]);
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
    updateView({ ...drag.view, x: drag.view.x - dx / scale, y: drag.view.y - dy / scale });
  };
  const endPointer = () => { drag = null; };
  window.addEventListener('pointermove', movePointer);
  window.addEventListener('pointerup', endPointer);
  svg.addEventListener('wheel', event => {
    event.preventDefault();
    const factor = event.deltaY > 0 ? 1.12 : 1 / 1.12;
    const width = Math.max(280, Math.min(Math.max(4200, positions.length * 220), view.width * factor));
    const height = width * 840 / 1400;
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
  render();
  if (positions.length) fit(positions.map(node => node.node_id));
  return {
    focus,
    replay,
    cancelReplay() { replayToken++; },
    reset,
    resize() { svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.width} ${view.height}`); },
    destroy() {
      destroyed = true;
      replayToken++;
      cancelAnimationFrame(frame);
      window.removeEventListener('pointermove', movePointer);
      window.removeEventListener('pointerup', endPointer);
      canvas.remove();
    },
  };
}
