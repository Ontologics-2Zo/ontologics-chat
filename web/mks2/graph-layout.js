/** Coordinates and camera steps only; identities, relations and ledger facts stay unchanged. */
const typeOf = node => String(node.entity_ref?.entity_type || node.type || node.node_type || '').replace(/[^a-z0-9]/gi, '').toLowerCase();
const ROW_TYPES = new Set(['sourcerow', 'bomline']);
const BOM_TYPES = new Set(['sourcedocument', 'sourcesnapshot', 'sourcerow', 'bom', 'bomsnapshot', 'bomline', 'bomapplication', 'approval', 'approvalgrant', 'documentversion']);
export const isSourceRow = node => ROW_TYPES.has(typeOf(node));
const compare = (a, b) => String(a.node_id).localeCompare(String(b.node_id));
const goldenAngle = Math.PI * (3 - Math.sqrt(5));

export function graphBounds(nodes, ids = nodes.map(node => node.node_id)) {
  const included = new Set(ids), found = nodes.filter(node => included.has(node.node_id));
  if (!found.length) return { x: 0, y: 0, width: 1400, height: 840 };
  const left = Math.min(...found.map(node => node.x)) - 135, right = Math.max(...found.map(node => node.x)) + 135;
  const top = Math.min(...found.map(node => node.y)) - 100, bottom = Math.max(...found.map(node => node.y)) + 100;
  return { x: left, y: top, width: right - left, height: bottom - top };
}

