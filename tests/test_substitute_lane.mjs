import test from 'node:test';
import assert from 'node:assert/strict';
import { substituteState, flowBanner } from '../web/mks2/procurement.js';

const author = { actor_id: 'actor-a', permitted_actions: ['document.create', 'document.update', 'document.post'] };
const reviewer = { actor_id: 'actor-b', permitted_actions: ['document.create', 'document.review', 'document.approve', 'document.post'] };
const candidate = (counted = 0) => ({ original_item_id: 'item-x', candidate_item_id: 'item-y', candidate_on_hand_qty: 120, unit: 'EA',
  status: counted ? 'APPROVED' : 'NOT_REVIEWED', counted_qty_for_selected_demand: counted });
const snapshot = (shortage, counted = 0, subs = [candidate(counted)]) => ({ summary: { item_id: 'item-x', delivery_round_id: 'R-1' },
  substitutes: subs, material_requirements: { items: [{ item_id: 'item-x', planned_shortage_qty: String(shortage) }] } });
const doc = (status, quantity = 23) => ({ id: 'sub-1234-x', kind: 'SUBSTITUTION', status, created_by: 'actor-a', ledger_version: 10,
  content: { original_item_id: 'item-x', candidate_item_id: 'item-y', quantity } });

test('no substitute candidate for the focus item hides the row', () => {
  assert.equal(substituteState(snapshot(23, 0, []), [], author), null);
});

test('before a BOM is posted there is no shortage, so the row stays hidden', () => {
  const noBom = { ...snapshot(23), material_requirements: { items: [] } };
  assert.equal(substituteState(noBom, [], author), null);
});

test('the author asks for the smaller of the shortage and the candidate stock in one click', () => {
  const state = substituteState(snapshot(23), [], author);
  assert.equal(state.status, '검토 전');
  assert.deepEqual(state.next.map(row => [row.label, row.quantity, row.disabled]), [['대체 승인 요청 · item-y 23개', 23, false]]);
  // The approver account cannot write it: nobody else could approve it.
  assert.equal(substituteState(snapshot(23), [], reviewer).next[0].disabled, true);
  assert.equal(substituteState(snapshot(500), [], author).next[0].quantity, 120);
});

test('approval stays with the other account, then posting counts it', () => {
  const waiting = substituteState(snapshot(23), [doc('DRAFT')], author);
  assert.ok(waiting.next.every(row => row.disabled));
  assert.deepEqual(flowBanner([waiting.next], author), { text: '검토자 승인 대기 1건', role: 'review', action: '검토자로 전환' });
  const review = substituteState(snapshot(23), [doc('DRAFT')], reviewer);
  assert.equal(review.next[0].action, 'review-approve');
  assert.equal(review.next[0].disabled, false);
  assert.equal(substituteState(snapshot(23), [doc('APPROVED')], reviewer).next[0].action, 'post');
  const posted = substituteState(snapshot(0, 23), [doc('POSTED')], author);
  assert.equal(posted.status, '인정 23개');
  assert.deepEqual(posted.next, []);  // no shortage left, nothing more to ask
});

test('a rejected draft is set aside and the request can be made again', () => {
  const rejected = { ...doc('DRAFT'), rejection_reason: 'Need a technical review first' };
  assert.equal(substituteState(snapshot(23), [rejected], author).next[0].action, 'new-substitution');
});
