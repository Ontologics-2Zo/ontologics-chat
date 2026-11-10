import test from 'node:test';
import assert from 'node:assert/strict';
import { supplyProgress } from '../web/mks2/procurement.js';

const snapshot = shortage => ({ summary: { item_id: 'item-x' }, material_requirements: { items: [{ item_id: 'item-x', planned_shortage_qty: String(shortage), shortage_qty: '1' }] } });
const pr = (status, extra = {}) => ({ id: 'pr-00000001-x', kind: 'PR', status, quantity: 23, remaining_qty: 23, created_by: 'actor-a', approved_by: status === 'APPROVED' || status === 'POSTED' ? 'actor-b' : null,
  content: { material_basis: 'planned', resolved: { item_id: 'item-x', unit: 'EA', supplier_id: 'sup-d', quote: { lead_business_days: 3 } } }, ...extra });
const author = { actor_id: 'actor-a', permitted_actions: ['document.create', 'document.post'] };
const reviewer = { actor_id: 'actor-b', permitted_actions: ['document.review', 'document.approve', 'document.post'] };
const states = progress => progress.steps.map(step => step.state).join(',');

test('no purchase request means no progress card', () => {
  assert.equal(supplyProgress([], snapshot(23), author), null);
});

test('a saved draft has requested approval; only another account can review it', () => {
  const mine = supplyProgress([pr('DRAFT')], snapshot(23), author);
  assert.equal(states(mine), 'done,done,current,pending');
  assert.equal(mine.status, '승인 진행');
  assert.equal(mine.next.disabled, true);
  assert.deepEqual(mine.rows[0], ['구매 요청', 'pr-00000 · 23개']);
  assert.deepEqual(mine.rows[1], ['내부 발주', '미작성']);
  assert.match(mine.rows[2][1], /견적 리드타임 3영업일 · 필요일 근거 없음/);
  const other = supplyProgress([pr('DRAFT')], snapshot(23), reviewer);
  assert.deepEqual([other.next.label, other.next.action], ['검토 기록', 'review']);
});

test('posting the PO confirms; a grown shortage is reported but nothing is changed', () => {
  const po = { id: 'po-00000002-x', kind: 'PO', status: 'POSTED', parent_id: 'pr-00000001-x', quantity: 23, created_by: 'actor-a', approved_by: 'actor-b', content: { pr_id: 'pr-00000001-x' } };
  const done = supplyProgress([pr('POSTED'), po], snapshot(0), reviewer);
  assert.equal(states(done), 'done,done,done,done'); assert.equal(done.status, '발주 확정');
  assert.equal(done.rows[1][1], 'po-00000 · sup-d · 23개'); assert.equal(done.next, null);
  const grown = supplyProgress([pr('DRAFT')], snapshot(28), author);
  assert.equal(grown.shortfall, 5);
  const posted = supplyProgress([pr('POSTED')], snapshot(0), author);
  assert.deepEqual([posted.status, posted.next.action], ['발주 작성 필요', 'new-po']);
});

test('rejected or cancelled requests and other items are ignored', () => {
  assert.equal(supplyProgress([pr('REJECTED')], snapshot(23), author), null);
  assert.equal(supplyProgress([pr('DRAFT', { cancelled_by: 'c' })], snapshot(23), author), null);
  const other = pr('DRAFT'); other.content.resolved.item_id = 'item-y';
  assert.equal(supplyProgress([other], snapshot(23), author), null);
});

test('a draft on an older ledger version asks its author to revise, not to review', () => {
  const old = pr('DRAFT'); old.content.basis_ledger_version = 9;
  const view = { ...snapshot(28), meta: { ledger_version: 10 } };
  const mine = supplyProgress([old], view, { ...author, permitted_actions: ['document.update'] });
  assert.deepEqual(mine.stale, { basis: 9, current: 10 });
  assert.deepEqual([mine.next.label, mine.next.action, mine.next.disabled], ['초안 수정', 'revise', false]);
  const other = supplyProgress([old], view, reviewer);
  assert.equal(other.next.disabled, true);
  assert.equal(supplyProgress([old], { ...snapshot(23), meta: { ledger_version: 9 } }, reviewer).stale, null);
});
