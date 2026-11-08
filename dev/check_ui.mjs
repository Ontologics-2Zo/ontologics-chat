// Run against a separately started local preview and isolated Chromium debugging port.
// Screenshots can contain private references: the artifact directory must be outside Git.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const options = Object.fromEntries(process.argv.slice(2).map(arg => arg.replace(/^--/,'').split(/=(.*)/s)));
const base = options.url || 'http://127.0.0.1:8765/';
const cdp = options.cdp || 'http://127.0.0.1:9233';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (!options.artifacts) throw Error('An explicit --artifacts directory outside the repository is required.');
const output = path.resolve(options.artifacts);
fs.mkdirSync(output, {recursive:true,mode:0o700});
if (fs.realpathSync(output).startsWith(`${repo}${path.sep}`) || fs.realpathSync(output) === repo) throw Error('Private artifacts must remain outside the repository.');
for (const url of [base, cdp]) if (!['127.0.0.1','localhost'].includes(new URL(url).hostname)) throw Error('This check is local only.');
const target = await (await fetch(`${cdp}/json/new?about:blank`,{method:'PUT'})).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject) => { ws.onopen=resolve;ws.onerror=reject; });
let sequence=0;
const pending=new Map(), requests=[], exceptions=[], checks=[];
ws.onmessage=async event => {
  const message=JSON.parse(event.data);
  if (message.method === 'Fetch.requestPaused') {
    try {
      const response=await send('Fetch.getResponseBody',{requestId:message.params.requestId});
      const payload=JSON.parse(response.base64Encoded?Buffer.from(response.body,'base64').toString():response.body);
      payload.bundle.graph.meta.projection_version++;
      const body=Buffer.from(JSON.stringify(payload)).toString('base64');
      await send('Fetch.fulfillRequest',{requestId:message.params.requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/json'}],body});
    } catch { await send('Fetch.failRequest',{requestId:message.params.requestId,errorReason:'Failed'}); }
    return;
  }
  if (message.method === 'Network.requestWillBeSent') requests.push({method:message.params.request.method,url:message.params.request.url});
  if (message.method === 'Runtime.exceptionThrown') exceptions.push('BROWSER_EXCEPTION');
  if (message.id && pending.has(message.id)) {
    const promise=pending.get(message.id);pending.delete(message.id);
    message.error ? promise.reject(Error('CDP_COMMAND_FAILED')) : promise.resolve(message.result);
  }
};
function send(method,params={}) {return new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression) {
  const response=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if (response.exceptionDetails) throw Error('BROWSER_EVALUATION_FAILED');
  return response.result.value;
}
async function until(expression) {
  for(let attempt=0;attempt<120;attempt++) {if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,75));}
  throw Error('UI_WAIT_TIMEOUT');
}
function check(name,condition) {checks.push({name,status:condition?'PASS':'FAIL'});if(!condition)throw Error(name);}
async function shot(name) {
  await evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
  const {data}=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  fs.writeFileSync(path.join(output,`${name}.png`),Buffer.from(data,'base64'),{mode:0o600});
}
async function click(selector) {await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);}
const report={scope:'UI/reference preview only; no service, database, projection or model verification',viewport:[1536,1100],checks};
try {
  await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1536,height:1100,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:base});
  await until("document.querySelector('#load-state')?.hidden === true");
  check('READ_ONLY_PREVIEW_LOADED',await evaluate("document.body.innerText.includes('원장 미연결')"));
  check('GRAPH_PANEL_400PX',await evaluate("document.querySelector('#graph-panel').getBoundingClientRect().width===400"));
  await shot('current-graph');
  await click('#tab-dashboard');await shot('current-dashboard');await click('#tab-graph');
  const beforeSuggestion=requests.length;
  await click('#graph-panel .suggestion');
  check('SUGGESTION_ONLY_FILLS_DRAFT',await evaluate("document.querySelector('#graph-panel input').value.length>0 && document.querySelectorAll('#graph-panel .message.user').length===0") && requests.length===beforeSuggestion);
  await evaluate("document.querySelector('#graph-panel form').requestSubmit()");
  await until("document.querySelector('#graph-panel .answer') !== null");
  check('EVIDENCE_PATH_VISIBLE',await evaluate("document.querySelectorAll('.mks-edge.mks-hit').length>0 && document.querySelectorAll('#graph-panel .evidence-path button').length>0"));
  await shot('current-evidence');
  await click('#graph-panel .suggestion');
  await evaluate("document.querySelector('#graph-panel form').requestSubmit();[...document.querySelectorAll('#graph-panel .answer-footer button')].find(e=>e.textContent==='근거 다시 보기').click()");
  await new Promise(resolve=>setTimeout(resolve,750));
  check('OLD_REPLAY_CANCELS_NEW_PENDING_ANSWER',await evaluate("document.querySelectorAll('#graph-panel .answer').length===1 && document.querySelector('#graph-panel .messages').textContent.includes('근거 재생을 중단')"));
  await click('#graph-panel .evidence-path button');
  check('EVIDENCE_OPENS_DETAIL',await evaluate("document.querySelector('.mks-graph-detail').hidden===false"));
  await shot('current-detail');
  await click('#reset');
  await click('#graph-panel .suggestion');
  await evaluate("document.querySelector('#graph-panel form').requestSubmit();document.querySelector('#reset').click()");
  await new Promise(resolve=>setTimeout(resolve,750));
  check('RESET_CANCELS_PENDING_ANSWER',await evaluate("document.querySelector('#graph-panel .answer')===null && document.querySelectorAll('.mks-edge.mks-hit').length===0"));
  await click('#tab-dashboard');
  check('DASHBOARD_PANEL_440PX',await evaluate("document.querySelector('#dashboard-panel').getBoundingClientRect().width===440"));
  check('NO_BOM_CALCULATION_HELD',await evaluate("document.querySelector('[data-region=materials]').textContent.includes('계산할 수 없습니다') && !document.querySelector('#dashboard-stage').textContent.includes('null')"));
  await until("document.querySelector('#notice').hidden");
  await click('#dashboard-stage [data-action=bom]');
  check('BOM_DRAWER_760PX',await evaluate("document.querySelector('.mk-drawer').getBoundingClientRect().width===760"));
  await shot('current-bom');
  await click('[data-bom-step="2"]');
  check('SOURCE_ROWS_REFERENCE_ONLY',await evaluate("document.querySelector('.mk-drawer').textContent.includes('현재 서비스의 파싱·검증 실행 결과가 아닙니다') && document.querySelectorAll('.mk-drawer tbody tr').length>0"));
  await click('[data-bom-step="6"]');
  check('APPROVAL_WRITE_DISABLED',await evaluate("document.querySelector('.mk-drawer-body button').disabled===true"));
  await evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  check('DRAWER_ESCAPE_CLOSES',await evaluate("document.querySelector('.mk-drawer')===null"));
  await click('#dashboard-panel .suggestion');
  await evaluate("document.querySelector('#dashboard-panel form').requestSubmit()");
  check('AGENT_REFERENCE_HELD',await evaluate("document.querySelector('#dashboard-panel .verdict').textContent.includes('판단 보류')"));
  await evaluate("[...document.querySelectorAll('#dashboard-panel button')].find(e=>e.textContent==='등록 후 견적 화면 예시').click()");
  check('QUOTE_SNAPSHOT_SEPARATE',await evaluate("document.querySelector('#dashboard-panel .messages').textContent.includes('별도 시연') && document.querySelector('#dashboard-panel .messages').textContent.includes('현재 원장에 반영된 결과가 아닙니다')"));
  await evaluate("document.querySelector('#dashboard-panel .mk-quote-list')?.scrollIntoView({block:'start'})");
  check('QUOTE_FIELDS_MATCH_REFERENCE',await evaluate("document.querySelectorAll('#dashboard-panel .mk-quote-list .mk-quote-card').length===3 && document.querySelector('#dashboard-panel .mk-quote-list').textContent.includes('상품 금액')"));
  await shot('current-quotes');
  await click('#theme');
  check('DARK_THEME',await evaluate("document.documentElement.dataset.theme==='dark'"));
  await click('#theme');
  await send('Emulation.setDeviceMetricsOverride',{width:768,height:1024,deviceScaleFactor:1,mobile:false});
  check('TABLET_NO_PAGE_OVERFLOW',await evaluate('document.documentElement.scrollWidth<=innerWidth'));
  await shot('current-tablet');
  await send('Emulation.setDeviceMetricsOverride',{width:1536,height:1100,deviceScaleFactor:1,mobile:false});
  await send('Page.reload',{ignoreCache:true});
  await until("document.querySelector('#load-state')?.hidden === true");
  check('RELOAD_RETURNS_INITIAL_REFERENCE',await evaluate("document.querySelector('#dashboard-stage').textContent.includes('구성 미등록')"));
  await send('Fetch.enable',{patterns:[{urlPattern:new URL('/api/preview',base).href,requestStage:'Response'}]});
  await send('Page.reload',{ignoreCache:true});
  await until("document.querySelector('#workspace')?.getAttribute('aria-busy')==='false'");
  check('MIXED_SNAPSHOT_FAILS_CLOSED',await evaluate("!document.querySelector('#load-state').hidden && document.querySelector('#load-state').textContent.includes('조회 버전이 달라') && document.querySelectorAll('.mks-node').length===0"));
  await send('Fetch.disable');
  check('NO_BROWSER_EXCEPTIONS',exceptions.length===0);
  check('NO_WRITE_OR_EXTERNAL_REQUESTS',requests.every(r=>r.method==='GET' && new URL(r.url).origin===new URL(base).origin));
  report.status='PASS';report.request_count=requests.length;report.model_calls=0;report.ledger_writes=0;
  console.log(JSON.stringify({status:report.status,checks:checks.length,model_calls:0,ledger_writes:0}));
} catch (error) {
  report.status='FAIL';report.failure=error.message;process.exitCode=1;
  console.log(JSON.stringify({status:'FAIL',check:error.message}));
} finally {
  fs.writeFileSync(path.join(output,'ui-checks.json'),JSON.stringify(report,null,2)+'\n',{mode:0o600});
  await fetch(`${cdp}/json/close/${target.id}`).catch(()=>{});ws.close();
}
