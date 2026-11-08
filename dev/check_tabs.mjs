// Checks the local comparison workspace only. Screenshots may contain private references.
// The original product files are verified against the preserved baseline before opening a tab.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const options = Object.fromEntries(process.argv.slice(2).map(arg => arg.replace(/^--/, '').split(/=(.*)/s)));
const base = options.url || 'http://127.0.0.1:8765/';
const cdp = options.cdp || 'http://127.0.0.1:9233';
const repo = fs.realpathSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'));
if (!options.artifacts) throw Error('An explicit --artifacts directory outside the repository is required.');
const output = path.resolve(options.artifacts);
const insideRepo = value => value === repo || value.startsWith(`${repo}${path.sep}`);
if (insideRepo(output)) throw Error('Private artifacts must remain outside the repository.');
fs.mkdirSync(output, { recursive: true, mode: 0o700 });
if (insideRepo(fs.realpathSync(output))) throw Error('Private artifacts must remain outside the repository.');
for (const url of [base, cdp]) {
  const parsed = new URL(url);
  if (!['http:', 'https:'].includes(parsed.protocol) || !['127.0.0.1', 'localhost'].includes(parsed.hostname) || parsed.username || parsed.password) throw Error('This check accepts local, credential-free URLs only.');
}

const checks = [];
const report = {
  scope: 'Local tab isolation and preserved-source verification only; no backend, database, projection or model verification',
  baseline: 'b3ee599',
  viewport: [1536, 1100],
  checks,
};
function check(name, condition) {
  checks.push({ name, status: condition ? 'PASS' : 'FAIL' });
  if (!condition) throw Error(name);
}
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const productFiles = ['index.html', 'styles.css', 'app.js', 'dashboard.js', 'dashboard.css', 'graph.js', 'graph.css'];
let target, ws;
const pending = new Map();
const requests = [];
const exceptions = [];
let sequence = 0;

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(Error('CDP_COMMAND_TIMEOUT')); }, 15000);
    pending.set(id, { resolve, reject, timer });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (response.exceptionDetails) throw Error('BROWSER_EVALUATION_FAILED');
  return response.result.value;
}
async function until(expression) {
  for (let attempt = 0; attempt < 160; attempt++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 75));
  }
  throw Error('UI_WAIT_TIMEOUT');
}
async function click(selector) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
}
const frameDoc = id => `document.getElementById(${JSON.stringify(id)}).contentDocument`;
const visible = id => `document.getElementById(${JSON.stringify(id)}).getBoundingClientRect().width > 0 && document.getElementById(${JSON.stringify(id)}).getBoundingClientRect().height > 0`;
const ready = id => `${frameDoc(id)}?.querySelector('#load-state')?.hidden === true && ${frameDoc(id)}.querySelectorAll('.mks-node').length > 0`;
const previewRequestCount = () => requests.filter(request => request.pathname.endsWith('/api/preview')).length;
async function shot(name) {
  await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(path.join(output, `${name}.png`), Buffer.from(data, 'base64'), { mode: 0o600 });
}

