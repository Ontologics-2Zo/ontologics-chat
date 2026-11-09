import assert from 'node:assert/strict';
import test from 'node:test';
import { bomStageStates, decisionFromChoices, createBomComparison, preferredBomSource,
  bomPresentation, reviewFocusRows } from '../web/mks2/bom.js';

const hash = digit => digit.repeat(64);
function documentRecord(id = 'synthetic-current', version = 2, digest = hash('b')) {
  return { id, version, payload_hash: digest, status: 'DRAFT', content: {
    source: { filename: `${id}.xlsx`, sha256: hash('c') }, source_conflicts: [], rows: [],
  } };
}
function sourceRow(overrides = {}) {
  return { row_id: hash('a'), locator: 'Synthetic!row=2', source_quantity: '2.5',
    source_quantity_text: '2.50', source_description: 'Synthetic component', designators: ['X1'],
    decision: { application_status: 'UNRESOLVED', review_basis: 'UNREVIEWED',
      item_id: null, quantity_per_unit: null, unit: null, evidence_note: '' }, ...overrides };
}
const applied = { application_status: 'APPLICABLE', item_id: 'SYNTHETIC-PART', quantity_per_unit: '2.5', unit: 'EA' };

const catalogSource = (source_id = 'synthetic-source', sha256 = hash('c')) => ({ source_id, sha256, filename: `${source_id}.ods` });
function postedDocument(source, configuration_id = 'SYNTHETIC-CONFIG', ledger_version = 2) {
  const doc = documentRecord(`synthetic-posted-${ledger_version}`);
  return { ...doc, status: 'POSTED', ledger_version,
    content: { ...doc.content, configuration_id, source: { ...source }, rows: [sourceRow({ decision: { ...applied } })] } };
}

test('preferred source follows the latest mapped posted document of the selected configuration, not catalog order', () => {
  const first = catalogSource(), second = catalogSource('synthetic-second', hash('d'));
  const documents = [postedDocument(first), postedDocument(second, 'SYNTHETIC-CONFIG', 3),
    postedDocument(first, 'OTHER-CONFIG', 99)];
  const before = JSON.stringify({ sources: [first, second], documents });
  assert.equal(preferredBomSource([first, second], documents, 'SYNTHETIC-CONFIG'), second);
  assert.equal(JSON.stringify({ sources: [first, second], documents }), before);
});

test('crossed source IDs, hashes and configurations do not authorize a preferred source', () => {
  const first = catalogSource(), second = catalogSource('synthetic-second', hash('d'));
  const crossed = postedDocument({ ...first, sha256: second.sha256 });
  assert.equal(preferredBomSource([first, second], [crossed], 'SYNTHETIC-CONFIG'), null);
  const wrongConfiguration = postedDocument(first, 'OTHER-CONFIG');
  assert.equal(preferredBomSource([first, second], [wrongConfiguration], 'SYNTHETIC-CONFIG'), null);
  const onlyHashMatches = postedDocument({ ...first, source_id: 'not-in-catalog' });
  assert.equal(preferredBomSource([first, second], [onlyHashMatches], 'SYNTHETIC-CONFIG'), null);
  assert.equal(preferredBomSource([first, { ...first }], [postedDocument(first)], 'SYNTHETIC-CONFIG'), null);
});

test('unposted or unmapped documents cannot choose among multiple catalog sources', () => {
  const first = catalogSource(), second = catalogSource('synthetic-second', hash('d'));
  for (const status of ['DRAFT', 'REVIEWED', 'APPROVED']) {
    assert.equal(preferredBomSource([first, second], [{ ...postedDocument(first), status }], 'SYNTHETIC-CONFIG'), null);
  }
  const unresolved = postedDocument(first); unresolved.content.rows = [sourceRow()];
  assert.equal(preferredBomSource([first, second], [unresolved], 'SYNTHETIC-CONFIG'), null);
});

