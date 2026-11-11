import test from 'node:test';
import assert from 'node:assert/strict';
import { supplyProgress } from '../web/mks2/procurement.js';

const snapshot = (shortage, version = 9) => ({ summary: { item_id: 'item-x' }, meta: { ledger_version: version },
  material_requirements: { items: [{ item_id: 'item-x', planned_shortage_qty: String(shortage), shortage_qty: '1' }] } });
const quote = { id: 'quote-1', kind: 'QUOTE', status: 'POSTED', content: { unit: 'EA', lead_business_days: 3, resolved: { supplier_id: 'sup-d' } } };
const pr = (status, extra = {}) => ({ id: 'pr-00000001-x', kind: 'PR', status, quantity: 23, remaining_qty: 23, created_by: 'actor-a',
  approved_by: ['APPROVED', 'POSTED'].includes(status) ? 'actor-b' : null,
  content: { material_basis: 'planned', basis_ledger_version: 9, resolved: { item_id: 'item-x', unit: 'EA', supplier_id: 'sup-d', quote: { lead_business_days: 3 } } }, ...extra });
const po = (status, extra = {}) => ({ id: 'po-00000002-x', kind: 'PO', status, parent_id: 'pr-00000001-x', quantity: 23, confirmed_qty: 0, receivable_qty: 0,
  created_by: 'actor-a', approved_by: status === 'POSTED' ? 'actor-b' : null, content: { basis_ledger_version: 9 }, ...extra });
const author = { actor_id: 'actor-a', permitted_actions: ['document.create', 'document.update', 'document.post'] };
const reviewer = { actor_id: 'actor-b', permitted_actions: ['document.create', 'document.review', 'document.approve', 'document.post'] };
const states = progress => progress.steps.map(step => step.state).join(',');
const actions = progress => progress.next.map(entry => entry.action || `(${entry.label})`);

test('before any request the card says there is no order yet', () => {
  const empty = supplyProgress([quote], snapshot(23), author);
  assert.equal(empty.empty, true); assert.equal(empty.status, '발주 없음');
  assert.match(empty.text, /아직 item-x 구매 요청·발주가 없습니다/);
});

test('an open request form is the 초안 step; the form itself is the 승인 요청 action', () => {
  const editor = { kind: 'PR', values: { quote_id: 'quote-1', quantity: 23 } };
  const draft = supplyProgress([quote], snapshot(23), author, editor);
  assert.equal(states(draft), 'current,pending,pending,pending');
  assert.deepEqual(actions(draft), []);
  assert.deepEqual(draft.rows[1], ['내부 발주', '미작성 · 견적 sup-d']);
});

test('a saved draft waits for another account; the reviewer approves in one button', () => {
  const mine = supplyProgress([pr('DRAFT')], snapshot(23), author);
  assert.equal(states(mine), 'done,current,pending,pending');
  assert.deepEqual(actions(mine), ['(승인)', '(반려)']);
  assert.match(mine.notes[0], /작성자 본인은 승인할 수 없습니다/);
  const other = supplyProgress([pr('DRAFT')], snapshot(23), reviewer);
  assert.deepEqual(actions(other), ['review-approve', 'reject']);
  const approved = supplyProgress([pr('APPROVED')], snapshot(23), author);
  assert.deepEqual([approved.status, actions(approved)[0]], ['확정 대기', 'post']);
});

test('after posting, the card follows the order through receipt and inspection', () => {
  assert.deepEqual(actions(supplyProgress([pr('POSTED')], snapshot(0), author)), ['new-po']);
  const ordered = supplyProgress([pr('POSTED'), po('POSTED')], snapshot(0), author);
  assert.equal(ordered.status, '입하 예정'); assert.equal(states(ordered), 'done,done,done,done');
  assert.deepEqual(actions(ordered), ['new-confirmation']);
  assert.equal(ordered.rows[1][1], 'PO-PO00 · sup-d · 23개');  // short label derived from the real ID
  const confirmed = supplyProgress([pr('POSTED'), po('POSTED', { confirmed_qty: 23, receivable_qty: 23 })], snapshot(0), author);
  assert.deepEqual(actions(confirmed), ['new-receipt']);
  const receipt = { id: 'rc-1', kind: 'RECEIPT', status: 'POSTED', parent_id: 'po-00000002-x', quantity: 23, remaining_qty: 23, content: {} };
  const waiting = supplyProgress([pr('POSTED'), po('POSTED', { confirmed_qty: 23 }), receipt], snapshot(0), author);
  assert.deepEqual([waiting.status, actions(waiting)[0]], ['검사 대기', 'new-iqc']);
  assert.equal(waiting.next[0].label, '수입검사 합격 · 23개');
  const iqc = { id: 'iqc-1', kind: 'IQC', status: 'POSTED', parent_id: 'rc-1', quantity: 23, content: {} };
  const done = supplyProgress([pr('POSTED'), po('POSTED', { confirmed_qty: 23 }), receipt, iqc], snapshot(0), author);
  assert.equal(done.status, '가용'); assert.deepEqual(done.next, []);
  const poDraft = supplyProgress([pr('POSTED'), po('DRAFT')], snapshot(0), reviewer);
  assert.equal(poDraft.focusLabel, '내부 발주'); assert.equal(states(poDraft), 'done,current,pending,pending');
});

