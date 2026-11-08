// Authenticated MK-S2 integration checks. Output only assertions; captures remain outside Git.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const options = Object.fromEntries(process.argv.slice(2).map(arg => arg.replace(/^--/, '').split(/=(.*)/s)));
const base = options.url || 'http://127.0.0.1:8767/mks2/';
const cdp = options.cdp || 'http://127.0.0.1:9233';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
for (const url of [base, cdp]) if (!['127.0.0.1', 'localhost'].includes(new URL(url).hostname)) throw Error('LOCAL_ONLY');
if (!options.artifacts) throw Error('PRIVATE_ARTIFACT_DIRECTORY_REQUIRED');
const output = path.resolve(options.artifacts);
fs.mkdirSync(output, { recursive: true, mode: 0o700 });
if (fs.realpathSync(output).startsWith(`${repo}${path.sep}`) || fs.realpathSync(output) === repo) throw Error('PRIVATE_ARTIFACTS_OUTSIDE_GIT');
const target = await (await fetch(`${cdp}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let sequence = 0, interceptedFailure = 'projection';
const pending = new Map(), requests = [], exceptions = [], checks = [];
ws.onmessage = async event => {
  const message = JSON.parse(event.data);
  if (message.method === 'Fetch.requestPaused') {
    try {
      if (interceptedFailure === 'identity') {
        await send('Fetch.fulfillRequest', { requestId: message.params.requestId, responseCode: 409, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify({error:{code:'IDENTITY_CHANGED'}})).toString('base64') });
        return;
      }
      const response = await send('Fetch.getResponseBody', { requestId: message.params.requestId });
      const payload = JSON.parse(response.base64Encoded ? Buffer.from(response.body, 'base64').toString() : response.body);
      payload.meta.projection_state = 'NOT_READY';
      const body = Buffer.from(JSON.stringify(payload)).toString('base64');
      await send('Fetch.fulfillRequest', { requestId: message.params.requestId, responseCode: 200, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body });
    } catch { await send('Fetch.failRequest', { requestId: message.params.requestId, errorReason: 'Failed' }); }
    return;
  }
  if (message.method === 'Network.requestWillBeSent') requests.push({ method: message.params.request.method, url: message.params.request.url });
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
async function until(expression) {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw Error('UI_WAIT_TIMEOUT');
}
function check(name, condition) { checks.push({ name, status: condition ? 'PASS' : 'FAIL' }); if (!condition) throw Error(name); }
async function click(selector) { await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`); }
async function shot(name) {
  await evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(path.join(output, `${name}.png`), Buffer.from(data, 'base64'), { mode: 0o600 });
}
const report = { scope: 'MK-S2 server session, read model, full ontology rendering and deterministic query UI; no live AI/BOM write acceptance', viewport: [1536, 1100], checks };
try {
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1536, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: base });
  await until("document.querySelector('#actor')?.disabled === false");
  if (!await evaluate("document.querySelector('#load-state')?.hidden")) {
    await evaluate("(()=>{const select=document.querySelector('#actor');select.value=[...select.options].find(o=>o.value)?.value;select.dispatchEvent(new Event('change'));})()");
  }
  await until("document.querySelector('#load-state')?.hidden === true");
  check('SERVER_CONNECTED', await evaluate("document.querySelector('#versions').textContent.includes('서버 연결')"));
  check('ORIGINAL_GRAPH_PANEL_WIDTH', await evaluate("document.querySelector('#graph-panel').getBoundingClientRect().width===400"));
  check('ALL_CURRENT_NODES_RENDERED', await evaluate("document.querySelectorAll('.mks-node').length===Number(document.querySelector('.mks-graph-scope').dataset.nodeCount)"));
  await shot('ledger-graph');
  const before = requests.filter(r => r.url.endsWith('/query')).length;
  await click('#graph-panel .suggestion');
  check('SUGGESTION_ONLY_FILLS_DRAFT', await evaluate("document.querySelector('#graph-panel input').value.length>0 && document.querySelectorAll('#graph-panel .message.user').length===0") && requests.filter(r => r.url.endsWith('/query')).length === before);
  await evaluate("document.querySelector('#graph-panel form').requestSubmit()");
  await until("document.querySelector('#graph-panel .answer')!==null");
  check('LIVE_DOMAIN_QUERY_SENT', requests.filter(r => r.url.endsWith('/query') && r.method === 'POST').length > before);
  check('VERSIONED_QUERY_EVIDENCE', await evaluate("document.querySelector('#graph-panel .answer-footer').textContent.includes('원장 v') && document.querySelectorAll('#graph-panel .evidence-path button').length>0"));
  await click('#graph-panel .evidence-path button');
  check('EVIDENCE_OPENS_DETAIL', await evaluate("document.querySelector('.mks-graph-detail').hidden===false"));
  await shot('ledger-evidence');
  const priorAnswers = await evaluate("document.querySelectorAll('#graph-panel .answer').length");
  await click('#refresh-ledger'); await until("document.querySelector('#load-state').hidden && !document.querySelector('#refresh-ledger').disabled");
  check('REFRESH_PRESERVES_PRIOR_ANSWERS', await evaluate("document.querySelectorAll('#graph-panel .answer').length") === priorAnswers);
  await click('[data-action="schema"]');
  check('FULL_ONTOLOGY_NOT_TRUNCATED', await evaluate("document.querySelectorAll('.mks-node').length===Number(document.querySelector('.mks-graph-scope').dataset.nodeCount) && document.querySelectorAll('.mks-edge').length===Number(document.querySelector('.mks-graph-scope').dataset.edgeCount)"));
  check('SCHEMA_COUNTS_MATCH_API', await evaluate("(async()=>{const session=await (await fetch('/api/mks2/session')).json();const response=await fetch('/api/mks2/ontology',{headers:{'X-Identity-Revision':session.principal.identity_revision}});const data=await response.json();return data.graph.data.nodes.length===document.querySelectorAll('.mks-node').length && data.graph.data.edges.length===document.querySelectorAll('.mks-edge').length})()"));
  await shot('full-ontology');
  await click('#tab-dashboard');
  check('ORIGINAL_DASHBOARD_PANEL_WIDTH', await evaluate("document.querySelector('#dashboard-panel').getBoundingClientRect().width===440"));
  check('NO_NULL_QUANTITIES', await evaluate("!document.querySelector('#dashboard-stage').textContent.includes('null') && !document.querySelector('#dashboard-stage').textContent.includes('undefined')"));
  check('ALL_REGISTER_ENTITIES_RENDERED', await evaluate("(async()=>{const session=await (await fetch('/api/mks2/session')).json();const response=await fetch('/api/mks2/snapshot',{headers:{'X-Identity-Revision':session.principal.identity_revision}});const data=await response.json();return document.querySelectorAll('[data-register=entities] tbody tr').length===data.entities.length})()"));
  await shot('ledger-dashboard');
  await click('#dashboard-stage [data-action="bom"]');
  check('ORIGINAL_BOM_DRAWER_WIDTH', await evaluate("document.querySelector('.mk-drawer').getBoundingClientRect().width===760"));
  await click('[data-bom-step="2"]');
  check('NO_FAKE_BOM_EXECUTION', await evaluate("document.querySelector('.mk-drawer').textContent.includes('실행 결과가 없습니다') && !document.querySelector('.mk-drawer').textContent.includes('참조 결과')"));
  await click('.mk-drawer .mk-btn[data-close]');
  await click('#tab-graph'); await click('#reset');
  check('RESET_ONLY_UI', await evaluate("document.querySelector('#versions').textContent.includes('서버 연결') && document.querySelector('#graph-panel .answer')===null"));
  await click('#theme');
  check('DARK_THEME', await evaluate("document.documentElement.dataset.theme==='dark'"));
  await shot('ledger-dark');
  await send('Emulation.setDeviceMetricsOverride', { width: 900, height: 1100, deviceScaleFactor: 1, mobile: false });
  check('TABLET_NO_HORIZONTAL_OVERFLOW', await evaluate("document.documentElement.scrollWidth<=window.innerWidth"));
  await send('Emulation.setDeviceMetricsOverride', { width: 1536, height: 1100, deviceScaleFactor: 1, mobile: false });
  await evaluate("(async()=>{const state=await (await fetch('/api/mks2/session')).json();const next=state.profiles.find(profile=>profile.company_id===state.principal.company_id&&profile.id!==state.profile_id);if(!next)throw Error('NO_ALTERNATE_PROFILE');document.querySelector('#graph-panel input').value='previous-account-draft';const select=document.querySelector('#actor');select.value=next.id;select.dispatchEvent(new Event('change'));})()");
  await until("document.querySelector('#load-state').hidden && !document.querySelector('#actor').disabled");
  check('ACCOUNT_SWITCH_CLEARS_OLD_CONTEXT', await evaluate("document.querySelector('#graph-panel input').value==='' && document.querySelector('#graph-panel .answer')===null && !document.body.textContent.includes('previous-account-draft')"));
  check('SESSION_COOKIE_NOT_SCRIPT_READABLE', await evaluate("!document.cookie.includes('onto_session')"));
  await evaluate("(async()=>{const state=await (await fetch('/api/mks2/session')).json();const next=state.profiles.find(profile=>profile.company_id===state.principal.company_id&&profile.id!==state.profile_id);const response=await fetch('/api/mks2/session',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'MK-S2','X-CSRF-Token':state.csrf_token,'X-Identity-Revision':state.principal.identity_revision},body:JSON.stringify({profile_id:next.id})});if(!response.ok)throw Error('OUT_OF_BAND_SWITCH_FAILED');})()");
  await until("document.querySelector('#load-state')?.textContent.includes('계정 권한이 변경')");
  check('SHARED_COOKIE_SWITCH_INVALIDATES_OLD_VIEW', await evaluate("document.querySelectorAll('.mks-node').length===0 && document.querySelector('#graph-panel').textContent==='' && document.querySelector('#dashboard-stage').textContent===''"));
  await send('Page.reload'); await until("document.querySelector('#load-state')?.hidden===true");
  await send('Fetch.enable', { patterns: [{ urlPattern: '*/api/mks2/snapshot*', requestStage: 'Response' }] });
  await send('Page.reload');
  await until("document.querySelector('#load-state')?.textContent.includes('투영') && !document.querySelector('#load-state').hidden");
  check('EQUAL_VERSION_UNREADY_PROJECTION_BLOCKED', await evaluate("!document.querySelector('#versions').textContent.includes('서버 연결') && document.querySelectorAll('.mks-node').length===0"));
  await send('Fetch.disable');
  await send('Page.reload'); await until("document.querySelector('#load-state')?.hidden === true");
  interceptedFailure = 'identity';
  await send('Fetch.enable', { patterns: [{ urlPattern: '*/api/mks2/events*', requestStage: 'Response' }] });
  await until("document.querySelector('#load-state')?.textContent.includes('계정 권한이 변경')");
  check('IDENTITY_REVOCATION_RESPONSE_CLEARS_UI', await evaluate("document.querySelectorAll('.mks-node').length===0 && document.querySelector('#dashboard-stage').textContent==='' && document.querySelector('#graph-panel').textContent==='' && document.querySelector('#dashboard-panel').textContent===''"));
  await send('Fetch.disable');
  await send('Page.reload'); await until("document.querySelector('#load-state')?.hidden === true");
  await evaluate("(()=>{const select=document.querySelector('#actor');select.value='';select.dispatchEvent(new Event('change'));})()");
  await until("document.querySelector('#load-state')?.textContent.includes('세션을 종료했습니다')");
  check('LOGOUT_CLEARS_UI_AND_SERVER_SESSION', await evaluate("(async()=>{const state=await (await fetch('/api/mks2/session')).json();return !state.authenticated && document.querySelectorAll('.mks-node').length===0 && document.querySelector('#graph-panel').textContent===''})()"));
  check('NO_PREVIEW_FIXTURE_REQUESTS', requests.every(r => !r.url.includes('/api/preview')));
  check('NO_EXTERNAL_REQUESTS', requests.every(r => r.url.startsWith(new URL(base).origin) || r.url === 'about:blank'));
  check('NO_BROWSER_EXCEPTIONS', exceptions.length === 0);
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
