// Material shortage alerts shared by the dashboard and digital twin 2.
// Values come only from the server snapshot (planned-input shortage when the
// server gives it). When a newer ledger version arrives in the same session,
// each item's shortage is compared with the previous version for a short time
// so the change is visible (for example 23 → 28 after another order's
// reservation, or → 0 after inspection). No fetch, no model, no write.

export const CHANGE_SHOW_MS = 20000;
let previous = null;   // { scope, version, values: { planned, base } of Map(item_id → shortage|null) }
let current = null;    // { scope, version, delta: { planned, base } of Map(item_id → change), at }

// planned: planned-input basis (yield applied) when the server gives it; base: production target basis.
export function shortageOf(item, basis = 'planned') {
  const value = basis === 'base' ? item?.shortage_qty : item?.planned_shortage_qty ?? item?.shortage_qty;
  if (value == null || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function changeKind(before, after) {
  if (before == null || after == null) return 'unknown';
  if (after === 0 && before > 0) return 'resolved';
  if (after > before) return after > 0 && before === 0 ? 'new' : 'worse';
  return 'better';
}

/** Per-item alert state for this snapshot, with a recent change when one is fresh. */
export function materialAlerts(snapshot, now = Date.now(), { basis = 'planned' } = {}) {
  const meta = snapshot?.meta || {}, requirements = snapshot?.material_requirements || {};
  // Company ledger scope: switching between the demo accounts (author/reviewer)
  // reads the same ledger, so the comparison must survive the switch.
  const scope = String(meta.company_id), version = Number(meta.ledger_version);
  const items = Array.isArray(requirements.items) ? requirements.items : [];
  const blocked = requirements.state === 'BLOCKED_BOM';
  if (!current || current.scope !== scope || current.version !== version) {
    const values = {}, delta = {};
    const newer = previous && previous.scope === scope && Number.isFinite(previous.version) && previous.version < version;
    for (const key of ['planned', 'base']) {
      values[key] = new Map(items.map(item => [item.item_id, blocked ? null : shortageOf(item, key)]));
      delta[key] = new Map();
      const before = newer ? previous.values[key] : null;
      if (before) for (const [id, after] of values[key]) {
        if (!before.has(id)) continue;
        const was = before.get(id);
        if (was !== after) delta[key].set(id, { before: was, after, kind: changeKind(was, after) });
      }
    }
    current = { scope, version, delta, at: now };
    previous = { scope, version, values };
  }
  const left = CHANGE_SHOW_MS - (now - current.at);
  return items.map(item => {
    const shortage = blocked ? null : shortageOf(item, basis);
    const change = left > 0 ? current.delta[basis === 'base' ? 'base' : 'planned'].get(item.item_id) || null : null;
    return { item_id: item.item_id, item_entity_id: item.item_entity_id, unit: item.unit,
      state: shortage == null ? 'unknown' : shortage > 0 ? 'short' : 'ok', shortage,
      change: change ? { ...change, remainingMs: left } : null };
  });
}

/** Short label for a change chip, e.g. "부족 23 → 28 ↑" or "부족 해소 ✓". */
export function changeText(change) {
  if (!change) return '';
  if (change.kind === 'resolved') return `부족 ${change.before} → 0 · 해소 ✓`;
  if (change.kind === 'unknown') return '부족 계산 변경';
  const arrow = change.after > change.before ? '↑' : '↓';
  return `부족 ${change.before} → ${change.after} ${arrow}`;
}

/** For tests: forget what earlier snapshots looked like. */
export function resetMaterialWatch() { previous = null; current = null; }