try {
  const preserved = productFiles.map(name => {
    const relative = `web/${name}`;
    const baseline = execFileSync('git', ['show', `b3ee599:${relative}`], { cwd: repo, stdio: ['ignore', 'pipe', 'pipe'] });
    return { path: relative, sha256: hash(baseline), unchanged: hash(fs.readFileSync(path.join(repo, relative))) === hash(baseline) };
  });
  report.preserved_files = preserved;
  check('ORIGINAL_SEVEN_PRODUCT_FILES_UNCHANGED', preserved.every(file => file.unchanged));

  target = await (await fetch(`${cdp}/json/new?about:blank`, { method: 'PUT' })).json();
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(Error('CDP_CONNECTION_FAILED')); });
  ws.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Network.requestWillBeSent') {
      const request = message.params.request;
      const parsed = new URL(request.url);
      requests.push({ method: request.method, origin: parsed.origin, pathname: parsed.pathname });
    }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push('BROWSER_EXCEPTION');
    if (message.id && pending.has(message.id)) {
      const promise = pending.get(message.id);
      clearTimeout(promise.timer);
      pending.delete(message.id);
      message.error ? promise.reject(Error('CDP_COMMAND_FAILED')) : promise.resolve(message.result);
    }
  };
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1536, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: base });
  await until(ready('frame-current'));
  check('CURRENT_TAB_IS_DEFAULT', await evaluate(visible('frame-current')));
  check('CURRENT_ROUTE_IS_FIXED_SAME_ORIGIN', await evaluate("new URL(document.getElementById('frame-current').src).origin === location.origin && new URL(document.getElementById('frame-current').src).pathname === '/current/' && new URL(document.getElementById('frame-current').src).hash === '#graph'"));
  check('MKS2_FRAME_IS_LAZY', !requests.some(request => request.pathname.startsWith('/mks2/')) && previewRequestCount() === 1);
  await evaluate(`(() => {
    const frame = document.getElementById('frame-current');
    const doc = frame.contentDocument;
    const node = doc.querySelector('.mks-node');
    node.dispatchEvent(new frame.contentWindow.MouseEvent('click', {bubbles:true}));
    doc.querySelector('#graph-panel input').value = 'UI_TAB_CURRENT_DRAFT';
    window.__tabsCheck = {currentDocument: doc, currentWindow: frame.contentWindow, currentNode: node.dataset.nodeId, currentTimeOrigin: frame.contentWindow.performance.timeOrigin};
  })()`);
  check('CURRENT_SELECTION_AND_DRAFT_SET', await evaluate(`${frameDoc('frame-current')}.querySelector('.mks-selected')?.dataset.nodeId === window.__tabsCheck.currentNode && ${frameDoc('frame-current')}.querySelector('#graph-panel input').value === 'UI_TAB_CURRENT_DRAFT'`));
  await shot('workspace-current');

  await click('#workspace-mks2');
  await until(ready('frame-mks2'));
  check('MKS2_ROUTE_IS_FIXED_SAME_ORIGIN', await evaluate("new URL(document.getElementById('frame-mks2').src).origin === location.origin && new URL(document.getElementById('frame-mks2').src).pathname === '/mks2/' && new URL(document.getElementById('frame-mks2').src).hash === '#graph'"));
  check('ONE_REFERENCE_READ_PER_FRAME', previewRequestCount() === 2);
  check('MKS2_VISIBLE_CURRENT_HIDDEN', await evaluate(`(${visible('frame-mks2')}) && !(${visible('frame-current')})`));
  check('MKS2_GRAPH_CONNECTION_SCOPE_DISCLOSED', await evaluate(`${frameDoc('frame-mks2')}.querySelector('.mks-graph-scope')?.textContent.includes('전체 온톨로지 연결 전') && ${frameDoc('frame-current')}.querySelector('.mks-graph-scope') === null`));
  check('MKS2_STARTS_WITH_INDEPENDENT_STATE', await evaluate(`${frameDoc('frame-mks2')}.querySelector('#graph-panel input').value === '' && ${frameDoc('frame-mks2')}.querySelector('.mks-selected') === null`));
  await evaluate(`(() => {
    const frame = document.getElementById('frame-mks2');
    const doc = frame.contentDocument;
    const node = doc.querySelectorAll('.mks-node')[1];
    node.dispatchEvent(new frame.contentWindow.MouseEvent('click', {bubbles:true}));
    doc.querySelector('#graph-panel input').value = 'UI_TAB_MKS2_DRAFT';
    window.__tabsCheck.mks2Document = doc;
    window.__tabsCheck.mks2Node = node.dataset.nodeId;
    window.__tabsCheck.mks2TimeOrigin = frame.contentWindow.performance.timeOrigin;
  })()`);
  await shot('workspace-mks2');
  await click('#workspace-current');
  await until(visible('frame-current'));
  check('CURRENT_DOCUMENT_NOT_RELOADED', await evaluate(`${frameDoc('frame-current')} === window.__tabsCheck.currentDocument && document.getElementById('frame-current').contentWindow.performance.timeOrigin === window.__tabsCheck.currentTimeOrigin`));
  check('CURRENT_SELECTION_AND_DRAFT_RETAINED', await evaluate(`${frameDoc('frame-current')}.querySelector('.mks-selected')?.dataset.nodeId === window.__tabsCheck.currentNode && ${frameDoc('frame-current')}.querySelector('#graph-panel input').value === 'UI_TAB_CURRENT_DRAFT'`));
  check('HIDDEN_MKS2_STATE_RETAINED', await evaluate(`${frameDoc('frame-mks2')} === window.__tabsCheck.mks2Document && ${frameDoc('frame-mks2')}.querySelector('.mks-selected')?.dataset.nodeId === window.__tabsCheck.mks2Node && ${frameDoc('frame-mks2')}.querySelector('#graph-panel input').value === 'UI_TAB_MKS2_DRAFT'`));

  await click('#workspace-mks2');
  await until(visible('frame-mks2'));
  await evaluate(`${frameDoc('frame-mks2')}.querySelector('#reset').click()`);
  check('MKS2_RESET_IS_LOCAL', await evaluate(`${frameDoc('frame-mks2')}.querySelector('#graph-panel input').value === '' && ${frameDoc('frame-mks2')}.querySelector('.mks-selected') === null && ${frameDoc('frame-current')}.querySelector('#graph-panel input').value === 'UI_TAB_CURRENT_DRAFT' && ${frameDoc('frame-current')}.querySelector('.mks-selected')?.dataset.nodeId === window.__tabsCheck.currentNode`));
  check('SWITCH_AND_RESET_DO_NOT_READ_API_AGAIN', previewRequestCount() === 2);
  check('BOTH_FRAMES_DISCLOSE_REFERENCE_MODE', await evaluate(`['frame-current','frame-mks2'].every(id => { const text = document.getElementById(id).contentDocument.body.textContent; return text.includes('원장 미연결') && text.includes('실제 AI 미연결'); })`));

  await click('#workspace-checks');
  await until(visible('panel-checks'));
  check('CHECKLIST_HIDES_BOTH_FRAMES_WITHOUT_UNLOADING', await evaluate(`!(${visible('frame-current')}) && !(${visible('frame-mks2')}) && ${frameDoc('frame-current')} === window.__tabsCheck.currentDocument && ${frameDoc('frame-mks2')} === window.__tabsCheck.mks2Document`));
  await evaluate("window.__tabsCheck.manualCount = document.getElementById('manual-count').textContent; window.__tabsCheck.serviceState = document.getElementById('checks-service-state').textContent");
  check('MANUAL_CHECKLIST_DISCLOSED', await evaluate("document.getElementById('panel-checks').textContent.includes('수동 확인') && document.getElementById('checks-service-state').textContent.includes('서비스 검증 미실행') && document.getElementById('checks-service-state').textContent.includes('NOT_RUN')"));
  await click('#panel-checks input[data-manual-check]');
  check('MANUAL_CHECK_CHANGES_ONLY_MANUAL_COUNT', await evaluate("document.getElementById('manual-count').textContent !== window.__tabsCheck.manualCount && document.getElementById('checks-service-state').textContent === window.__tabsCheck.serviceState"));
  check('MANUAL_CHECK_DOES_NOT_CALL_API', previewRequestCount() === 2);
  await shot('workspace-checklist');

  for (const hostileHash of ['#https://example.invalid/', '#mks2?src=https://example.invalid/', '#javascript:alert(1)']) {
    await evaluate(`location.hash = ${JSON.stringify(hostileHash)}`);
    await until(visible('frame-current'));
    check('HOSTILE_HASH_FIXED_ROUTE_' + checks.filter(item => item.name.startsWith('HOSTILE_HASH')).length, await evaluate("['frame-current','frame-mks2'].every(id => { const url = new URL(document.getElementById(id).src); return url.origin === location.origin && ['/current/','/mks2/'].includes(url.pathname); })"));
  }
  check('HOSTILE_HASH_DOES_NOT_RELOAD_FRAMES', await evaluate(`${frameDoc('frame-current')} === window.__tabsCheck.currentDocument && ${frameDoc('frame-mks2')} === window.__tabsCheck.mks2Document && document.getElementById('frame-current').contentWindow.performance.timeOrigin === window.__tabsCheck.currentTimeOrigin && document.getElementById('frame-mks2').contentWindow.performance.timeOrigin === window.__tabsCheck.mks2TimeOrigin`));
  check('TOTAL_REFERENCE_READS_REMAIN_TWO', previewRequestCount() === 2);
  check('NO_BROWSER_EXCEPTIONS', exceptions.length === 0);
  check('NO_WRITES_OR_EXTERNAL_REQUESTS', requests.every(request => request.method === 'GET' && request.origin === new URL(base).origin));
  report.status = 'PASS';
  report.request_count = requests.length;
  report.reference_reads = previewRequestCount();
  report.model_calls = 0;
  report.ledger_writes = 0;
  console.log(JSON.stringify({ status: 'PASS', checks: checks.length, reference_reads: report.reference_reads, model_calls: 0, ledger_writes: 0 }));
} catch (error) {
  report.status = 'FAIL';
  // Fixed test codes only; do not print provider response bodies, fixture text or process errors.
  report.failure = checks.at(-1)?.status === 'FAIL' ? checks.at(-1).name : ['UI_WAIT_TIMEOUT', 'BROWSER_EVALUATION_FAILED', 'CDP_COMMAND_TIMEOUT', 'CDP_COMMAND_FAILED', 'CDP_CONNECTION_FAILED'].includes(error.message) ? error.message : 'CHECK_SETUP_FAILED';
  process.exitCode = 1;
  console.log(JSON.stringify({ status: 'FAIL', check: report.failure }));
} finally {
  fs.writeFileSync(path.join(output, 'tab-checks.json'), JSON.stringify(report, null, 2) + '\n', { mode: 0o600 });
  for (const promise of pending.values()) clearTimeout(promise.timer);
  pending.clear();
  if (target?.id) await fetch(`${cdp}/json/close/${target.id}`).catch(() => {});
  ws?.close();
}
