/** Presentation coordinates only: no inferred relations, filtering or ledger mutation. */
const LAYERS = [
  { id: 'promise', label: '고객 · 도착 약속', types: ['customer', 'contract_line', 'delivery_round'] },
  { id: 'production', label: '생산 · 완제품', types: ['production_job', 'production_report', 'fg_receipt', 'board_unit'] },
  { id: 'delivery', label: '출하 · 고객 도착', types: ['shipment', 'arrival'] },
  { id: 'design', label: '설계 · BOM 근거', types: ['configuration', 'product_model', 'revision', 'source_document', 'source_snapshot', 'bom', 'bom_snapshot', 'bom_line', 'bom_application', 'approval', 'document_version'] },
  { id: 'material', label: '자재 · 재고 · 예약', types: ['item', 'lot', 'reservation', 'demand'] },
  { id: 'supply', label: '조달 · 입하 · 수입검사', types: ['supplier', 'quotation', 'purchase_request', 'purchase_order', 'receipt', 'incoming_inspection', 'supply_confirmation', 'PurchaseRequest', 'PurchaseOrder', 'PurchaseOrderLine', 'PartsReceiptLine', 'InspectionResult', 'SupplierConfirmation'] },
  { id: 'other', label: '그 밖의 원장 근거', types: [] },
];

const typeOf = node => node.entity_ref?.entity_type || node.node_type;
const layerOf = node => LAYERS.find(layer => layer.types.includes(typeOf(node))) || LAYERS.at(-1);
const order = (a, b) => String(typeOf(a)).localeCompare(String(typeOf(b))) || String(a.node_id).localeCompare(String(b.node_id));

/** Shallow planes retain front-facing labels. Large source sets use a bounded grid. */
export function layoutGraph(nodes, { evidenceIds = [], schema = false } = {}) {
  const evidence = new Set(evidenceIds);
  const focusing = !schema && nodes.some(node => evidence.has(node.node_id));
  const isRow = node => ['bom_line', 'source_row', 'SourceRow', 'BOMLine'].includes(typeOf(node));
  const rowEvidence = focusing ? nodes.filter(node => evidence.has(node.node_id) && isRow(node)).sort(order) : [];
  const placed = new Map(), layers = [];
  const groups = schema
    ? [{ id: 'schema', label: '전체 온톨로지 · 클래스와 제약', nodes: [...nodes].sort(order) }]
    : LAYERS.map(layer => ({ ...layer, nodes: nodes.filter(node => layerOf(node).id === layer.id && (!focusing || evidence.has(node.node_id) && !isRow(node))).sort(order) })).filter(group => group.nodes.length);
  const total = groups.reduce((sum, group) => sum + group.nodes.length, 0);
  const maxColumns = focusing ? 3 : Math.max(4, Math.ceil(Math.sqrt(total * .8)));
  const shelfWidth = focusing ? 1820 : Math.max(1820, Math.ceil(Math.sqrt(total * 1.8)) * 180);
  let x = 0, y = 0, rowHeight = 0;
  function place(group, columns, originX, originY, context = false) {
    const rows = Math.ceil(group.nodes.length / columns);
    const width = columns * 180 + 70, height = rows * 118 + 100;
    const layer = { id: group.id, label: group.label, count: group.nodes.length, x: originX, y: originY, width, height, depth: 18, context };
    layers.push(layer);
    group.nodes.forEach((node, i) => placed.set(node.node_id, { ...node,
      x: originX + 125 + i % columns * 180,
      y: originY + 78 + Math.floor(i / columns) * 118,
      layoutLayer: group.id,
    }));
    return layer;
  }
  for (const group of groups) {
    const columns = Math.max(1, Math.min(maxColumns, Math.ceil(Math.sqrt(group.nodes.length * 1.2))));
    const width = columns * 180 + 70;
    if (x && x + width > shelfWidth) { x = 0; y += rowHeight + 72; rowHeight = 0; }
    const layer = place(group, columns, x, y);
    x += width + 64; rowHeight = Math.max(rowHeight, layer.height);
  }
  const focusBounds = { x: -28, y: -54, width: Math.max(600, ...layers.map(layer => layer.x + layer.width)) + 72,
    height: Math.max(300, y + rowHeight) + 110 };
  if (rowEvidence.length) {
    const rowLayer = place({ id: 'source-rows', label: '질문 근거 · 원문 행·BOM 행 객체', nodes: rowEvidence },
      Math.max(4, Math.ceil(Math.sqrt(rowEvidence.length * 1.5))), 0, y + rowHeight + 150);
    y = rowLayer.y; rowHeight = rowLayer.height;
  }
  const evidenceBounds = { x: -28, y: -54, width: Math.max(600, ...layers.map(layer => layer.x + layer.width)) + 72,
    height: Math.max(300, y + rowHeight) + 110 };
  if (focusing) {
    const remaining = nodes.filter(node => !evidence.has(node.node_id)).sort((a, b) => layerOf(a).id.localeCompare(layerOf(b).id) || order(a, b));
    if (remaining.length) place({ id: 'context', label: '전체 원장 · 질문 밖 객체도 보존', nodes: remaining },
      Math.max(4, Math.ceil(Math.sqrt(remaining.length * 1.5))), 0, y + rowHeight + 240, true);
  }
  return { nodes: nodes.map(node => placed.get(node.node_id)), layers, focusing, focusBounds, evidenceBounds,
    coreCount: total, rowCount: rowEvidence.length };
}

