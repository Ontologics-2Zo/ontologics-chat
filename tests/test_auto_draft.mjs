import test from 'node:test';
import assert from 'node:assert/strict';
import { AUTO_ACTIONS, autoCandidate } from '../web/mks2/procurement.js';

const author = { actor_id: 'actor-a', permitted_actions: ['document.create', 'document.update', 'document.post'] };
const reviewer = { actor_id: 'actor-b', permitted_actions: ['document.create', 'document.review', 'document.approve', 'document.post'] };
const supply = (next, extra = {}) => ({ empty: false, inspected: false, next, ...extra });

test('the agent writes the next material draft only on the author account', () => {
  const next = [{ label: '내부 발주 초안', action: 'new-po', target: 'pr-1' }];
  assert.equal(autoCandidate({ supply: supply(next), fulfillment: { next: [] }, documents: [], identity: author }).entry.action, 'new-po');
  assert.equal(autoCandidate({ supply: supply(next), fulfillment: { next: [] }, documents: [], identity: reviewer }), null);
  assert.equal(autoCandidate({ supply: supply(next), fulfillment: { next: [] }, documents: [], identity: author, enabled: false }), null);
});

test('approvals, purchase requests and person-only decisions are never automatic', () => {
  for (const action of ['review-approve', 'approve', 'post', 'reject', 'revise', 'new-scenario', 'new-substitution']) assert.equal(AUTO_ACTIONS.has(action), false, action);
  const waiting = [{ label: '승인', disabled: true }, { label: '반려', disabled: true }];
  assert.equal(autoCandidate({ supply: supply(waiting), fulfillment: { next: [] }, documents: [], identity: author }), null);
  // No purchase request yet: the supply card is empty, so nothing starts on its own.
  assert.equal(autoCandidate({ supply: { empty: true, next: [] }, fulfillment: { next: [{ action: 'cohort', stage: 'FG_RECEIPT' }] }, documents: [], identity: author }), null);
});

test('board moves start after the purchased LOT passed inspection, one production record at a time', () => {
  const fulfillment = { next: [{ label: '기초 보드 입고 · 10대', action: 'cohort', stage: 'FG_RECEIPT' }, { label: '생산', action: 'lot-production', lot: 'LOT-1', quantity: 3 }] };
  assert.equal(autoCandidate({ supply: supply([]), fulfillment, documents: [], identity: author }), null);
  const picked = autoCandidate({ supply: supply([], { inspected: true }), fulfillment, documents: [], identity: author });
  assert.deepEqual([picked.lane, picked.entry.action, picked.index], ['fulfillment', 'cohort', 0]);
  const production = [{ label: '생산 실적 기록 · 40대', action: 'new-production', target: 'iqc-1' }];
  assert.equal(autoCandidate({ supply: supply(production, { inspected: true }), fulfillment, documents: [], identity: author }).entry.action, 'new-production');
});

test('one draft at a time; its own stale draft is re-saved on the current version', () => {
  const next = [{ label: '내부 발주 초안', action: 'new-po', target: 'pr-1' }];
  const posted = { kind: 'QUOTE', status: 'DRAFT' };  // quotes never block
  const waitingOther = { kind: 'COHORT', status: 'DRAFT', created_by: 'actor-a', content: { note: '직접 작성 · 사람이 쓴 초안', basis_ledger_version: 15 } };
  const staleAgent = { kind: 'COHORT', status: 'REVIEWED', created_by: 'actor-a', content: { note: 'Agent 자동 작성 · 기초 보드', basis_ledger_version: 15 } };
  const freshAgent = { ...staleAgent, content: { ...staleAgent.content, basis_ledger_version: 17 } };
  const rejected = { ...staleAgent, status: 'DRAFT', rejection_reason: '수량 다시 확인' };
  const args = documents => ({ supply: supply(next), fulfillment: { next: [] }, documents, identity: author, ledgerVersion: 17 });
  assert.equal(autoCandidate(args([posted])).entry.action, 'new-po');
  assert.equal(autoCandidate(args([waitingOther])), null);          // a person's draft waits
  assert.equal(autoCandidate(args([freshAgent])), null);            // waiting for the reviewer
  assert.equal(autoCandidate(args([staleAgent])).revise, staleAgent);
  assert.equal(autoCandidate(args([rejected])).entry.action, 'new-po'); // a returned draft is set aside
  assert.equal(autoCandidate({ ...args([staleAgent]), identity: reviewer }), null);
});

test('while a document waits, every new-draft button is locked and the banner ignores it', async () => {
  const { lockCreates, laneWaiting, flowBanner } = await import('../web/mks2/procurement.js');
  const pending = [{ kind: 'SCENARIO', status: 'DRAFT' }];
  const entries = [{ label: '내부 발주 초안', action: 'new-po', target: 'pr-1' }, { label: '승인', action: 'review-approve' }];
  const locked = lockCreates(entries, pending);
  assert.deepEqual(locked.map(entry => [entry.action, Boolean(entry.disabled), Boolean(entry.locked)]),
    [['new-po', true, true], ['review-approve', false, false]]);
  assert.equal(lockCreates(entries, [{ kind: 'QUOTE', status: 'DRAFT' }, { kind: 'PO', status: 'POSTED' }]), entries);
  assert.equal(lockCreates(entries, [{ kind: 'PO', status: 'DRAFT', rejection_reason: '수량 확인' }]), entries);
  const scenarioLocked = lockCreates([{ label: '새 시연 가정 작성', action: 'new-scenario' }], pending);
  assert.equal(laneWaiting(scenarioLocked), 'locked');
  assert.equal(flowBanner([scenarioLocked], reviewer), null);
  const later = lockCreates([{ label: '기초 보드 입고 · 10대', action: 'cohort' }], [], '구매 LOT 수입검사 후 진행');
  assert.deepEqual([later[0].disabled, later[0].lockedText, laneWaiting(later)], [true, '구매 LOT 수입검사 후 진행', 'locked']);
  const choice = [{ label: '새 시연 가정 작성', action: 'new-scenario', disabled: true, optional: true }];
  assert.equal(laneWaiting(choice), 'optional');
  assert.equal(flowBanner([choice], reviewer), null);
});

test('a stale substitution or schedule draft is never re-saved by the agent', () => {
  const next = [{ label: '내부 발주 초안', action: 'new-po', target: 'pr-1' }];
  for (const kind of ['SUBSTITUTION', 'SCENARIO', 'PR']) {
    const stale = { kind, status: 'DRAFT', created_by: 'actor-a', content: { note: '자동 작성 · 사람이 누른 항목', basis_ledger_version: 8 } };
    assert.equal(autoCandidate({ supply: supply(next), fulfillment: { next: [] }, documents: [stale], identity: author, ledgerVersion: 11 }), null, kind);
  }
  const receipt = { kind: 'RECEIPT', status: 'DRAFT', created_by: 'actor-a', content: { note: '자동 작성 · 입하', basis_ledger_version: 8 } };
  assert.equal(autoCandidate({ supply: supply(next), fulfillment: { next: [] }, documents: [receipt], identity: author, ledgerVersion: 11 }).revise, receipt);
});
