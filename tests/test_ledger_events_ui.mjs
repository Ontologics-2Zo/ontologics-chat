import assert from 'node:assert/strict';
import test from 'node:test';
import { activitySummary, formatLedgerEventTime, ledgerEventFreshIds, renderLedgerEvents, createLedgerEventRenderer, mountDashboard } from '../web/mks2/dashboard.js';

function event(version = 2, extra = {}) {
  return { event_id: `synthetic-event-${version}`, ledger_version: version, event_type: 'BomRegistered',
    recorded_at: '2026-11-09T00:15:20.123456+00:00', origin: 'actual_source_registration', ...extra };
}
function snapshot(version = 2, events = [event(version)]) {
  return { meta: { company_id: 'synthetic-company', identity_revision: 'synthetic-identity', ledger_version: version },
    events, event_count: events.length, summary: {} };
}

test('ledger time is Asia/Seoul HH:mm while retaining the exact zoned timestamp in its full label', () => {
  const stamp = '2026-11-09T00:15:20.123456+00:00';
  const result = formatLedgerEventTime(stamp);
  assert.equal(result.short, '09:15');
  assert.ok(result.full.includes('2026-11-09 09:15:20 Asia/Seoul'));
  assert.ok(result.full.includes(stamp));
  assert.equal(result.datetime, stamp);
  assert.equal(formatLedgerEventTime('2026-11-08T15:00:00Z').short, '00:00');
  assert.equal(formatLedgerEventTime('2026-11-09T09:15:20+09:00').short, '09:15');
});

test('invalid, unzoned and normalized-overflow timestamps never become apparent recorded times', () => {
  for (const value of [undefined, null, '', 'not-a-date', '2026-11-09T09:15:20', '2026-02-29T00:00:00Z',
    '2026-02-30T00:00:00Z', '2026-13-01T00:00:00Z', '2026-11-09T24:00:00Z',
    '2026-11-09T12:60:00Z', '2026-11-09T12:00:60Z', '2026-11-09T00:00:00+99:00', '<img src=x>']) {
    assert.deepEqual(formatLedgerEventTime(value), { short: '—', full: '기록 시각 미확인', datetime: null }, String(value));
  }
  assert.notEqual(formatLedgerEventTime('2024-02-29T00:00:00Z').datetime, null);
});

test('event row uses time, ledger version and server summary with collapsed exact document evidence', () => {
  const item = event(2, { summary: '합성 테스트 BOM 등록 기록', document_kind: 'BOM',
    document_ref: { id: 'synthetic-document', type: 'bom', version: 3, payload_hash: 'a'.repeat(64) } });
  const data = snapshot(2, [item]), before = JSON.stringify(data), html = renderLedgerEvents(data);
  assert.ok(html.includes('data-ledger-events') && html.includes('data-ledger-event-list'));
  assert.match(html, /<time[^>]*>09:15<\/time><span class="mk-event-version"[^>]*>v2<\/span><details/);
  assert.ok(html.includes('<summary aria-label="합성 테스트 BOM 등록 기록 · 사건 근거 보기">합성 테스트 BOM 등록 기록</summary>'));
  for (const text of ['synthetic-document', 'v3', 'a'.repeat(64), '사건 ID', '사건 유형', '기록 시각', '실제 원본 등록']) assert.ok(html.includes(text), text);
  assert.ok(!/<details[^>]*\bopen\b/.test(html));
  assert.ok(!html.includes('<button') && !html.includes('<form'));
  assert.equal(JSON.stringify(data), before);
});

test('only actual committed event types receive fallback labels; unknown kinds remain explicit', () => {
  const known = { OPENING_BALANCE_IMPORTED: '합성 기초 원장 이관', BomRegistered: 'BOM 원본 등록 반영',
    ProcurementPosted: '구매 흐름 문서 원장 반영', EvidenceDocumentPosted: '근거 문서 원장 반영' };
  for (const [event_type, label] of Object.entries(known)) {
    assert.ok(renderLedgerEvents(snapshot(2, [event(2, { event_type })])).includes(`>${label}</summary>`));
  }
  for (const event_type of ['SyntheticUnknown', 'APPROVED', '__proto__', 'constructor']) {
    const html = renderLedgerEvents(snapshot(2, [event(2, { event_type })]));
    assert.ok(html.includes(`>원장 사건 · ${event_type}</summary>`));
    assert.ok(!html.includes('[object Object]') && !html.includes('[native code]'));
  }
});

test('unavailable document evidence does not invent a document or approval reference', () => {
  const html = renderLedgerEvents(snapshot(2, [event(2, { document_ref: null, document_kind: null, origin: 'unknown' })]));
  assert.ok(html.includes('출처 확인 필요'));
  assert.ok(!html.includes('<dt>문서 ID</dt>') && !html.includes('<dt>문서 버전</dt>'));
  assert.ok(!html.includes('approved_by') && !html.includes('승인 완료'));
});

test('summaries, IDs and document evidence are escaped rather than interpreted as HTML', () => {
  const attack = '<img src=x onerror="attack()"><script>attack()</script>';
  const item = event(2, { event_id: attack, summary: attack, event_type: attack,
    document_kind: attack, document_ref: { id: attack, type: attack, version: 1, payload_hash: attack } });
  const html = renderLedgerEvents(snapshot(2, [item]));
  assert.ok(html.includes('&lt;img') && html.includes('&quot;attack()&quot;'));
  assert.ok(!html.includes('<img') && !html.includes('<script>'));
  assert.ok(!html.includes('data-ledger-event-id="<'));
});