test('a grown shortage or a changed ledger basis is reported; only the author revises', () => {
  const grown = supplyProgress([pr('DRAFT')], snapshot(28), author);
  assert.deepEqual([grown.shortfall, grown.needed], [5, 28]);
  const stale = supplyProgress([pr('DRAFT')], snapshot(28, 10), author);
  assert.deepEqual(stale.stale, { basis: 9, current: 10 });
  assert.deepEqual(actions(stale), ['revise']);
  assert.equal(supplyProgress([pr('DRAFT')], snapshot(28, 10), reviewer).next[0].disabled, true);
});

test('rejected or cancelled requests and other items are ignored', () => {
  assert.equal(supplyProgress([pr('REJECTED')], snapshot(23), author).empty, true);
  assert.equal(supplyProgress([pr('DRAFT', { cancelled_by: 'c' })], snapshot(23), author).empty, true);
  const other = pr('DRAFT'); other.content.resolved.item_id = 'item-y';
  assert.equal(supplyProgress([other], snapshot(23), author).empty, true);
});

test('after inspection the card continues to production, shipment and arrival', () => {
  const chain = [pr('POSTED'), po('POSTED', { confirmed_qty: 23 }),
    { id: 'rc-1', kind: 'RECEIPT', status: 'POSTED', parent_id: 'po-00000002-x', quantity: 23, remaining_qty: 0, lot_code: 'LOT-N', content: {} },
    { id: 'iqc-1', kind: 'IQC', status: 'POSTED', parent_id: 'rc-1', passed_qty: 23, quantity: 23, content: {} },
    { id: 'prod-1', kind: 'PRODUCTION', status: 'POSTED', parent_id: 'iqc-1', quantity: 3, content: {} }];
  const view = { ...snapshot(0), summary: { item_id: 'item-x', production_remaining_qty: 37 },
    fulfillment: { boards: { 'B-1': { status: 'PRODUCED', production_id: 'prod-1' }, 'B-2': { status: 'PRODUCED', production_id: 'prod-1' } } } };
  const produced = supplyProgress(chain, view, author);
  assert.equal(produced.status, '생산 진행');
  assert.deepEqual(actions(produced), ['new-production', 'new-next']);
  assert.match(produced.next[1].label, /완성품 입고 기록 · 2대/);
  assert.ok(produced.rows.some(([label, value]) => label === '입하 · 검사' && value === 'LOT-N · 입하 23개 · 합격 23개'));
  assert.ok(produced.rows.some(([label, value]) => label === '보드 이행' && value === '생산 2 · 출하 0 · 도착 0대'));
  const fgDraft = supplyProgress([...chain, { id: 'fg-1', kind: 'FG_RECEIPT', status: 'DRAFT', parent_id: 'prod-1', created_by: 'actor-a', content: {} }], view, reviewer);
  assert.equal(fgDraft.focusLabel, '완성품 입고'); assert.deepEqual(actions(fgDraft), ['review-approve', 'reject']);
});

test('a request written by the approver says how to continue, and the author can reject it', () => {
  const byReviewer = pr('DRAFT', { created_by: 'actor-b' });
  const own = supplyProgress([byReviewer], snapshot(23), reviewer);
  assert.deepEqual(actions(own), ['(승인)', '(반려)']);
  assert.match(own.notes[0], /승인할 다른 계정이 없습니다/);
  const asAuthor = supplyProgress([byReviewer], snapshot(23), { ...author, permitted_actions: [...author.permitted_actions, 'document.review'] });
  assert.deepEqual(actions(asAuthor), ['(승인)', 'reject']);
  assert.match(asAuthor.notes[0], /승인 권한이 없습니다/);
});

