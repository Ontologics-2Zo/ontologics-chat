// Actual browser/server BOM verification. Captures and raw data stay outside Git.
// Mutation coverage requires an explicitly isolated ledger service; no defaults
// permit source intake or approval against the user's running baseline ledger.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const options = Object.fromEntries(process.argv.slice(2).map(arg => arg.replace(/^--/, '').split(/=(.*)/s)));
const base = options.url || 'http://127.0.0.1:8871/mks2/';
const cdp = options.cdp || 'http://127.0.0.1:9233';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
for (const url of [base, cdp]) if (!['127.0.0.1', 'localhost'].includes(new URL(url).hostname)) throw Error('LOCAL_ONLY');
if (!options.artifacts) throw Error('PRIVATE_ARTIFACT_DIRECTORY_REQUIRED');
const output = path.resolve(options.artifacts);
fs.mkdirSync(output, { recursive: true, mode: 0o700 });
if (fs.realpathSync(output).startsWith(`${repo}${path.sep}`) || fs.realpathSync(output) === repo) throw Error('PRIVATE_ARTIFACTS_OUTSIDE_GIT');
const exercise = options['exercise-workflow'] === '1';
const expectedRunnerBlock = options['expect-runner-blocked'];
const syntheticAggregation = options['synthetic-aggregation'] === '1';
if (syntheticAggregation && (options['source-mode'] !== 'synthetic-browser-runner' || options['expected-rows'] !== '4')) throw Error('SYNTHETIC_AGGREGATION_REQUIRES_EXPLICIT_FOUR_ROW_FIXTURE');
if (exercise && expectedRunnerBlock) throw Error('MUTATION_AND_RUNNER_BLOCK_ARE_EXCLUSIVE');
if (exercise && (options['isolated-ledger'] !== '1' || !/^[0-9a-f]{64}$/.test(options['source-id'] || ''))) throw Error('EXPLICIT_ISOLATED_LEDGER_AND_SOURCE_REQUIRED');
const target = await (await fetch(`${cdp}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let sequence = 0;
const pending = new Map(), requests = [], exceptions = [], checks = [];
ws.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.method === 'Network.requestWillBeSent') requests.push({ method: message.params.request.method, url: message.params.request.url, initiator: message.params.initiator?.type, type: message.params.type });
  if (message.method === 'Runtime.exceptionThrown') exceptions.push('BROWSER_EXCEPTION');
  if (message.id && pending.has(message.id)) {
    const promise = pending.get(message.id); pending.delete(message.id);
    message.error ? promise.reject(Error('CDP_COMMAND_FAILED')) : promise.resolve(message.result);
  }
};
function send(method, params = {}) { return new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); }); }
async function evaluate(expression) {
  const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (response.exceptionDetails) throw Error('BROWSER_EVALUATION_FAILED');
  return response.result.value;
}
async function until(expression, attempts = 600) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw Error('UI_WAIT_TIMEOUT');
}
function check(name, condition) { checks.push({ name, status: condition ? 'PASS' : 'FAIL' }); if (!condition) throw Error(name); }
async function click(selector) { await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`); }
async function fill(selector, value) {
  await evaluate(`(()=>{const input=document.querySelector(${JSON.stringify(selector)});input.value=${JSON.stringify(value)};input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
}
async function actor(profile) {
  await fill('#actor', profile);
  await until("document.querySelector('#load-state')?.hidden && !document.querySelector('#actor').disabled");
}
async function openBom() {
  await click('#tab-dashboard');
  await click('#dashboard-stage [data-action="bom"]');
  await until("document.querySelector('.mk-drawer')?.getAttribute('aria-busy')==='false' && document.querySelector('[data-bom-field=source]')");
}
async function serverDocMeta() {
  return evaluate(`(async()=>{const session=await (await fetch('/api/mks2/session')).json();const result=await (await fetch('/api/mks2/bom/documents',{headers:{'X-Identity-Revision':session.principal.identity_revision}})).json();const id=document.querySelector('[data-bom-field=document]')?.value;const doc=result.documents.find(item=>item.id===id);return doc?{rows:doc.content.rows.length,version:doc.version,status:doc.status,ledger_version:doc.ledger_version}:null;})()`);
}
async function shot(name) {
  await evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(path.join(output, `${name}.png`), Buffer.from(data, 'base64'), { mode: 0o600 });
}
const report = { source_mode: options['source-mode'] || 'unspecified', scope: expectedRunnerBlock ? 'Read-only fail-closed Runner status and preserved ledger/full ontology' : exercise ? 'Isolated server BOM intake, exact review/approval/post, all-row UI and snapshot projection; source_mode identifies parser evidence' : 'Server existing BOM document, all-row UI and snapshot projection; read-only BOM checks', checks };
try {
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1536, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: base });
  await until("document.querySelector('#actor')?.disabled===false");
  await actor('dev-operator-a');
  await openBom();
  check('EXISTING_DRAWER_WIDTH_PRESERVED', await evaluate("document.querySelector('.mk-drawer').getBoundingClientRect().width===760"));
  check('ALL_EIGHT_BOM_STAGES_PRESERVED', await evaluate("document.querySelectorAll('[data-bom-step]').length===8"));
  if (expectedRunnerBlock) {
    const readiness = await evaluate(`(async()=>{const session=await(await fetch('/api/mks2/session')).json();const result=await(await fetch('/api/mks2/bom/sources',{headers:{'X-Identity-Revision':session.principal.identity_revision}})).json();return result.runner;})()`);
    check('RUNNER_ENFORCEMENT_FAILS_CLOSED', readiness.ready === false && readiness.code === expectedRunnerBlock);
    check('BLOCKED_RUNNER_INTAKE_DISABLED', await evaluate("document.querySelector('[data-bom-action=create]').disabled"));
    check('BLOCKED_RUNNER_REASON_VISIBLE', await evaluate("document.querySelector('.mk-drawer').textContent.includes('이 실행환경의 파일 처리 자원 제한 미지원')"));
    check('NO_DOCUMENT_CREATED_ON_BLOCKED_RUNNER', (await serverDocMeta()) === null);
    await shot('bom-runner-blocked');
    await click('[data-bom-step="2"]');
    check('BLOCKED_RUNNER_HAS_NO_FAKE_ROWS', await evaluate("document.querySelectorAll('[data-bom-table=source] tbody tr').length===0 && document.querySelector('.mk-drawer').textContent.includes('등록 문서 없음')"));
    await click('.mk-drawer [data-close]');
  } else {
  if (exercise) {
    await fill('[data-bom-field="source"]', options['source-id']);
    check('RUNNER_READY_INTAKE_ENABLED', await evaluate("document.querySelector('[data-bom-action=create]').disabled===false"));
    await click('[data-bom-action="create"]');
    await until("document.querySelectorAll('[data-bom-table=source] tbody tr').length>0 && document.querySelector('.mk-drawer').getAttribute('aria-busy')==='false'");
    check('INTAKE_CALLED_REAL_SERVER', requests.some(request => request.method === 'POST' && request.url.endsWith('/api/mks2/bom/documents')));
  } else {
    await click('[data-bom-step="2"]');
    await until("document.querySelectorAll('[data-bom-table=source] tbody tr').length>0");
  }
  let meta = await serverDocMeta();
  check('ALL_SOURCE_ROWS_RENDERED', await evaluate("document.querySelectorAll('[data-bom-table=source] tbody tr').length") === meta.rows);
  if (options['expected-rows']) check('EXPECTED_SOURCE_COVERAGE', meta.rows === Number(options['expected-rows']));
  check('SOURCE_CELLS_KEPT_AS_TEXT', await evaluate("document.querySelectorAll('.mk-bom-cells').length===document.querySelectorAll('[data-bom-table=source] tbody tr').length && document.querySelector('.mk-drawer script')===null"));
  await shot('bom-source-rows');
  const writesBefore = requests.filter(request => request.method === 'POST' && request.url.includes('/api/mks2/bom/')).length;
  await click('[data-bom-step="1"]'); await click('[data-bom-step="3"]'); await click('[data-bom-step="6"]');
  check('STAGE_NAVIGATION_DOES_NOT_WRITE', requests.filter(request => request.method === 'POST' && request.url.includes('/api/mks2/bom/')).length === writesBefore);
  if (exercise) {
    check('SELF_REVIEW_AND_APPROVAL_DISABLED', await evaluate("document.querySelector('[data-bom-action=review]').disabled && document.querySelector('[data-bom-action=approve]').disabled"));
    if (syntheticAggregation) {
      // This deliberately synthetic choice proves aggregation without asserting
      // an engineering mapping or applying any profile to a private BOM.
      await click('[data-bom-step="4"]');
      const fixtureRows = await evaluate("[...document.querySelectorAll('[data-bom-field=row] option')].map(option=>option.value)");
      for (const [index, rowId] of fixtureRows.slice(0, 2).entries()) {
        await fill('[data-bom-field="row"]', rowId);
        await fill('[name="application_status"]', 'APPLICABLE');
        await fill('[name="review_basis"]', 'DEMO_ASSUMPTION');
        await fill('[name="quantity_per_unit"]', String(index + 1));
        await fill('[name="unit"]', 'EA');
        await fill('[name="item_id"]', 'IM-0016');
        await fill('[name="evidence_note"]', 'Synthetic browser aggregation fixture only: same item with explicit assumed quantity; no technical manufacturing approval.');
        await click('[data-bom-form="row"] button[type="submit"]');
      }
      check('ROW_DECISIONS_STAGED_WITHOUT_SERVER_WRITE', requests.filter(request => request.method === 'POST' && request.url.includes('/api/mks2/bom/')).length === writesBefore);
    }
    await click('[data-bom-step="5"]');
    await fill('[data-bom-field="review-note"]', syntheticAggregation ? 'Synthetic aggregation acceptance: two explicit assumed rows share one item; preserve other unresolved rows and technical review status.' : 'Isolated browser acceptance: preserve every unresolved source row and technical review status.');
    await click('[data-bom-action="revise"]');
    await until("document.querySelector('.mk-bom-notice').textContent.includes('새 버전') && document.querySelector('.mk-drawer').getAttribute('aria-busy')==='false'");
    const revised = await serverDocMeta();
    check('REVISION_PRESERVES_ALL_ROWS', revised.version === meta.version + 1 && revised.rows === meta.rows && revised.status === 'DRAFT');
    await click('.mk-drawer [data-close]');
    await actor('dev-reviewer-b');
    check('ACCOUNT_SWITCH_REMOVES_OLD_DRAWER', await evaluate("document.querySelector('.mk-drawer')===null"));
    await openBom(); await click('[data-bom-step="6"]');
    check('OTHER_REVIEWER_CAN_REVIEW', await evaluate("document.querySelector('[data-bom-action=review]').disabled===false"));
    await fill('[data-bom-field="reviewer-note"]', 'Isolated browser review: original source and all rows checked; unresolved mapping and technical approval remain explicit.');
    await click('[data-bom-field="acknowledge"]');
    await click('[data-bom-action="review"]');
    await until("document.querySelector('[data-bom-action=approve]')?.disabled===false");
    check('REVIEW_PERSISTED_BEFORE_APPROVAL', (await serverDocMeta()).status === 'REVIEWED');
    await click('[data-bom-action="approve"]');
    await until("document.querySelector('.mk-bom-notice')?.textContent.includes('승인했습니다') && document.querySelector('.mk-drawer').getAttribute('aria-busy')==='false'");
    check('APPROVAL_NOT_POSTING', (await serverDocMeta()).status === 'APPROVED');
    await click('[data-bom-step="7"]');
    await click('[data-bom-action="post"]');
    await until("document.querySelector('[data-bom-action=refresh]')?.disabled===false");
    meta = await serverDocMeta();
    check('EXACT_APPROVED_BOM_POSTED', meta.status === 'POSTED' && Number.isInteger(meta.ledger_version));
    await shot('bom-posted');
    await until(`(async()=>{const session=await (await fetch('/api/mks2/session')).json();const response=await fetch('/api/mks2/snapshot',{headers:{'X-Identity-Revision':session.principal.identity_revision}});if(!response.ok)return false;const payload=await response.json();return payload.meta.ledger_version===${meta.ledger_version}&&payload.meta.projection_version===${meta.ledger_version};})()`);
    await click('[data-bom-action="refresh"]');
    await until("document.querySelector('#load-state')?.hidden && !document.querySelector('#refresh-ledger').disabled");
  } else await click('.mk-drawer [data-close]');
  check('ALL_REQUIREMENT_ROWS_RENDERED', await evaluate("(async()=>{const session=await (await fetch('/api/mks2/session')).json();const payload=await (await fetch('/api/mks2/snapshot',{headers:{'X-Identity-Revision':session.principal.identity_revision}})).json();return document.querySelectorAll('[data-register=material-rows] tbody tr').length===payload.material_requirements.rows.length && payload.material_requirements.rows.length>0;})()"));
  if (syntheticAggregation) {
    const aggregate = await evaluate("(async()=>{const session=await(await fetch('/api/mks2/session')).json();const payload=await(await fetch('/api/mks2/snapshot',{headers:{'X-Identity-Revision':session.principal.identity_revision}})).json();const requirements=payload.material_requirements;return {rows:requirements.rows.length,unresolved:requirements.unresolved_count,full_coverage:requirements.full_coverage,items:requirements.items};})()");
    const item = aggregate.items[0];
    check('SYNTHETIC_UNRESOLVED_ROWS_PRESERVED', aggregate.rows === 4 && aggregate.unresolved === 2 && aggregate.full_coverage === false);
    check('SYNTHETIC_SAME_ITEM_ROWS_AGGREGATED_ONCE', aggregate.items.length === 1 && item.item_id === 'IM-0016' && item.unit === 'EA' && item.row_ids.length === 2 && item.quantity_per_unit === '3' && item.available_qty === '20');
    check('SYNTHETIC_TARGET_REQUIREMENT_AND_SHORTAGE', item.required_qty === '120' && item.shortage_qty === '100');
    check('SYNTHETIC_PLANNED_REQUIREMENT_AND_SHORTAGE', item.planned_required_qty === '129' && item.planned_shortage_qty === '109');
    check('TARGET_TABLE_MATCHES_AGGREGATE_SERVER_VALUES', await evaluate("(()=>{const rows=document.querySelectorAll('[data-register=material-items] tbody tr');const cells=[...rows[0].querySelectorAll('td')].map(cell=>cell.textContent);return rows.length===1 && cells[1]==='120' && cells[2]==='20' && cells[3]==='100' && document.querySelector('[data-region=materials] > p').textContent.includes('전체 BOM 중 일부 품목 기준 · 1개 품목');})()"));
  }
  check('SOURCE_ARITHMETIC_MARKED_CONDITIONAL', await evaluate("document.querySelector('[data-region=materials]').textContent.includes('원문 수량 단순 환산 · 단위/실장 검수 전')"));
  check('UNKNOWN_NOT_DISPLAYED_AS_ZERO', await evaluate("document.querySelector('[data-region=materials]').textContent.includes('미확인') && !document.querySelector('[data-region=materials]').textContent.includes('undefined') && !document.querySelector('[data-region=materials]').textContent.includes('null')"));
  check('RAW_REVIEW_DETAILS_START_COLLAPSED', await evaluate("document.querySelector('.mk-material-details').open===false"));
  check('CALCULATION_SCOPE_FROM_SERVER', await evaluate("(async()=>{const session=await(await fetch('/api/mks2/session')).json();const payload=await(await fetch('/api/mks2/snapshot',{headers:{'X-Identity-Revision':session.principal.identity_revision}})).json();return document.querySelector('[data-region=materials] > p').textContent.includes(`전체 BOM 중 일부 품목 기준 · ${payload.material_requirements.items.length}개 품목`);})()"));
  await click('.mk-material-details > summary');
  const selectedEvidenceRow = await evaluate("document.querySelector('[data-bom-evidence]').dataset.bomEvidence");
  await click('[data-bom-evidence]');
  await until("document.querySelector('.mk-drawer')?.getAttribute('aria-busy')==='false' && document.querySelector('[data-bom-field=row]')");
  check('MATERIAL_EVIDENCE_OPENS_ITS_POSTED_BOM', await evaluate("(async()=>{const session=await(await fetch('/api/mks2/session')).json();const payload=await(await fetch('/api/mks2/snapshot',{headers:{'X-Identity-Revision':session.principal.identity_revision}})).json();return document.querySelector('[data-bom-field=document]').value===payload.material_requirements.bom_id;})()"));
  check('MATERIAL_EVIDENCE_OPENS_EXACT_ROW', await evaluate("document.querySelector('[data-bom-field=row]').value") === selectedEvidenceRow);
  check('UNSUPPORTED_SOURCE_VERIFIED_CHOICE_EXCLUDED', await evaluate("document.querySelector('[name=review_basis] option[value=SOURCE_VERIFIED]')===null"));
  await click('.mk-drawer [data-close]');
  await click('.mk-material-details > summary');
  await click('[data-basis="planned"]');
  check('PLAN_BASIS_LABELLED_ASSUMPTION', await evaluate("document.querySelector('[data-region=materials]').textContent.includes('가정') && document.querySelector('[data-region=materials]').textContent.includes('보장이 아닙니다')"));
  if (syntheticAggregation) check('PLANNED_TABLE_MATCHES_AGGREGATE_SERVER_VALUES', await evaluate("(()=>{const rows=document.querySelectorAll('[data-register=material-items] tbody tr');const cells=[...rows[0].querySelectorAll('td')].map(cell=>cell.textContent);return rows.length===1 && cells[1]==='129' && cells[2]==='20' && cells[3]==='109';})()"));
  await shot('bom-materials');
  }
  await click('#tab-graph');
  check('ALL_LEDGER_NODES_PRESERVED', await evaluate("document.querySelectorAll('.mks-node').length===Number(document.querySelector('.mks-graph-scope').dataset.nodeCount)"));
  await click('[data-action="schema"]');
  check('FULL_ONTOLOGY_PRESERVED', await evaluate("document.querySelectorAll('.mks-node').length===Number(document.querySelector('.mks-graph-scope').dataset.nodeCount) && document.querySelectorAll('.mks-edge').length===Number(document.querySelector('.mks-graph-scope').dataset.edgeCount)"));
  await send('Emulation.setDeviceMetricsOverride', { width: 900, height: 1100, deviceScaleFactor: 1, mobile: false });
  await click('#tab-dashboard');
  check('TABLET_LAYOUT_CONTAINED', await evaluate("document.documentElement.scrollWidth<=window.innerWidth"));
  check('NO_PREVIEW_FIXTURE_REQUESTS', requests.every(request => !request.url.includes('/api/preview')));
  // The managed browser injects its own chrome-extension cursor image. This is
  // an intrinsic browser asset, not an application request to another server.
  report.browser_extension_assets = requests.filter(request => new URL(request.url).protocol === 'chrome-extension:').length;
  report.unexpected_request_origins = [...new Set(requests.filter(request => { const url = new URL(request.url); return url.origin !== new URL(base).origin && url.protocol !== 'chrome-extension:' && request.url !== 'about:blank'; }).map(request => new URL(request.url).origin))];
  check('NO_EXTERNAL_NETWORK_REQUESTS', report.unexpected_request_origins.length === 0);
  check('NO_BROWSER_EXCEPTIONS', exceptions.length === 0);
  if (!exercise) check('NO_BOM_MUTATIONS_IN_READ_ONLY_RUN', requests.every(request => request.method !== 'POST' || !request.url.includes('/api/mks2/bom/')));
  report.status = 'PASS';
} catch (error) {
  report.status = 'FAIL'; report.error = error.message;
  try { await shot('failure'); } catch {}
  process.exitCode = 1;
} finally {
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2), { mode: 0o600 });
  console.log(JSON.stringify(report));
  ws.close();
}