test('a bounded list reports its real displayed and total counts without adding missing events', () => {
  const events = Array.from({ length: 100 }, (_, i) => event(150 - i));
  const data = { ...snapshot(150, events), event_count: 150, events_limit: 100 };
  const before = JSON.stringify(data), html = renderLedgerEvents(data);
  assert.ok(html.includes('100 / 150'));
  assert.ok(html.includes('최근 100건 표시 · 전체 150건'));
  assert.equal((html.match(/data-ledger-event-id=/g) || []).length, 100);
  assert.equal(JSON.stringify(data), before);
});

test('empty and unknown-total lists do not create a fictional opening event or total', () => {
  const empty = renderLedgerEvents(snapshot(2, []));
  assert.ok(empty.includes('0 / 0') && empty.includes('조회된 원장 사건이 없습니다.'));
  assert.ok(!empty.includes('data-ledger-event-id=') && !empty.includes('합성 기초 원장 이관'));
  const missing = snapshot(); delete missing.event_count;
  assert.ok(renderLedgerEvents(missing).includes('1 / 미확인'));
});

test('events newer than the displayed snapshot are not presented as facts at its version', () => {
  const data = snapshot(2, [event(3), event(2)]), html = renderLedgerEvents(data);
  assert.ok(html.includes('data-ledger-event-id="synthetic-event-2"'));
  assert.ok(!html.includes('data-ledger-event-id="synthetic-event-3"'));
  assert.ok(html.includes('조회 버전에 포함되지 않은 사건은 표시하지 않았습니다.'));
  assert.equal(data.events.length, 2);
});

test('first load, same version, identity switch and company switch never mark the whole list as new', () => {
  const data = snapshot(2, [event(2), event(1)]);
  for (const previous of [null, data.meta, { ...data.meta, ledger_version: 3 },
    { ...data.meta, ledger_version: 1, company_id: 'other-company' },
    { ...data.meta, ledger_version: 1, identity_revision: 'other-identity' },
    { ledger_version: 1 }]) {
    assert.equal(ledgerEventFreshIds(previous, data).size, 0);
  }
  assert.ok(!renderLedgerEvents(data).includes('class="mk-event-fresh"'));
});

test('same authenticated scope highlights only actual newly committed versions', () => {
  const data = snapshot(4, [event(5), event(4), event(3), event(2), event(1)]);
  const ids = ledgerEventFreshIds({ ...data.meta, ledger_version: 2 }, data);
  assert.deepEqual([...ids], ['synthetic-event-4', 'synthetic-event-3']);
  const html = renderLedgerEvents(data, { freshIds: ids });
  assert.equal((html.match(/class="mk-event-fresh"/g) || []).length, 2);
});

test('local repaints consume new-event highlighting once without dropping events or changing their evidence', () => {
  const data = snapshot(3, [event(3), event(2)]), before = JSON.stringify(data);
  const render = createLedgerEventRenderer(data, { ...data.meta, ledger_version: 2 });
  const initial = render(), repainted = render(), laterRepaint = render();
  assert.equal((initial.match(/class="mk-event-fresh"/g) || []).length, 1);
  assert.ok(!repainted.includes('class="mk-event-fresh"') && !laterRepaint.includes('class="mk-event-fresh"'));
  assert.equal(initial.replace(' class="mk-event-fresh"', ''), repainted);
  assert.equal(repainted, laterRepaint);
  assert.equal(JSON.stringify(data), before);
});

test('dashboard remount retains only an in-memory same-scope cursor and does not write or call APIs', () => {
  const previousDocument = globalThis.document;
  globalThis.document = { addEventListener() {} };
  const container = { innerHTML: '', classList: { add() {}, remove() {} }, addEventListener() {}, replaceChildren() {} };
  let dashboard, calls = 0;
  const mount = data => { dashboard?.destroy(); dashboard = mountDashboard(container, { snapshot: data, api() { calls++; throw Error('No API calls expected'); } }); return container.innerHTML; };
  try {
    assert.ok(!mount(snapshot(2, [event(2), event(1)])).includes('class="mk-event-fresh"'));
    assert.equal((mount(snapshot(3, [event(3), event(2), event(1)])).match(/class="mk-event-fresh"/g) || []).length, 1);
    assert.ok(!mount(snapshot(3)).includes('class="mk-event-fresh"'));
    const switched = snapshot(4); switched.meta.identity_revision = 'new-synthetic-identity';
    assert.ok(!mount(switched).includes('class="mk-event-fresh"'));
    assert.equal(calls, 0);
  } finally {
    dashboard?.destroy();
    if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument;
  }
});

test('document steps appear among ledger events without a ledger version', () => {
  const step = { audit_id: 'audit-1', recorded_at: '2099-01-01T00:00:00+00:00', action: 'approve', actor_id: 'actor-dev-b',
    kind: 'PO', document_id: 'abcd1234-0000', quantity: 25 };
  assert.equal(activitySummary(step), 'PO-ABCD 내부 발주 승인 · B (아직 원장 미반영)');
  assert.equal(activitySummary({ ...step, action: 'create', actor_id: 'actor-dev-a' }), 'PO-ABCD 내부 발주 초안 · 25개 · 승인 요청 (작성 A)');
  const html = renderLedgerEvents(snapshot(2, [event(2)]), { activity: [step, { ...step, audit_id: '<x>' , recorded_at: '2000-01-01T00:00:00+00:00', action: 'review' }] });
  assert.ok(html.indexOf('data-activity-id="audit-1"') < html.indexOf('data-ledger-event-id'));  // newest first
  assert.ok(html.includes('>문서</span>'));
  assert.ok(!html.includes('<x>'));  // escaped
});