test('source fallback requires a sole catalog entry and never guesses from a filename', () => {
  const first = catalogSource(), second = { ...catalogSource('synthetic-second', hash('d')), filename: first.filename };
  assert.equal(preferredBomSource([first], [], 'SYNTHETIC-CONFIG'), first);
  assert.equal(preferredBomSource([first, second], [], 'SYNTHETIC-CONFIG'), null);
  assert.equal(preferredBomSource([], [], 'SYNTHETIC-CONFIG'), null);
});

test('presentation counts rows, sheet-scoped positions and decimal quantities independently', () => {
  const rows = [
    sourceRow({ sheet: 'A', source_quantity: '2', designators: ['X1', 'X2'] }),
    sourceRow({ sheet: 'A', source_quantity: '0.5', designators: ['X3'] }),
    sourceRow({ sheet: 'A', source_quantity: '1', designators: ['X1'] }),
    sourceRow({ sheet: 'A', source_quantity: null, designators: [] }),
    sourceRow({ sheet: 'B', source_quantity: '1', designators: ['X1'], decision: { ...applied } }),
  ];
  const before = JSON.stringify(rows);
  assert.deepEqual(bomPresentation(rows), { rows: 5, positions: 5, uniquePositions: 4,
    linked: 1, positiveIntegers: 3, matchingPositions: 3, missingPositions: 1 });
  assert.equal(JSON.stringify(rows), before);
});

test('quantity-position checks compare exact decimals and do not round near integers into successful checks', () => {
  const rows = [
    sourceRow({ source_quantity: '1.0000000000000001', designators: ['X1'] }),
    sourceRow({ source_quantity: '0.99999999999999999', designators: ['X2'] }),
    sourceRow({ source_quantity: '0002.000', designators: ['X3', 'X4'] }),
    sourceRow({ source_quantity: '0.0', designators: [] }),
    sourceRow({ source_quantity: '0.000001', designators: ['X5'] }),
  ];
  const result = bomPresentation(rows);
  assert.equal(result.positiveIntegers, 1);
  assert.equal(result.matchingPositions, 1);
  assert.equal(rows[0].source_quantity, '1.0000000000000001');
  assert.equal(rows[2].source_quantity, '0002.000');
});

test('local decision counts change the display without changing stored source or decisions', () => {
  const rows = [sourceRow({ row_id: 'synthetic-one' }), sourceRow({ row_id: 'synthetic-two' })];
  const drafts = new Map([['synthetic-one', { ...applied }]]), before = JSON.stringify(rows);
  assert.equal(bomPresentation(rows).linked, 0);
  assert.equal(bomPresentation(rows, row => drafts.get(row.row_id) || row.decision).linked, 1);
  assert.equal(JSON.stringify(rows), before);
});

function documentWithRows(count = 74) {
  const doc = documentRecord();
  doc.content.rows = Array.from({ length: count }, (_, i) => sourceRow({ row_id: `synthetic-row-${i}`,
    source_row: i + 2, locator: `Synthetic!row=${i + 2}`, cells: [`Synthetic source value ${i}`] }));
  return doc;
}
function focusResponse(doc, row_ids) {
  return { document: { document_id: doc.id, version: doc.version, payload_hash: doc.payload_hash }, row_ids,
    read_only: true, selection_only: true, model_calls: 0, ledger_mutations: 0,
    decisions_copied: false, approvals_copied: false };
}

test('a previous five-row review focus narrows a fresh draft display without copying decisions or losing the other sixty-nine rows', () => {
  const doc = documentWithRows(), ids = [0, 4, 15, 26, 73].map(i => doc.content.rows[i].row_id);
  const response = focusResponse(doc, ids), before = JSON.stringify({ doc, response });
  const focused = reviewFocusRows(doc, response);
  assert.deepEqual(focused.map(row => row.row_id), ids);
  assert.ok(focused.every(row => row.decision.application_status === 'UNRESOLVED'));
  assert.equal(doc.content.rows.length, 74);
  assert.equal(doc.content.rows.filter(row => !ids.includes(row.row_id)).length, 69);
  assert.equal(doc.content.rows.filter(row => row.decision.application_status === 'UNRESOLVED').length, 74);
  assert.equal(bomPresentation(doc.content.rows).linked, 0);
  assert.equal(JSON.stringify({ doc, response }), before);
});