/** One stable connected network. Questions affect the camera, never coordinates. */
export function layoutGraph(nodes, { edges = [], schema = false } = {}) {
  const sorted = [...nodes].sort(compare), byId = new Map(sorted.map(node => [node.node_id, node]));
  const links = [...edges].filter(edge => byId.has(edge.source_node_id) && byId.has(edge.target_node_id))
    .sort((a, b) => String(a.edge_id).localeCompare(String(b.edge_id)));
  const adjacent = new Map(sorted.map(node => [node.node_id, new Set()]));
  for (const edge of links) {
    adjacent.get(edge.source_node_id).add(edge.target_node_id);
    adjacent.get(edge.target_node_id).add(edge.source_node_id);
  }
  const rows = schema ? [] : sorted.filter(isSourceRow), rowIds = new Set(rows.map(node => node.node_id));
  const bom = schema ? [] : sorted.filter(node => BOM_TYPES.has(typeOf(node)));
  const bomIds = new Set(bom.map(node => node.node_id));
  const placed = new Map(), counts = new Map();
  const anchors = {
    customer:[90,110], contractline:[260,110], deliveryround:[260,300], productionjob:[470,300],
    productionreport:[430,130], fgreceipt:[470,470], shipment:[360,600], arrival:[360,740],
    configuration:[690,300], productconfiguration:[690,300], productmodel:[650,130], revision:[790,120],
    item:[900,180], internalitem:[900,180], lot:[1120,180], partlot:[1120,180],
    reservation:[1100,30], inventoryreservation:[1100,30], demand:[1310,30],
    quotation:[1120,560], supplierquote:[1120,560], supplieroffer:[1120,560], supplier:[1320,560],
    purchaserequest:[800,400], purchaseorder:[1100,400], receipt:[900,650], partsreceiptline:[900,650],
    incominginspection:[1090,720], inspectionresult:[1090,720], supplierconfirmation:[1300,400],
  };
  const hubs = sorted.filter(node => !rowIds.has(node.node_id));
  const bomHubs = hubs.filter(node => bomIds.has(node.node_id));
  const cloud = { x: 1000, y: rows.length ? 1100 + Math.sqrt(rows.length) * 24 : 600 };
  for (const [index, node] of hubs.entries()) {
    const type = typeOf(node), occurrence = counts.get(type) || 0; counts.set(type, occurrence + 1);
    let x, y;
    if (!schema && bomIds.has(node.node_id)) {
      const i = bomHubs.indexOf(node), radius = 105 + Math.sqrt(i + 1) * 48;
      x = cloud.x + Math.cos(i * goldenAngle) * radius;
      y = cloud.y - 440 + Math.sin(i * goldenAngle) * radius * .7;
    } else if (!schema && anchors[type]) {
      const base = anchors[type], angle = occurrence * goldenAngle;
      x = base[0] + (occurrence ? Math.cos(angle) * (100 + occurrence * 30) : 0);
      y = base[1] + (occurrence ? Math.sin(angle) * (100 + occurrence * 30) : 0);
    } else {
      const angle = index * goldenAngle, radius = 100 + Math.sqrt(index + 1) * 72;
      x = 700 + Math.cos(angle) * radius; y = 400 + Math.sin(angle) * radius;
    }
    placed.set(node.node_id, { ...node, x, y, layoutCluster: bomIds.has(node.node_id) ? 'bom' : 'network', anchorX: x, anchorY: y });
  }
  // Relax real hub-to-hub relationships around the reference business flow.
  // This is deterministic and uses no runtime animation or random seed.
  const hubLinks = links.filter(edge => !rowIds.has(edge.source_node_id) && !rowIds.has(edge.target_node_id));
  for (let iteration = 0; iteration < 100; iteration++) {
    const delta = new Map(hubs.map(node => [node.node_id, { x: 0, y: 0 }]));
    for (let i = 0; i < hubs.length; i++) for (let j = i + 1; j < hubs.length; j++) {
      const a = placed.get(hubs[i].node_id), b = placed.get(hubs[j].node_id);
      const dx = b.x - a.x || .01, dy = b.y - a.y || .01, distance = Math.hypot(dx, dy);
      if (distance >= 190) continue;
      const force = (190 - distance) * .08;
      delta.get(a.node_id).x -= dx / distance * force; delta.get(a.node_id).y -= dy / distance * force;
      delta.get(b.node_id).x += dx / distance * force; delta.get(b.node_id).y += dy / distance * force;
    }
    for (const edge of hubLinks) {
      const a = placed.get(edge.source_node_id), b = placed.get(edge.target_node_id);
      const dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy) || 1;
      const force = Math.max(-80, Math.min(120, distance - 210)) * .025;
      delta.get(a.node_id).x += dx / distance * force; delta.get(a.node_id).y += dy / distance * force;
      delta.get(b.node_id).x -= dx / distance * force; delta.get(b.node_id).y -= dy / distance * force;
    }
    for (const node of hubs) {
      const point = placed.get(node.node_id), change = delta.get(node.node_id);
      point.x += change.x + (point.anchorX - point.x) * .04;
      point.y += change.y + (point.anchorY - point.y) * .04;
    }
  }
  // SourceRow and BOMLine are separate objects joined only by their actual
  // relationships. Keep connected row sets adjacent inside one BOM cloud,
  // including all historical snapshots, without merging repeated labels.
  const seen = new Set(), components = [];
  for (const node of rows) {
    if (seen.has(node.node_id)) continue;
    const component = [], pending = [node.node_id]; seen.add(node.node_id);
    while (pending.length) {
      const id = pending.shift(); component.push(byId.get(id));
      for (const neighbor of [...adjacent.get(id)].sort()) if (rowIds.has(neighbor) && !seen.has(neighbor)) { seen.add(neighbor); pending.push(neighbor); }
    }
    components.push(component.sort(compare));
  }
  components.forEach((component, index) => {
    const angle = index * goldenAngle, radius = 250 + Math.sqrt(index + 1) * 67;
    const center = { x: cloud.x + Math.cos(angle) * radius, y: cloud.y + Math.sin(angle) * radius * .85 };
    component.forEach((node, i) => {
      const orbit = component.length === 1 ? 0 : 52 + Math.max(0, component.length - 2) * 12;
      const phase = -Math.PI / 2 + 2 * Math.PI * i / component.length;
      placed.set(node.node_id, { ...node, x: center.x + Math.cos(phase) * orbit, y: center.y + Math.sin(phase) * orbit,
        layoutCluster: 'bom', rowGroup: component[0].node_id });
    });
  });
  // Resolve glyph collisions across the cloud and its connecting business
  // nodes. Prefer moving source rows so the reference business flow stays put.
  const points = sorted.map(node => placed.get(node.node_id));
  for (let pass = 0; pass < 80; pass++) {
    let collisions = 0;
    for (let i = 0; i < points.length; i++) for (let j = i + 1; j < points.length; j++) {
      const a = points[i], b = points[j], dx = b.x - a.x || .01, dy = b.y - a.y || .01;
      const distance = Math.hypot(dx, dy);
      if (distance >= 92) continue;
      collisions++;
      const amount = 92 - distance + .05;
      const aRow = rowIds.has(a.node_id), bRow = rowIds.has(b.node_id);
      const weightA = aRow !== bRow ? Number(aRow) : .5, weightB = 1 - weightA;
      a.x -= dx / distance * amount * weightA; a.y -= dy / distance * amount * weightA;
      b.x += dx / distance * amount * weightB; b.y += dy / distance * amount * weightB;
    }
    if (!collisions) break;
  }
  const positions = nodes.map(node => {
    const { anchorX, anchorY, ...point } = placed.get(node.node_id);
    return { ...point, x: Math.round(point.x * 1000) / 1000, y: Math.round(point.y * 1000) / 1000 };
  });
  const coreIds = positions.filter(node => !isSourceRow(node)).map(node => node.node_id);
  return { nodes: positions, layers: [], focusing: false, coreIds, bomIds: [...bomIds], rowCount: rows.length,
    coreCount: coreIds.length, bounds: graphBounds(positions), focusBounds: graphBounds(positions, coreIds),
    bomBounds: graphBounds(positions, [...bomIds]) };
}