test('the approving account does not get the next creation step', () => {
  const asReviewer = supplyProgress([pr('POSTED')], snapshot(0), reviewer);
  assert.equal(asReviewer.next[0].action, 'new-po'); assert.equal(asReviewer.next[0].disabled, true);
  assert.match(asReviewer.notes[0], /작성자 계정이 만듭니다/);
  assert.equal(supplyProgress([pr('POSTED')], snapshot(0), author).next[0].disabled, false);
});

test('a returned draft is set aside so the author can start the step again', () => {
  const returned = po('DRAFT', { created_by: 'actor-b', rejection_reason: '검토자 작성' });
  const view = supplyProgress([pr('POSTED'), returned], snapshot(0), author);
  assert.equal(view.status, '발주 작성 필요'); assert.deepEqual(actions(view), ['new-po']);
  assert.match(view.notes.at(-1), /반려된 초안 1건/);
  assert.equal(supplyProgress([pr('DRAFT', { rejection_reason: 'x' })], snapshot(23), author).empty, true);
});

test('the schedule scenario card follows the same author/approver rule', async () => {
  const { scenarioState } = await import('../web/mks2/procurement.js');
  const none = scenarioState([], author);
  assert.equal(none.status, '가정 없음'); assert.equal(none.next[0].action, 'new-scenario'); assert.equal(none.next[0].disabled, false);
  assert.equal(scenarioState([], reviewer).next[0].disabled, true);
  const draft = { id: 'scn-0001', kind: 'SCENARIO', status: 'DRAFT', created_by: 'actor-a',
    content: { inspection_business_days: 1, daily_capacity: 20, ship_prep_business_days: 1, transit_days: 2 } };
  const mine = scenarioState([draft], author);
  assert.deepEqual(mine.next.map(entry => [entry.action, entry.disabled]), [['review-approve', true], ['reject', true]]);
  assert.deepEqual(scenarioState([draft], reviewer).next.map(entry => entry.action), ['review-approve', 'reject']);
  assert.deepEqual(mine.values[1], ['생산능력', '20대/영업일']);
  const posted = scenarioState([{ ...draft, status: 'POSTED', ledger_version: 10 }], author);
  assert.equal(posted.status, '반영됨 · 원장 v10'); assert.equal(posted.next[0].label, '새 가정 작성');
  assert.equal(scenarioState([{ ...draft, rejection_reason: 'x' }], author).status, '가정 없음');
});

test('the delivery-round card offers baseline moves, LOT production and next board steps by one click', async () => {
  const { fulfillmentState } = await import('../web/mks2/procurement.js');
  const view = { summary: { item_id: 'item-x', production_remaining_qty: 40, promise_remaining_qty: 68, arrived_qty: 32 },
    legacy_cohort: { good_not_received: 10, warehouse: 15, in_transit: 3 },
    material_requirements: { items: [{ item_id: 'item-x', quantity_per_unit: '1' }] },
    inventory: [{ lot_id: 'LOT-1', item_id: 'item-x', available_qty: 20 }, { lot_id: 'LOT-2', item_id: 'item-x', available_qty: 23 }],
    fulfillment: { boards: { 'B-1': { status: 'PRODUCED', production_id: 'prod-1' } } } };
  const production = { id: 'prod-1', kind: 'PRODUCTION', status: 'POSTED', content: { lot_code: 'LOT-2' } };
  const state = fulfillmentState(view, [production], author);
  assert.deepEqual(state.next.map(entry => entry.label),
    ['기초 보드 입고 · 10대', '기초 보드 출하 · 15대', '기초 보드 도착 · 3대', '생산 실적 기록 · 23대 (LOT-2)', '완성품 입고 · 1대']);
  assert.ok(fulfillmentState(view, [production], reviewer).next.every(entry => entry.disabled));
  const draft = { id: 'ch-1', kind: 'COHORT', status: 'DRAFT', created_by: 'actor-a', content: {} };
  const waiting = fulfillmentState(view, [draft], author);
  assert.deepEqual(waiting.next.map(entry => [entry.action, entry.disabled]), [['review-approve', true], ['reject', true]]);
  const done = fulfillmentState({ ...view, summary: { ...view.summary, promise_remaining_qty: 0 } }, [], author);
  assert.equal(done.status, '전량 도착');
});