test('stale focus is discarded while current and explicitly staged mapped rows remain visible', () => {
  const doc = documentWithRows(4);
  doc.content.rows[1].decision = { ...applied };
  const drafts = new Map([[doc.content.rows[2].row_id, { ...applied }]]);
  const response = focusResponse(doc, [doc.content.rows[0].row_id]); response.document.version++;
  const before = JSON.stringify({ doc, drafts: [...drafts] });
  assert.deepEqual(reviewFocusRows(doc, response, drafts).map(row => row.row_id),
    [doc.content.rows[1].row_id, doc.content.rows[2].row_id]);
  assert.equal(JSON.stringify({ doc, drafts: [...drafts] }), before);
});

test('focus only accepts the exact document identity, version and hash', () => {
  const doc = documentWithRows(3); doc.content.rows[2].decision = { ...applied };
  for (const [key, value] of [['document_id', 'different-document'], ['version', 99], ['payload_hash', hash('f')]]) {
    const response = focusResponse(doc, [doc.content.rows[0].row_id]); response.document[key] = value;
    assert.deepEqual(reviewFocusRows(doc, response).map(row => row.row_id), [doc.content.rows[2].row_id], key);
  }
  assert.deepEqual(reviewFocusRows(doc, null).map(row => row.row_id), [doc.content.rows[2].row_id]);
});

test('focus requires explicit selection-only, no-write, no-model and no-copy declarations', () => {
  const doc = documentWithRows(3); doc.content.rows[2].decision = { ...applied };
  const invalid = { read_only: false, selection_only: false, model_calls: 1, ledger_mutations: 1,
    decisions_copied: true, approvals_copied: true };
  for (const [key, value] of Object.entries(invalid)) {
    const response = focusResponse(doc, [doc.content.rows[0].row_id]); response[key] = value;
    assert.deepEqual(reviewFocusRows(doc, response).map(row => row.row_id), [doc.content.rows[2].row_id], key);
    delete response[key];
    assert.deepEqual(reviewFocusRows(doc, response).map(row => row.row_id), [doc.content.rows[2].row_id], `${key} absent`);
  }
});

test('focus ignores injected row objects and outside IDs, deduplicates IDs, and preserves source ordering', () => {
  const doc = documentWithRows(4), attack = '<img src=x onerror="attack()">';
  doc.content.rows[1].cells = [attack];
  const response = focusResponse(doc, [doc.content.rows[3].row_id, attack, '__proto__', doc.content.rows[1].row_id, doc.content.rows[3].row_id]);
  response.rows = [{ row_id: doc.content.rows[1].row_id, decision: { ...applied, evidence_note: attack }, approved_by: 'injected' }];
  const before = JSON.stringify(doc);
  const focused = reviewFocusRows(doc, response);
  assert.deepEqual(focused.map(row => row.row_id), [doc.content.rows[1].row_id, doc.content.rows[3].row_id]);
  assert.ok(focused.every(row => row.decision.application_status === 'UNRESOLVED' && !('approved_by' in row)));
  assert.equal(JSON.stringify(doc), before);
});

test('an explicitly staged unmapping removes the current-mapping fallback without restoring its prior decision', () => {
  const doc = documentWithRows(3); doc.content.rows[0].decision = { ...applied };
  const drafts = new Map([[doc.content.rows[0].row_id, { application_status: 'UNRESOLVED', item_id: null }]]);
  assert.deepEqual(reviewFocusRows(doc, null, drafts), []);
  assert.equal(doc.content.rows[0].decision.application_status, 'APPLICABLE');
  assert.equal(drafts.get(doc.content.rows[0].row_id).application_status, 'UNRESOLVED');
});

test('visiting a later stage cannot fabricate missing source or row-decision evidence', () => {
  assert.ok(bomStageStates(null, { step: 7 }).every(state => state === 'pending'));
  const doc = documentRecord();
  doc.content.rows = [sourceRow({ decision: undefined }), sourceRow({ decision: {} })];
  const before = JSON.stringify(doc);
  assert.equal(bomStageStates(doc, { step: 7 })[4], 'pending');
  assert.deepEqual(bomStageStates(doc, { step: 7 }), bomStageStates(doc, { step: 1 }));
  assert.equal(JSON.stringify(doc), before);
});