/** Each edge remains independent, including parallel, reverse and self relations. */
export function routeEdges(edges, nodes) {
  const byId = new Map(nodes.map(node => [node.node_id, node]));
  const pairs = new Map();
  for (const edge of edges) {
    const key = JSON.stringify([edge.source_node_id, edge.target_node_id].sort());
    if (!pairs.has(key)) pairs.set(key, []);
    pairs.get(key).push(edge);
  }
  const routes = new Map();
  for (const pair of pairs.values()) {
    pair.sort((a, b) => String(a.edge_id).localeCompare(String(b.edge_id)));
    pair.forEach((edge, index) => {
      const a = byId.get(edge.source_node_id), b = byId.get(edge.target_node_id);
      if (!a || !b) return;
      if (a.node_id === b.node_id) {
        const spread = 55 + index * 22;
        routes.set(edge.edge_id, { d: `M ${a.x - 16} ${a.y - 17} C ${a.x - spread} ${a.y - spread * 1.8}, ${a.x + spread} ${a.y - spread * 1.8}, ${a.x + 16} ${a.y - 17}`, x: a.x, y: a.y - spread * 1.4 });
        return;
      }
      const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
      const direction = a.node_id < b.node_id ? 1 : -1;
      const offset = ((index - (pair.length - 1) / 2) * 44 + 18) * direction;
      if ((a.layoutLayer === 'source-rows' || b.layoutLayer === 'source-rows') && Math.abs(dy) > 180) {
        // Separate source evidence descends between planes instead of radiating across the core.
        const sign = Math.sign(dy), startY = a.y + sign * 25, endY = b.y - sign * 29;
        const middleY = (startY + endY) / 2 + offset;
        routes.set(edge.edge_id, { d: `M ${a.x} ${startY} C ${a.x} ${middleY}, ${b.x} ${middleY}, ${b.x} ${endY}`, x: (a.x + b.x) / 2, y: middleY - 8 });
        return;
      }
      const cx = (a.x + b.x) / 2 - dy / length * offset;
      const cy = (a.y + b.y) / 2 + dx / length * offset;
      const port = (node, target, distance) => {
        const size = Math.hypot(target.x - node.x, target.y - node.y) || 1;
        return { x: node.x + (target.x - node.x) / size * distance, y: node.y + (target.y - node.y) / size * distance };
      };
      const start = port(a, { x: cx, y: cy }, 25), end = port(b, { x: cx, y: cy }, 29);
      routes.set(edge.edge_id, { d: `M ${start.x} ${start.y} Q ${cx} ${cy} ${end.x} ${end.y}`, x: (start.x + 2 * cx + end.x) / 4, y: (start.y + 2 * cy + end.y) / 4 - 8 });
    });
  }
  return routes;
}

export function fitGraphBounds(bounds, ratio = 5 / 3) {
  const aspect = Number.isFinite(ratio) && ratio > 0 ? Math.max(.25, Math.min(6, ratio)) : 5 / 3;
  let width = Math.max(560, bounds.width), height = Math.max(340, bounds.height);
  if (width / height > aspect) height = width / aspect; else width = height * aspect;
  return { x: bounds.x + (bounds.width - width) / 2, y: bounds.y + (bounds.height - height) / 2, width, height };
}

export function interpolateView(start, next, progress) {
  const t = Number.isFinite(progress) ? Math.max(0, Math.min(1, progress)) : 1;
  const weight = 1 - (1 - t) ** 3;
  return Object.fromEntries(['x', 'y', 'width', 'height'].map(key => [key, start[key] + (next[key] - start[key]) * weight]));
}
