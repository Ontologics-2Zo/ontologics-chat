import assert from 'node:assert/strict';
import test from 'node:test';
import { identifyBomFile, bomFlowStates } from '../web/mks2/bom.js';

// DEC-H48: a local file selects a stored source only by exact SHA-256.
const sources = [
  { source_id: 's-ods', filename: 'board-rev-b.ods', sha256: 'a'.repeat(64), format: 'ods' },
  { source_id: 's-pdf', filename: 'board-rev-b.pdf', sha256: 'b'.repeat(64), format: 'pdf' },
  { source_id: 's-old', filename: 'board-rev-a.ods', sha256: 'c'.repeat(64), format: 'ods' },
];
const documents = [{ status: 'POSTED', content: { source: { sha256: 'c'.repeat(64) }, configuration_id: 'CFG-A' } }];

test('a matching stored table source is accepted without upload', () => {
  const result = identifyBomFile('a'.repeat(64), sources, documents, 'CFG-B');
  assert.equal(result.ok, true);
  assert.equal(result.source.source_id, 's-ods');
});

test('unknown, unsupported, already-registered and unhashable files are held with a reason', () => {
  assert.match(identifyBomFile('d'.repeat(64), sources, documents, 'CFG-B').why, /해시가 일치하지 않습니다/);
  assert.match(identifyBomFile('b'.repeat(64), sources, documents, 'CFG-B').why, /PDF는 BOM 표 등록을 지원하지 않습니다/);
  assert.match(identifyBomFile('c'.repeat(64), sources, documents, 'CFG-B').why, /이미 CFG-A 구성으로 등록/);
  assert.equal(identifyBomFile('c'.repeat(64), sources, documents, 'CFG-A').ok, true);
  assert.match(identifyBomFile(null, sources, documents, 'CFG-B').why, /해시를 계산할 수 없어/);
});

test('flow chips follow the server status, including rejection', () => {
  const states = status => bomFlowStates(status).map(item => item.state).join(',');
  assert.equal(states('DRAFT'), 'on,pending,pending,pending');
  assert.equal(states('REVIEWED'), 'done,on,pending,pending');
  assert.equal(states('APPROVED'), 'done,done,on,pending');
  assert.equal(states('POSTED'), 'done,done,done,done');
  assert.equal(states('REJECTED'), 'rejected,rejected,rejected,rejected');
});

import { candidateDecisions, bomRequestFlow } from '../web/mks2/bom.js';

test('initial mapping assumptions prefill only exact unresolved rows as conditional decisions', () => {
  const rows = [
    { row_id: 'r1', source_row: 51, designators: ['U3'], locator: 'S!row=51', decision: { application_status: 'UNRESOLVED' } },
    { row_id: 'r2', source_row: 6, designators: ['R10', 'R11'], locator: 'S!row=6', decision: { application_status: 'UNRESOLVED' } },
    { row_id: 'r3', source_row: 53, designators: ['U6'], locator: 'S!row=53', decision: { application_status: 'NOT_MOUNTED' } },
  ];
  const candidates = [
    { item_id: 'IM-1', source_row_number: 51, refdes: ['U3'], qty_per_board_assumption: 1, unit_assumption: 'EA', assumption_refs: ['ASM-1'] },
    { item_id: 'IM-2', source_row_number: 6, refdes: ['R10'], qty_per_board_assumption: 4, unit_assumption: 'EA' },
    { item_id: 'IM-3', source_row_number: 53, refdes: ['U6'], qty_per_board_assumption: 1, unit_assumption: 'EA' },
  ];
  const staged = candidateDecisions(rows, candidates);
  assert.deepEqual([...staged.keys()], ['r1']);
  const decision = staged.get('r1');
  assert.equal(decision.review_basis, 'DEMO_ASSUMPTION');
  assert.equal(decision.quantity_per_unit, '1');
  assert.match(decision.evidence_note, /ASM-1.*제조 기술검수·품목 동일성·단위 확정 승인이 아님/);
});

test('reference flow treats a saved decided draft as an approval request', () => {
  const doc = (status, version, decided = true) => ({ status, version, content: { rows: [{ decision: { application_status: decided ? 'APPLICABLE' : 'UNRESOLVED' } }] } });
  const states = (document, options) => bomRequestFlow(document, options).steps.map(item => item.state).join(',');
  assert.equal(states(doc('DRAFT', 1, false)), 'on,pending,pending,pending');
  assert.equal(states(doc('DRAFT', 2)), 'done,on,pending,pending');
  assert.equal(states(doc('DRAFT', 2), { dirty: true }), 'on,pending,pending,pending');
  assert.equal(states(doc('APPROVED', 3)), 'done,done,on,pending');
  assert.equal(states(doc('POSTED', 3)), 'done,done,done,done');
  assert.equal(bomRequestFlow(doc('DRAFT', 2)).requested, true);
});

import { candidateSource, bomStageDisplay } from '../web/mks2/bom.js';

test('the initial mapping assumptions point to exactly one stored table source', () => {
  const sources = [{ source_id: 'pdf', sha256: '773c8b3fb0fa' + 'a'.repeat(52), format: 'pdf' },
    { source_id: 'ods', sha256: '773c8b3fb0fa' + 'b'.repeat(52), format: 'ods' }, { source_id: 'other', sha256: 'ef4f04d46ca8' + 'c'.repeat(52), format: 'ods' }];
  assert.equal(candidateSource(sources, [{ source_row_id: 'POE-L1-WROOM:773c8b3fb0fa:49' }, { source_row_id: 'POE-L1-WROOM:773c8b3fb0fa:60' }]).source_id, 'ods');
  assert.equal(candidateSource(sources, [{ source_row_id: 'X:773c8b3fb0fa:1' }, { source_row_id: 'Y:ef4f04d46ca8:2' }]), null);
  assert.equal(candidateSource(sources, []), null);
});

test('after posting, review and first-registration chips show as done with notes; before posting they stay', () => {
  const states = ['complete', 'review', 'complete', 'pending', 'review', 'complete', 'complete', 'complete'];
  const posted = bomStageDisplay(states, { status: 'POSTED' }, { status: 'NO_BASELINE' });
  assert.deepEqual(posted.map(item => item.state), ['complete', 'noted', 'complete', 'noted', 'noted', 'complete', 'complete', 'complete']);
  assert.match(posted[1].note, /확인 필요 항목 남음/);
  assert.match(posted[3].note, /최초 등록/);
  assert.deepEqual(bomStageDisplay(states, { status: 'APPROVED' }).map(item => item.state), states);
});