test('five conditional decisions leave sixty-nine unresolved rows visible as partial review', () => {
  const doc = documentRecord();
  doc.content.rows = Array.from({ length: 74 }, (_, i) => sourceRow({ row_id: `synthetic-row-${i}`,
    decision: i < 5 ? { ...applied, review_basis: 'DEMO_ASSUMPTION' } : sourceRow().decision }));
  assert.equal(bomStageStates(doc)[4], 'review');
  assert.equal(doc.content.rows.length, 74);
  assert.equal(doc.content.rows.filter(row => row.decision.application_status === 'UNRESOLVED').length, 69);
});

test('review, approval and posting remain separate milestones even with every row decided', () => {
  const doc = documentRecord();
  doc.content.rows = [sourceRow({ decision: { ...applied } }),
    ...['NOT_MOUNTED', 'SETTING'].map(application_status => sourceRow({ decision: {
      application_status, item_id: null, quantity_per_unit: null, unit: null,
    } }))];
  assert.equal(bomStageStates(doc)[4], 'complete');
  assert.equal(bomStageStates(doc)[6], 'pending');
  doc.status = 'REVIEWED';
  assert.equal(bomStageStates(doc)[6], 'review');
  assert.equal(bomStageStates(doc)[7], 'pending');
  doc.status = 'APPROVED';
  assert.equal(bomStageStates(doc)[6], 'complete');
  assert.equal(bomStageStates(doc)[7], 'pending');
  doc.status = 'POSTED';
  assert.equal(bomStageStates(doc)[7], 'complete');
});

test('an application status without a complete item, positive quantity and unit cannot complete mapping', () => {
  const doc = documentRecord();
  for (const field of ['item_id', 'quantity_per_unit', 'unit']) {
    for (const missing of [undefined, null, '', '  ']) {
      doc.content.rows = [sourceRow({ decision: { ...applied, [field]: missing } })];
      assert.notEqual(bomStageStates(doc)[4], 'complete', `${field}: ${String(missing)}`);
    }
  }
  for (const quantity of ['0', '0.000000', '-1', '-0.5', 'NaN', 'Infinity', '1x']) {
    doc.content.rows = [sourceRow({ decision: { ...applied, quantity_per_unit: quantity } })];
    assert.notEqual(bomStageStates(doc)[4], 'complete', quantity);
  }
  for (const quantity of ['0.000001', '1', '2.5']) {
    doc.content.rows = [sourceRow({ decision: { ...applied, quantity_per_unit: quantity } })];
    assert.equal(bomStageStates(doc)[4], 'complete', quantity);
  }
  doc.content.rows = [sourceRow({ decision: { ...applied } }), sourceRow({ decision: { ...applied, unit: null } })];
  assert.equal(bomStageStates(doc)[4], 'review');
});

test('excluded and setting decisions complete mapping without manufacturing quantities or item links', () => {
  const doc = documentRecord();
  doc.content.rows = ['NOT_MOUNTED', 'SETTING'].map(application_status => sourceRow({ decision: {
    application_status, review_basis: 'DEMO_ASSUMPTION', item_id: null, quantity_per_unit: null, unit: null,
  } }));
  assert.equal(bomStageStates(doc)[4], 'complete');
});

test('source conflict and ambiguous comparison retain review indicators', () => {
  const doc = documentRecord();
  doc.content.source_conflicts = ['SOURCE_FILENAME_SHEET_CONFIGURATION_CONFLICT'];
  assert.equal(bomStageStates(doc)[1], 'review');
  assert.equal(bomStageStates(doc, { comparison: { status: 'NEEDS_REVIEW' } })[3], 'review');
  assert.equal(bomStageStates(doc, { comparison: { status: 'NO_BASELINE' } })[3], 'pending');
  for (const status of ['COMPARED', 'IDENTICAL_SOURCE']) {
    assert.equal(bomStageStates(doc, { comparison: { status } })[3], 'complete');
  }
  assert.ok(!bomStageStates(null, { processing: true }).includes('complete'));
});