/** Resolve actual evidence once, deduplicate identities, and batch dense BOM rows. */
export function planEvidenceReplay(nodes, edges, evidence = {}) {
  const byId = new Map(nodes.map(node => [node.node_id, node])), edgeById = new Map(edges.map(edge => [edge.edge_id, edge]));
  const requested = new Set((evidence.nodeIds || evidence.evidence_node_ids || []).filter(id => byId.has(id)));
  const resolved = new Set((evidence.edgeIds || evidence.evidence_edge_ids || []).filter(id => edgeById.has(id)));
  for (const hop of evidence.hops || evidence.graph_path || []) {
    if (!Array.isArray(hop) || hop.length !== 2) continue;
    for (const id of hop) if (byId.has(id)) requested.add(id);
    const matching = edges.filter(edge => edge.source_node_id === hop[0] && edge.target_node_id === hop[1]);
    if (matching.length === 1) resolved.add(matching[0].edge_id);
  }
  const steps = [], bulkEdges = [], bulkNodes = new Set();
  for (const id of resolved) {
    const edge = edgeById.get(id);
    if (!byId.has(edge.source_node_id) || !byId.has(edge.target_node_id)) continue;
    const pair = [edge.source_node_id, edge.target_node_id]; pair.forEach(id => requested.add(id));
    if (pair.some(id => isSourceRow(byId.get(id)))) { bulkEdges.push(id); pair.forEach(id => bulkNodes.add(id)); }
    else steps.push({ kind: 'relation', nodeIds: pair, edgeIds: [id] });
  }
  for (const id of requested) if (isSourceRow(byId.get(id))) bulkNodes.add(id);
  // Present connected business evidence consecutively, without changing the
  // directed edge itself or claiming a new relationship between components.
  const rank = id => ({ customer: 0, contractline: 1, deliveryround: 2, productionjob: 3,
    configuration: 4, productconfiguration: 4 }[typeOf(byId.get(id))] ?? 10);
  const ordered = [], remaining = [...steps].sort((a, b) =>
    Math.min(...a.nodeIds.map(rank)) - Math.min(...b.nodeIds.map(rank)) || String(a.edgeIds[0]).localeCompare(String(b.edgeIds[0])));
  const visited = new Set();
  while (remaining.length) {
    const connected = remaining.findIndex(step => step.nodeIds.some(id => visited.has(id)));
    const step = remaining.splice(connected < 0 ? 0 : connected, 1)[0];
    ordered.push(step); step.nodeIds.forEach(id => visited.add(id));
  }
  // Keep the usual business path readable, but do not make a large evidence
  // request wait minutes. Remaining real relationships stay in one visible batch.
  const individual = ordered.slice(0, 14), remainder = ordered.slice(14);
  steps.splice(0, steps.length, ...individual);
  if (remainder.length) steps.push({ kind: 'relations',
    nodeIds: [...new Set(remainder.flatMap(step => step.nodeIds))],
    edgeIds: remainder.flatMap(step => step.edgeIds) });
  const stepped = new Set([...steps.flatMap(step => step.nodeIds), ...bulkNodes]);
  const extra = [...requested].filter(id => !stepped.has(id));
  if (extra.length) steps.push({ kind: 'nodes', nodeIds: extra, edgeIds: [] });
  if (bulkNodes.size) steps.push({ kind: 'bom', nodeIds: [...bulkNodes], edgeIds: bulkEdges });
  return { nodeIds: [...requested], edgeIds: [...resolved], steps };
}

/** Presentation duration only; these are never server/tool execution timings. */
export function replayStepTiming(kind) {
  if (kind === 'bom') return { duration: 2400, drawDelay: 650, drawDuration: 1400 };
  if (kind === 'relations') return { duration: 2200, drawDelay: 650, drawDuration: 1200 };
  if (kind === 'relation') return { duration: 1450, drawDelay: 250, drawDuration: 800 };
  return { duration: 650, drawDelay: 0, drawDuration: 0 };
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