test('unresolved and excluded choices erase application values without changing source evidence', () => {
  for (const application_status of ['UNRESOLVED', 'NOT_MOUNTED', 'SETTING']) {
    const row = sourceRow(), fields = { ...applied, application_status }, before = JSON.stringify({ row, fields });
    const decision = decisionFromChoices(row, fields);
    assert.equal(decision.item_id, null);
    assert.equal(decision.quantity_per_unit, null);
    assert.equal(decision.unit, null);
    assert.equal(decision.review_basis, application_status === 'UNRESOLVED' ? 'UNREVIEWED' : 'DEMO_ASSUMPTION');
    assert.equal(JSON.stringify({ row, fields }), before);
  }
});

test('source quantity and an old candidate cannot fill an unselected application choice', () => {
  const row = sourceRow({ decision: { ...applied, review_basis: 'DEMO_ASSUMPTION' } });
  for (const field of ['item_id', 'quantity_per_unit', 'unit']) {
    assert.throws(() => decisionFromChoices(row, { ...applied, [field]: '' }), /BOM_SELECTION_REQUIRED/);
    assert.throws(() => decisionFromChoices(row, { ...applied, [field]: '  ' }), /BOM_SELECTION_REQUIRED/);
  }
  assert.throws(() => decisionFromChoices(row, { ...applied, application_status: 'APPROVED' }), /BOM_SELECTION_REQUIRED/);
});

test('explicit application keeps exact decimal text and conditional evidence without manufacturing approval', () => {
  const row = sourceRow(), before = JSON.stringify(row);
  const decision = decisionFromChoices(row, { ...applied, quantity_per_unit: '0.000001', evidence_note: '  Synthetic independent review note  ' });
  assert.equal(decision.quantity_per_unit, '0.000001');
  assert.equal(decision.review_basis, 'DEMO_ASSUMPTION');
  assert.ok(decision.evidence_note.includes(row.locator));
  assert.ok(decision.evidence_note.includes('사용자 선택'));
  assert.ok(decision.evidence_note.includes('제조 기술검수·품목 동일성·단위 확정 승인이 아님'));
  assert.ok(decision.evidence_note.includes('Synthetic independent review note'));
  assert.equal(JSON.stringify(row), before);
  assert.ok(!('approved_by' in decision) && !('status' in decision));
});

test('unchanged decisions preserve existing evidence while changed choices receive fresh evidence', () => {
  const row = sourceRow({ decision: { ...applied, review_basis: 'DEMO_ASSUMPTION', evidence_note: 'Existing synthetic conditional justification' } });
  assert.equal(decisionFromChoices(row, applied).evidence_note, row.decision.evidence_note);
  const changed = decisionFromChoices(row, { ...applied, quantity_per_unit: '3' });
  assert.notEqual(changed.evidence_note, row.decision.evidence_note);
  assert.ok(changed.evidence_note.includes('대당 3'));
});

const ref = doc => doc ? { document_id: doc.id, version: doc.version, payload_hash: doc.payload_hash } : null;
function comparisonResponse(left, right, overrides = {}) {
  return { left: ref(left), right: ref(right), status: left ? 'COMPARED' : 'NO_BASELINE',
    read_only: true, model_calls: 0, ledger_mutations: 0,
    summary: { added: 0, removed: 0, changed: 0, unchanged: 1, needs_review: 0 }, rows: [], ...overrides };
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test('comparison sends exact selected document versions and hashes through a read-only endpoint', async () => {
  const left = documentRecord('synthetic-previous', 1, hash('a')), right = documentRecord(), calls = [];
  const before = JSON.stringify({ left, right });
  const view = createBomComparison({ api: async (path, body) => { calls.push({ path, body }); return comparisonResponse(left, right); } });
  await view.load(right, [left, right], left.id);
  assert.deepEqual(calls, [{ path: '/api/mks2/bom/compare', body: {
    left: { document_id: left.id, expected_version: left.version, payload_hash: left.payload_hash },
    right: { document_id: right.id, expected_version: right.version, payload_hash: right.payload_hash },
  } }]);
  assert.equal(view.result().status, 'COMPARED');
  assert.equal(JSON.stringify({ left, right }), before);
  assert.ok(!calls.some(call => /approve|revise|post|create/.test(call.path)));
});

test('first registration with no baseline remains a read-only non-comparison', async () => {
  const right = documentRecord(), calls = [];
  const view = createBomComparison({ api: async (path, body) => { calls.push({ path, body }); return comparisonResponse(null, right); } });
  await view.load(right, [right], '');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.left, null);
  assert.equal(view.result().status, 'NO_BASELINE');
  assert.ok(view.render(right, [right]).includes('비교 기준 없음'));
  assert.equal(bomStageStates(right, { comparison: view.result() })[3], 'pending');
});

test('missing baseline does not silently compare against a different document', async () => {
  const right = documentRecord(); let calls = 0;
  const view = createBomComparison({ api: async () => { calls++; return comparisonResponse(null, right); } });
  await view.load(right, [right], 'no-longer-present');
  assert.equal(calls, 0);
  assert.equal(view.result(), null);
  assert.ok(view.render(right, [right]).includes('비교 결과를 확인하지 못했습니다'));
});

test('late comparison results cannot overwrite a newer baseline selection', async () => {
  const first = deferred(), second = deferred(), old = documentRecord('old', 1, hash('a'));
  const newer = documentRecord('newer', 2, hash('d')), right = documentRecord(); let calls = 0;
  const view = createBomComparison({ api: () => ++calls === 1 ? first.promise : second.promise });
  const firstLoad = view.load(right, [old, newer, right], old.id);
  const secondLoad = view.load(right, [old, newer, right], newer.id);
  second.resolve(comparisonResponse(newer, right)); await secondLoad;
  first.resolve(comparisonResponse(old, right)); await firstLoad;
  assert.equal(view.result().left.document_id, newer.id);
});

test('reset discards an in-flight comparison without refreshing a closed or replaced document', async () => {
  const pending = deferred(), right = documentRecord(); let changes = 0;
  const view = createBomComparison({ api: () => pending.promise, onChange: () => changes++ });
  const load = view.load(right, [right], '');
  view.reset(); const changesAtReset = changes;
  pending.resolve(comparisonResponse(null, right)); await load;
  assert.equal(view.result(), null);
  assert.equal(changes, changesAtReset);
});

for (const [status, message] of [[403, '비교할 권한이 없습니다'], [409, '문서 버전이 바뀌었습니다']]) {
  test(`comparison ${status} clears any previous successful evidence`, async () => {
    const left = documentRecord('synthetic-previous', 1, hash('a')), right = documentRecord(); let calls = 0;
    const view = createBomComparison({ api: async () => {
      if (++calls > 1) throw Object.assign(new Error('Synthetic failure'), { status });
      return comparisonResponse(left, right);
    } });
    await view.load(right, [left, right], left.id); assert.ok(view.result());
    await view.load(right, [left, right], left.id);
    assert.equal(view.result(), null);
    assert.ok(view.render(right, [left, right]).includes(message));
  });
}

for (const [name, alter] of [
  ['left document', value => { value.left.document_id = 'different'; }],
  ['left version', value => { value.left.version++; }],
  ['left hash', value => { value.left.payload_hash = hash('f'); }],
  ['right document', value => { value.right.document_id = 'different'; }],
  ['right version', value => { value.right.version++; }],
  ['right hash', value => { value.right.payload_hash = hash('f'); }],
  ['read-only declaration', value => { value.read_only = false; }],
  ['model calls', value => { value.model_calls = 1; }],
  ['ledger mutations', value => { value.ledger_mutations = 1; }],
]) {
  test(`mismatched ${name} cannot be shown as completed comparison evidence`, async () => {
    const left = documentRecord('synthetic-previous', 1, hash('a')), right = documentRecord();
    const result = comparisonResponse(left, right); alter(result);
    const view = createBomComparison({ api: async () => result });
    await view.load(right, [left, right], left.id);
    assert.equal(view.result(), null);
    assert.ok(view.render(right, [left, right]).includes('비교 결과를 확인하지 못했습니다'));
  });
}

test('comparison renders source cells and document labels as escaped data', async () => {
  const attack = '<img src=x onerror="attack()"><script>attack()</script>';
  const left = documentRecord('synthetic-previous', 1, hash('a')), right = documentRecord();
  left.content.source.filename = attack;
  const result = comparisonResponse(left, right, { rows: [{ change_type: 'CHANGED', review_reasons: [],
    left: { source_description: attack, source_quantity_text: attack, locator: attack, designators: [attack] }, right: null }] });
  const before = JSON.stringify(result);
  const view = createBomComparison({ api: async () => result });
  await view.load(right, [left, right], left.id);
  const html = view.render(right, [left, right]);
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<img') && !html.includes('<script>'));
  assert.equal(JSON.stringify(result), before);
});

test('a raw-cell-only source change remains inspectable in collapsed details despite equal descriptions and quantities', async () => {
  const left = documentRecord('synthetic-previous', 1, hash('a')), right = documentRecord();
  const common = { source_description: 'Same synthetic description', source_quantity_text: '2.5',
    locator: 'Synthetic!row=2', designators: ['X1'] };
  const attack = '<img src=x onerror="attack()">';
  const result = comparisonResponse(left, right, { rows: [{ change_type: 'CHANGED',
    changes: ['SOURCE_VALUE_CHANGED'], review_reasons: [],
    left: { ...common, cells: ['Same synthetic description', '2.5', 'Synthetic old specification'] },
    right: { ...common, cells: ['Same synthetic description', '2.5', `Synthetic new specification ${attack}`] } }] });
  const before = JSON.stringify(result);
  const view = createBomComparison({ api: async () => result });
  await view.load(right, [left, right], left.id);
  const html = view.render(right, [left, right]);
  const details = [...html.matchAll(/<details\b([^>]*)>([\s\S]*?)<\/details>/g)]
    .filter(match => match[2].includes('원문 셀 전체'));
  assert.equal(details.length, 2);
  assert.ok(details.every(match => !/\bopen\b/.test(match[1])));
  assert.ok(details[0][2].includes('<li>Synthetic old specification</li>'));
  assert.ok(details[1][2].includes('Synthetic new specification &lt;img'));
  assert.ok(details.every(match => /<ol\b[^>]*class="mk-bom-cells"/.test(match[2])));
  assert.ok(!html.includes('<img'));
  assert.equal(JSON.stringify(result), before);
});

test('comparison explains quantity, extraction and configuration warnings without declaring them resolved', async () => {
  const left = documentRecord('synthetic-previous', 1, hash('a')), right = documentRecord();
  const result = comparisonResponse(left, right, { status: 'NEEDS_REVIEW',
    warnings: ['DIFFERENT_CONFIGURATION_EXPLICIT_BASELINE', 'SINGLE_SHEET_NAMES_DIFFER'],
    source_warnings: { left: ['SOURCE_ROW_REQUIRES_REVIEW'], right: [] },
    rows: [{ change_type: 'NEEDS_REVIEW', review_reasons: ['QUANTITY_UNCONFIRMED', 'SAME_SOURCE_EXTRACTION_DIFFERS'],
      left: { source_description: 'Synthetic component', source_quantity_text: '', locator: 'Synthetic!row=2', designators: ['X1'] }, right: null }] });
  const view = createBomComparison({ api: async () => result });
  await view.load(right, [left, right], left.id);
  const html = view.render(right, [left, right]);
  for (const message of ['원문 수량 미확인', '같은 원본의 추출 결과가 다름', '기준 구성이 다릅니다', '시트명이 다릅니다', '비교 완료가 경고 해소를 뜻하지 않습니다']) {
    assert.ok(html.includes(message), message);
  }
  assert.equal(bomStageStates(right, { comparison: view.result() })[3], 'review');
});
