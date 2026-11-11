// MK-S2 relationship graph (DEC-H47): flow lanes, network, card and attribute views.
// Presentation only. Nodes, edges and attributes are the authenticated ledger
// snapshot's entities/relationships; derived class/attribute nodes and folded
// row groups are display aids, never ledger relations. No fetch or write here.
export function mountLaneGraph(container, { data, summary = {}, itemOrder = [], activeBom = null, onSelect = () => {}, onBom = () => {} } = {}) {
  const root = document.createElement('div');
  root.className = 'lg-root';
  root.innerHTML = `<div class="canvas" data-lg="canvas">
    <svg data-lg="svg" viewBox="0 0 1880 1000" role="img" aria-label="업무 관계 그래프: 위 레인은 업무 흐름, 아래 레인은 BOM 버전 이력과 원문 근거"></svg>
    <div class="gbar">
      <div class="gsearch"><input data-lg="gfind" type="search" placeholder="찾기: ID · 위치(U3) · 품목명 · 원문 값" autocomplete="off" aria-label="그래프에서 찾기"><div class="gres" data-lg="gres" hidden></div></div>
      <div class="layers modes" role="group" aria-label="보기 방식"><button type="button" class="lay" data-mode="lane" aria-pressed="true">흐름 레인</button><button type="button" class="lay" data-mode="net" aria-pressed="false">네트워크</button><button type="button" class="lay" data-mode="card" aria-pressed="false" title="네트워크 배치 + 카드형 노드">카드형</button><button type="button" class="lay" data-mode="rdf" aria-pressed="false" title="클래스(type)·속성값(attributes) 노드를 파생해 펼침">속성 확장</button></div>
      <div class="layers" role="group" aria-label="표시 레이어"><button type="button" class="lay" data-layer="rows" aria-pressed="true">BOM 행 묶음</button><button type="button" class="lay" data-layer="docs" aria-pressed="true">문서 이력</button><button type="button" class="lay" data-layer="labels" aria-pressed="false">관계 이름</button></div>
      <span class="sp"></span>
      <button class="btn sm" data-lg="relayout" type="button" hidden>다시 배치</button><button class="btn sm" data-lg="zout" type="button" aria-label="축소">−</button><button class="btn sm" data-lg="zin" type="button" aria-label="확대">+</button><button class="btn sm" data-lg="fit" type="button">전체 보기</button><button class="btn sm" type="button" data-lg="bom">BOM 등록</button>
    </div>
    <div class="legend" aria-hidden="true"><span><i class="lc" style="--cat:var(--c-dem)"></i>수요·생산</span><span><i class="lc" style="--cat:var(--c-bom)"></i>구성·BOM</span><span><i class="lc" style="--cat:var(--c-inv)"></i>품목·재고·예약</span><span><i class="lc" style="--cat:var(--c-pro)"></i>조달</span><span><i class="lc" style="--cat:var(--c-ev)"></i>근거·문서</span><span><i class="ld"></i>시연 가정·미확약</span><span><b class="lx">×N</b>묶인 관계 수</span><span>┈ 레인 간 연결</span><span class="lane-only">상자 오른쪽 위 = 데이터 출처</span><span class="net-only">노드를 끌어 옮기기</span><span class="rdf-only"><b class="lx">Class</b> type에서 · <b class="lx">▪</b> 속성값은 attributes에서 파생 (원장 관계 아님)</span></div>
    <div class="snapinfo" data-lg="snapinfo"></div>
    <div class="banner" data-lg="lagban" role="status" hidden></div>
    <aside class="rowx" data-lg="rowx" aria-label="BOM 행 탐색기" hidden></aside>
    <aside class="insp" data-lg="detail" aria-label="선택한 객체 상세" hidden></aside>
  </div>`;
  container.append(root);
  const $ = name => root.querySelector(`[data-lg="${name}"]`);
  const listeners = [];
  const on = (target, type, handler, options) => { target.addEventListener(type, handler, options); listeners.push([target, type, handler, options]); };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const fmt = n => n == null ? '—' : Number(n).toLocaleString('ko-KR');
  const reduced = () => globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  let traceTimer = [], traceDone = null, destroyed = false;
  function stopTrace() { traceTimer.forEach(clearTimeout); traceTimer = []; const done = traceDone; traceDone = null; done?.(false); }
  let MODE = 'lane';
  const MODES = ['lane', 'net', 'card', 'rdf'];
  const appByItem = displayId => APPS.find(a => a.item && did(a.item) === displayId);
  function chainIds() {
    const byDisplay = (id, types) => GD.nodes.find(n => n.display_id === id && (!types || types.includes(n.type)))?.id;
    const round = byDisplay(summary.delivery_round_id, ['DeliveryRound']) || ofType('DeliveryRound')[0]?.id;
    const job = byDisplay(summary.production_job_id, ['ProductionJob']) || (round && (ins(round, 'jobForRound')[0] || {}).source);
    const cfg = byDisplay(summary.configuration_id, ['ProductConfiguration']) || (job && (outs(job, 'jobConfiguration')[0] || {}).target);
    const customer = round && (outs(round, 'deliverToCustomer')[0] || {}).target;
    const contract = round && (outs(round, 'roundOfContractLine')[0] || {}).target;
    return { round, job, cfg, customer, contract };
  }
  const GD=data;
  const NB=new Map(GD.nodes.map(n=>[n.id,n]));
  const OUT=new Map(), IN=new Map();
  function mpush(m,k,v){let a=m.get(k); if(!a){a=[];m.set(k,a);} a.push(v);}
  GD.edges.forEach(e=>{mpush(OUT,e.source,e); mpush(IN,e.target,e);});
  const outs=(id,p)=>(OUT.get(id)||[]).filter(e=>!p||e.predicate===p);
  const ins=(id,p)=>(IN.get(id)||[]).filter(e=>!p||e.predicate===p);
  const ofType=t=>GD.nodes.filter(n=>n.type===t);
  const A=id=>(NB.get(id)||{}).attributes||{};
  const did=id=>NB.get(id).display_id;
  const short=(s,n)=>{s=String(s==null?'':s); return s.length>n?s.slice(0,n-1)+'…':s;};
  const h8=s=>s?String(s).slice(0,8):'';
  const TYPE_KO={ScenarioRun:'가정 시나리오',ScenarioProfile:'가정 프로파일',ScenarioAssumption:'가정 값',ProductionPlan:'생산 계획',Customer:'고객',ContractLine:'계약 라인',DeliveryRound:'납품 회차',ProductionJob:'작업지시',ProductConfiguration:'제품 구성',BOMSnapshot:'BOM 스냅샷',BOMLine:'BOM 행',BOMApplication:'BOM 적용',InternalItem:'사내 품목',PartLot:'재고 LOT',SupplierOffer:'견적',Supplier:'공급사',SourceDocument:'원본 문서',SourceSnapshot:'원본 스냅샷',SourceRow:'원문 행',DocumentVersion:'문서 버전',ApprovalGrant:'승인 기록',ReservationEvent:'예약 사건',PurchaseRequest:'구매 요청',PurchaseOrder:'발주',PurchaseOrderLine:'발주 행',PartsReceiptLine:'입하 행',InspectionResult:'수입검사',Evidence:'근거'};
  const ORIGIN_KO={reviewed_application:'검토된 적용 판단',synthetic_operation:'합성 운영 데이터',actual_source:'실제 원본',development_workflow:'개발 워크플로',synthetic_assumption:'시연 가정'};
  const ORIGIN_S={reviewed_application:'검토',synthetic_operation:'합성',actual_source:'원본',development_workflow:'워크플로',synthetic_assumption:'가정'};
  const PRED_KO={roundOfContractLine:'계약 라인의 회차',jobForRound:'회차 생산',jobConfiguration:'작업 구성',deliverToCustomer:'납품 고객',lotOf:'LOT 품목',offersItem:'견적 품목',offerFromSupplier:'견적 공급사',snapshotOfDocument:'문서 스냅샷',parsedFromSnapshot:'파싱 원본',hasBOMSnapshot:'BOM 스냅샷',approvalForDocumentVersion:'버전 승인',lineHasApplication:'행 적용',appliesToConfiguration:'적용 구성',applicationMapsItem:'적용 품목',rowInSnapshot:'스냅샷 소속',hasBOMLine:'BOM 행',lineSourceRow:'원문 행',reservationEventLot:'예약 LOT',recordedByDocumentVersion:'문서 버전',supportedBy:'근거',requestSelectsOffer:'선택 견적',hasPOLine:'발주 행',fulfillsRequest:'요청 이행',orderLineItem:'발주 품목',receiptAgainstPOLine:'입하 대상',inspectionOfReceiptLine:'검사 대상',lotAcceptedBy:'LOT 합격',acceptedReceiptLine:'합격 입하'};
  const CAT={ScenarioRun:'dem',ScenarioProfile:'dem',ScenarioAssumption:'dem',ProductionPlan:'dem',Customer:'dem',ContractLine:'dem',DeliveryRound:'dem',ProductionJob:'dem',ProductConfiguration:'bom',BOMSnapshot:'bom',BOMLine:'bom',BOMApplication:'bom',InternalItem:'inv',PartLot:'inv',SupplierOffer:'pro',Supplier:'pro',SourceDocument:'ev',SourceSnapshot:'ev',SourceRow:'ev',DocumentVersion:'ev',ApprovalGrant:'ev',ReservationEvent:'inv',PurchaseRequest:'pro',PurchaseOrder:'pro',PurchaseOrderLine:'pro',PartsReceiptLine:'inv',InspectionResult:'inv',Evidence:'ev'};
  const DOCS=new Set(['SourceDocument','SourceSnapshot','DocumentVersion','ApprovalGrant']);
  const META=GD.meta||{};
  
  /* ---- 버전 인덱스 (배치용. DocumentVersion·ApprovalGrant는 bom_id 속성이 같은 줄에 놓을 뿐 관계를 만들지 않음) ---- */
  const LV=id=>(NB.get(id).evidence||{}).ledger_version??A(id).bom_version;
  const VER=ofType('BOMSnapshot').sort((a,b)=>LV(a.id)-LV(b.id)).map(b=>{
    const ss=(outs(b.id,'parsedFromSnapshot')[0]||{}).target||null;
    const sd=ss?((outs(ss,'snapshotOfDocument')[0]||{}).target||null):null;
    const dvn=ofType('DocumentVersion').find(d=>A(d.id).bom_id===A(b.id).bom_id&&A(d.id).version===A(b.id).bom_version);
    const dv=dvn?dvn.id:null;
    const ag=dv?((ins(dv,'approvalForDocumentVersion')[0]||{}).source||null):null;
    return {b:b.id,v:LV(b.id),docv:A(b.id).bom_version,bomId:A(b.id).bom_id,ss,sd,dv,ag,lines:outs(b.id,'hasBOMLine').map(e=>e.target),rows:ss?ins(ss,'rowInSnapshot').map(e=>e.source):[]};
  });
  const LINE_V=new Map(),ROW_V=new Map(),LINE_SRC=new Map(),SRC_LINE=new Map(),LINE_APP=new Map(),APP_LINE=new Map();
  VER.forEach((V,i)=>{
    V.lines.forEach(l=>{LINE_V.set(l,i); const s=outs(l,'lineSourceRow')[0]; if(s){LINE_SRC.set(l,s.target); SRC_LINE.set(s.target,l);} const a=outs(l,'lineHasApplication')[0]; if(a){LINE_APP.set(l,a.target); APP_LINE.set(a.target,l);}});
    V.rows.forEach(r=>ROW_V.set(r,i));
  });
  function ordOf(id){const n=NB.get(id); if(!n) return 0; if(n.type==='SourceRow') return A(id).ordinal; const s=LINE_SRC.get(id); return s?A(s).ordinal:0;}
  VER.forEach(V=>{V.lines.sort((a,b)=>ordOf(a)-ordOf(b)); V.rows.sort((a,b)=>ordOf(a)-ordOf(b));});
  const ITEM_ORDER=itemOrder.slice();
  const APPS_ALL=ofType('BOMApplication').map(a=>({app:a.id,line:APP_LINE.get(a.id),item:(outs(a.id,'applicationMapsItem')[0]||{}).target})).sort((x,y)=>{const f=v=>{const i=v?ITEM_ORDER.indexOf(did(v)):-1; return i<0?99:i;}; return f(x.item)-f(y.item);});
  // The business lane shows the active BOM's applications; older versions stay in the history lane.
  const activeLine=l=>!activeBom||(LINE_V.has(l)&&VER[LINE_V.get(l)].bomId===activeBom.bom_id&&VER[LINE_V.get(l)].docv===activeBom.bom_version);
  const APPS=APPS_ALL.filter(a=>!a.line||activeLine(a.line));
  const ACTIVE_APP=new Set(APPS.map(a=>a.app));
  const desig=id=>(A(id).designators||[]).join(' ');
  const verKey=l=>(A(l).designators||[]).slice().sort().join(' ');
  const VKEY=VER.map(V=>{const m=new Map(); V.lines.forEach(l=>m.set(verKey(l),A(l).source_description)); return m;});
  function verLabel(i){const V=VER[i]; return 'v'+V.v+(V.sd?' · '+NB.get(V.sd).label:'');}
  
  /* ---- 이행 묶음 (흐름 레인 표시용): 생산 실적별 보드·장착 기록. 노드·관계는 그대로 ---- */
  const PRODS=ofType('ProductionReport').map(n=>n.id);
  const BOARD_P=new Map(), INST_P=new Map();
  PRODS.forEach((p,i)=>outs(p,'reportedBoard').forEach(e=>BOARD_P.set(e.target,i)));
  ofType('InstallationEvent').forEach(n=>{const b=(outs(n.id,'installedOn')[0]||{}).target; if(b&&BOARD_P.has(b)) INST_P.set(n.id,BOARD_P.get(b));});
  /* ---- 화면 상태 ---- */
  const PIN=new Set();
  const LAY={rows:true,docs:true,labels:false};
  const G={hitN:new Set(),hitE:new Set(),sel:null,focus:null,vb:{x:0,y:0,w:1880,h:1000},anim:null,seen:new Set(),newIds:new Map(),init:false,fitted:false,view:null};
  const svg=$('svg');
  const pk=(a,b)=>a<b?a+'|'+b:b+'|'+a;
  
  function slot(id){const n=NB.get(id); if(!n) return null; const t=n.type;
    if(DOCS.has(t)) return LAY.docs?id:null;
    if(t==='BOMApplication') return (ACTIVE_APP.has(id)||MODE!=='lane')?id:null;
    if(t==='BOMLine'){if((LINE_APP.has(id)&&(activeLine(id)||MODE!=='lane'))||PIN.has(id)) return id; return LAY.rows?'gL:'+LINE_V.get(id):(MODE!=='lane'?id:null);}
    if(t==='SourceRow'){if(PIN.has(id)) return id; return LAY.rows?'gR:'+ROW_V.get(id):(MODE!=='lane'?id:null);}
    // Many boards and install records fold per production record in the lane view only.
    if(MODE==='lane'&&!PIN.has(id)){if(t==='BoardUnit'&&BOARD_P.has(id)) return 'gB:'+BOARD_P.get(id); if(t==='InstallationEvent'&&INST_P.has(id)) return 'gI:'+INST_P.get(id);}
    return id;}
  function ensureVisible(id){const n=NB.get(id); if(!n) return; if((n.type==='BOMLine'||n.type==='SourceRow'||n.type==='BoardUnit'||n.type==='InstallationEvent')&&slot(id)!==id) PIN.add(id); if(DOCS.has(n.type)&&!LAY.docs){LAY.docs=true; syncLayerBtns();}}
  
  /* ---- 배치 계산 ---- */
  const COLA=[[90,'고객 · 계약'],[335,'납품 회차'],[580,'생산'],[800,'제품 구성'],[1040,'BOM 행 → 적용'],[1280,'사내 품목'],[1520,'재고 · 견적'],[1740,'공급사']];
  const COLB=[[330,'승인'],[575,'문서 버전'],[800,'BOM 스냅샷'],[1040,'BOM 행'],[1280,'원문 행'],[1520,'원본 스냅샷'],[1740,'원본 문서']];
  function boxSize(n){const t=n.type; if(t==='DeliveryRound') return [212,112]; if(t==='ProductConfiguration') return [168,66]; if(t==='BOMLine'||t==='SourceRow') return [168,34]; if(t==='BOMApplication') return [168,38]; return [168,56];}
  function buildView(){return MODE==='lane'?buildLane():buildNet();}
  function buildLane(){
    const OY=50, P=new Map(), laneA=new Set();
    const put=(key,x,y,w,h,ex)=>P.set(key,Object.assign({key,x,y,w,h},ex||{}));
    const putN=(id,x,y)=>{const [w,h]=boxSize(NB.get(id)); put(id,x,y,w,h); laneA.add(id);};
    const byDid=d=>GD.nodes.find(n=>n.display_id===d&&n.type!=='BOMLine'&&n.type!=='SourceRow');
    const chain=chainIds(); [[chain.customer,90,150],[chain.contract,90,330],[chain.round,335,240],[chain.job,580,240],[chain.cfg,800,240]].forEach(([id,x,y])=>{if(id&&NB.has(id)) putN(id,x,y+OY);});
    APPS.forEach((a,i)=>{const c=90+i*90+OY; if(a.line) putN(a.line,1040,c-21); putN(a.app,1040,c+19); if(a.item&&!P.has(a.item)) putN(a.item,1280,c+19);});
    let ry=30;
    ofType('PartLot').forEach(l=>{if(!P.has(l.id)){putN(l.id,1520,ry+OY); ry+=77;} ins(l.id,'reservationEventLot').forEach(e=>{if(!P.has(e.source)){putN(e.source,1520,ry+OY); ry+=77;}});});
    ofType('SupplierOffer').forEach(o=>{if(P.has(o.id)) return; putN(o.id,1520,ry+OY); const s=(outs(o.id,'offerFromSupplier')[0]||{}).target; if(s&&!P.has(s)) putN(s,1740,ry+OY); ry+=77;});
    // 이행 띠: 생산 실적마다 한 줄(실적 → 보드 묶음 → 장착 묶음 → 소비), 그 아래 입고 → 배정 → 출하 → 도착.
    if(PRODS.length){
      let fy=Math.max(470,90+APPS.length*90+OY+40);
      const consumptionOf=p=>{for(const e of outs(p,'reportedBoard')){const inst=(ins(e.target,'installedOn')[0]||{}).source; const c=inst&&(outs(inst,'supportedByConsumption')[0]||{}).target; if(c) return c;} return null;};
      PRODS.forEach((p,i)=>{
        const boards=GD.nodes.filter(n=>n.type==='BoardUnit'&&BOARD_P.get(n.id)===i&&!PIN.has(n.id)).map(n=>n.id);
        const installs=GD.nodes.filter(n=>n.type==='InstallationEvent'&&INST_P.get(n.id)===i&&!PIN.has(n.id)).map(n=>n.id);
        if(!P.has(p)) putN(p,580,fy);
        if(boards.length) put('gB:'+i,800,fy,168,56,{group:'B',pi:i,members:boards});
        if(installs.length) put('gI:'+i,1040,fy,168,56,{group:'I',pi:i,members:installs});
        const c=consumptionOf(p); if(c&&!P.has(c)) putN(c,1280,fy);
        const all=new Set(outs(p,'reportedBoard').map(e=>e.target));
        const fgs=ins(p,'fgReceiptFromReport').map(e=>e.source);
        const ships=ofType('Shipment').filter(sh=>outs(sh.id,'shipsBoard').some(e=>all.has(e.target))).map(sh=>sh.id);
        const arr=ofType('ArrivalRecord').filter(ar=>outs(ar.id,'arrivedBoard').some(e=>all.has(e.target))).map(ar=>ar.id);
        const allocs=fgs.flatMap(f=>ins(f,'allocationFromReceipt').map(e=>e.source));
        const row=[[fgs,580],[allocs,800],[ships,1040],[arr,1280]];
        const depth=Math.max(0,...row.map(([ids])=>ids.length));
        for(let k=0;k<depth;k++) row.forEach(([ids,x])=>{const id=ids[k]; if(id&&!P.has(id)) putN(id,x,fy+78*(k+1));});
        fy+=78*(depth+1)+24;
      });
    }
    let oy=450;
    GD.nodes.forEach(n=>{if(P.has(n.id)||slot(n.id)!==n.id||DOCS.has(n.type)||n.type==='BOMLine'||n.type==='SourceRow'||n.type==='BOMSnapshot'||n.type==='BOMApplication'||n.type==='Evidence') return; putN(n.id,1740,oy+OY); oy+=78;});
    let aBottom=0; P.forEach(b=>{aBottom=Math.max(aBottom,b.y+b.h/2);});
    const lbTop=Math.max(600,aBottom+52);
    let top=lbTop+34; const bands=[];
    VER.forEach((V,i)=>{
      const pl=V.lines.filter(l=>PIN.has(l)&&!(LINE_APP.has(l)&&activeLine(l))), pr=V.rows.filter(r=>PIN.has(r));
      const fl=V.lines.filter(l=>!(LINE_APP.has(l)&&activeLine(l))&&!PIN.has(l)), fr=V.rows.filter(r=>!PIN.has(r));
      const gL=LAY.rows&&fl.length>0, gR=LAY.rows&&fr.length>0;
      const H=96+Math.max(pl.length,pr.length)*40, yc=top+48;
      bands.push({i,top,H});
      put(V.b,800,yc,168,56);
      if(LAY.docs){if(V.ag) put(V.ag,330,yc,168,56); if(V.dv) put(V.dv,575,yc,168,56); if(V.ss) put(V.ss,1520,yc,168,56); if(V.sd) put(V.sd,1740,yc,168,56);}
      if(gL) put('gL:'+i,1040,yc,168,56,{group:'L',vi:i,members:fl});
      if(gR) put('gR:'+i,1280,yc,168,56,{group:'R',vi:i,members:fr});
      pl.forEach((l,k)=>put(l,1040,yc+52+k*40,168,34));
      pr.forEach((r,k)=>put(r,1280,yc+52+k*40,168,34));
      top+=H+10;
    });
    const E=new Map();
    GD.edges.forEach(e=>{const a=slot(e.source), b=slot(e.target); if(!a||!b||a===b||!P.has(a)||!P.has(b)) return;
      const k=a+'|'+b+'|'+e.predicate; let x=E.get(k); if(!x){x={a,b,p:e.predicate,n:0}; E.set(k,x);} x.n++;});
    E.forEach(x=>{const la=laneA.has(x.a), lb=laneA.has(x.b), g=x.a[0]==='g'&&x.a[2]===':'||x.b[0]==='g'&&x.b[2]===':';
      x.tier=la!==lb?(g?'xh':(x.p==='hasBOMSnapshot'?'core':'x')):(la?'core':'ev');});
    return {P,E:[...E.values()],bands,lbTop,laneA,H:top+6};
  }
  
  /* ---- 선 경로 ---- */
  function orth(pts,r){let d=`M${pts[0][0]},${pts[0][1]}`; for(let i=1;i<pts.length-1;i++){const [x0,y0]=pts[i-1],[x1,y1]=pts[i],[x2,y2]=pts[i+1];
      const r1=Math.min(r,Math.hypot(x1-x0,y1-y0)/2,Math.hypot(x2-x1,y2-y1)/2); const ux=Math.sign(x1-x0),uy=Math.sign(y1-y0),vx=Math.sign(x2-x1),vy=Math.sign(y2-y1);
      d+=` L${x1-ux*r1},${y1-uy*r1} Q${x1},${y1} ${x1+vx*r1},${y1+vy*r1}`;}
    const L=pts[pts.length-1]; return d+` L${L[0]},${L[1]}`;}
  function edgeRoute(e,S,T,V){
    const sl=S.x-S.w/2, sr=S.x+S.w/2, tl=T.x-T.w/2, tr=T.x+T.w/2;
    const cross=V.laneA.has(e.a)!==V.laneA.has(e.b);
    if(cross){
      if(Math.abs(S.x-T.x)<4){const bx=sl-16; return {d:orth([[sl,S.y+S.h/4],[bx,S.y+S.h/4],[bx,T.y],[tl,T.y]],8),lx:bx-4,ly:(S.y+T.y)/2,anc:'end'};}
      if(T.x>S.x){const bx=sr+Math.min(36,(tl-sr)/2); return {d:orth([[sr,S.y],[bx,S.y],[bx,T.y],[tl,T.y]],8),lx:bx+4,ly:(S.y+T.y)/2,anc:'start'};}
      const bx=sl-Math.min(36,(sl-tr)/2); return {d:orth([[sl,S.y],[bx,S.y],[bx,T.y],[tr,T.y]],8),lx:bx-4,ly:(S.y+T.y)/2,anc:'end'};
    }
    if(e.p==='parsedFromSnapshot'&&Math.abs(S.y-T.y)<2){const y0=S.y-S.h/2, lift=20; return {d:`M${S.x+30},${y0} C${S.x+30},${y0-lift} ${T.x-30},${y0-lift} ${T.x-30},${y0}`,lx:(S.x+T.x)/2,ly:y0-lift*0.75+12,anc:'middle'};}
    const dx=T.x-S.x;
    if(Math.abs(dx)>(S.w+T.w)/2*0.9){const s=Math.sign(dx), sx=S.x+s*S.w/2, tx=T.x-s*T.w/2, c=Math.max(24,Math.abs(tx-sx)/2);
      return {d:`M${sx},${S.y} C${sx+s*c},${S.y} ${tx-s*c},${T.y} ${tx},${T.y}`,lx:(sx+tx)/2,ly:(S.y+T.y)/2,anc:'middle'};}
    const s=Math.sign(T.y-S.y)||1, sy=S.y+s*S.h/2, ty=T.y-s*T.h/2;
    return {d:`M${S.x},${sy} L${T.x},${ty}`,lx:S.x+6,ly:(sy+ty)/2+3,anc:'start'};
  }
  
  /* ---- 노드 그리기 ---- */
  function chipSVG(x,y,t,c){const w=t.length*6+10; return `<g class="nch ${c}" transform="translate(${x},${y})"><rect width="${w}" height="15" rx="4"/><text x="5" y="11">${esc(t)}</text></g>`;}
  function nodeSVG(b){
    const cls=['nd'], k=b.key; let kd='',og='',tt='',sb='',body='',cat='ev';
    if(b.group==='B'||b.group==='I'){cls.push('grp'); cat='dem'; const pr=NB.get(PRODS[b.pi]);
      kd=b.group==='B'?'보드 묶음':'장착 기록 묶음'; tt=`${b.members.length}${b.group==='B'?'대':'건'}`; sb=`${pr?pr.display_id:'생산 실적'} · 눌러서 목록`; og='원장';
    } else if(b.group){const V=VER[b.vi]; cls.push('grp');
      if(b.group==='L'){cat='bom'; const u=b.members.filter(l=>((A(l).decision||{}).application_status)==='UNRESOLVED').length; kd=`BOM 행 묶음 · v${V.v}`; tt=`${b.members.length}행`; sb=`UNRESOLVED ${u} · 눌러서 행 탐색`;}
      else {cat='ev'; const rs=b.members.map(r=>A(r).source_row).filter(x=>x!=null); kd=`원문 행 묶음 · v${V.v}`; tt=`${b.members.length}행`; sb=rs.length?`시트 row ${Math.min(...rs)}–${Math.max(...rs)}`:'';}
      og='원본';
    } else {
      const n=NB.get(k), a=n.attributes||{}; cat=CAT[n.type]||'ev'; kd=TYPE_KO[n.type]||n.type; og=ORIGIN_S[n.origin]||n.origin||''; tt=n.display_id;
      if(n.origin==='synthetic_assumption') cls.push('tent');
      switch(n.type){
        case 'Customer': sb=`납품 회차 ${ins(k,'deliverToCustomer').length}건 연결`; break;
        case 'ContractLine': sb=`약속 ${a.promised_qty} ${a.unit||''}`; break;
        case 'ProductionJob': sb=`configuration_id ${a.configuration_id||''}`; break;
        case 'ProductConfiguration': cls.push('st-warn'); body=chipSVG(12,42,a.registration_status||'','w')+chipSVG(14+((a.registration_status||'').length*6+10),42,a.technical_review_status||'','m'); break;
        case 'DeliveryRound':{const segs=[[a.arrived_qty,'var(--v1)','도착'],[a.in_transit_qty,'var(--v2)','운송 중'],[a.warehouse_qty,'var(--v3)','창고'],[a.good_not_received_qty,'var(--v4)','입고 대기'],[a.remaining_good_target_qty,'var(--vtrack)','남은 생산']];
          const tot=a.promised_qty||1, W=b.w-24; let x=12;
          body=`<g class="qbar">${segs.map(s=>{const w=(s[0]||0)/tot*W; const r=`<rect x="${x}" y="54" width="${Math.max(0,w-1)}" height="10" rx="2" style="fill:${s[1]}"><title>${s[2]} ${s[0]}</title></rect>`; x+=w; return r;}).join('')}</g>
            <text class="sb" x="12" y="81">약속 ${a.promised_qty} · 도착 ${a.arrived_qty} · 운송 ${a.in_transit_qty} · 창고 ${a.warehouse_qty}</text>
            <text class="sb" x="12" y="96">입고 대기 ${a.good_not_received_qty} · <tspan class="em">남은 생산 ${a.remaining_good_target_qty}</tspan> · 남은 약속 ${a.remaining_promise_qty}</text>`;
          sb=`arrival_due_date ${a.arrival_due_date}`; break;}
        case 'InternalItem': tt=n.display_id; sb=a.display_name?a.display_name:(a.substitute_status?`substitute ${a.substitute_status}`:''); if(a.available_qty!=null) sb+=` · 가용 ${a.available_qty}`; if(a.candidate_on_hand_qty!=null) sb+=` · 후보 재고 ${a.candidate_on_hand_qty}`; break;
        case 'PartLot': sb=`보유 ${a.on_hand_qty} · 타 예약 ${a.reserved_other_qty} · 가용 ${a.available_qty}`; break;
        case 'SupplierOffer': kd=`견적 · ${a.status||''}`; sb=`${fmt(a.unit_price)}원 · MOQ ${a.moq} · 입하 ${(a.assumed_arrival_date||'').slice(5)}`; if(a.supplier_confirmation===false) cls.push('tent'); break;
        case 'Supplier': sb=`견적 ${ins(k,'offerFromSupplier').length}건`; break;
        case 'ReservationEvent': kd='예약 사건 · 원장'; tt=`${a.demand_ref} · ${a.item_id} ${a.quantity}`; sb=`가용 ${a.available_before}→${a.available_after} · 타 예약 ${a.reserved_other_before}→${a.reserved_other_after}`; cls.push('new-ev'); break;
        case 'BOMSnapshot': kd=`BOM 스냅샷 · v${a.bom_version}`; sb=`${a.row_count}행 · ${a.status}`; break;
        case 'BOMLine': kd=`BOM 행 #${ordOf(k)} · v${VER[LINE_V.get(k)].v}`; tt=short(desig(k)+' · '+(a.source_description||''),22); break;
        case 'BOMApplication':{const it=(outs(k,'applicationMapsItem')[0]||{}).target; kd='BOM 적용'; tt=short(`${a.quantity_per_unit} ${a.unit}/대 → ${it?did(it):'?'}`,22); break;}
        case 'SourceRow': kd=`원문 행 · 시트 row ${a.source_row} · v${VER[ROW_V.get(k)].v}`; tt=short((a.cells||[]).join(' | '),22); break;
        case 'SourceSnapshot': tt=h8(a.sha256)+'…'; sb=`${a.format} · ${fmt(a.bytes)} B · ${a.parser_version||''}`; break;
        case 'SourceDocument': sb=`sha256 ${h8(a.source_sha256)}…`; break;
        case 'DocumentVersion': sb=`version ${a.version} · ${h8(a.payload_hash)}…`; break;
        case 'ApprovalGrant': tt=`승인 v${a.approved_version}`; sb=`${a.created_by} → ${a.approved_by}`; break;
      }
    }
    cls.push('cat-'+cat); if(G.hitN.has(k)) cls.push('hit'); if(G.sel===k) cls.push('sel'); if(PIN.has(k)) cls.push('pin');
    const sm=b.h<40, ky=sm?12:15, ty=sm?27:33;
    const stk=b.group?`<rect class="stk" x="7" y="7" width="${b.w}" height="${b.h}" rx="7"/><rect class="stk" x="3.5" y="3.5" width="${b.w}" height="${b.h}" rx="7"/>`:'';
    return `<g class="${cls.join(' ')}" data-k="${esc(k)}" transform="translate(${b.x-b.w/2},${b.y-b.h/2})" tabindex="0" role="button" aria-label="${esc(kd+' '+tt)}">${stk}<rect class="bg" width="${b.w}" height="${b.h}" rx="7"/><rect class="stripe" width="4" height="${b.h}" rx="2"/>`
      +`<text class="kd" x="12" y="${ky}">${esc(short(kd,26))}</text>${og?`<text class="og" x="${b.w-8}" y="${ky}" text-anchor="end">${esc(og)}</text>`:''}`
      +`<text class="tt" x="12" y="${ty}">${esc(short(tt,22))}</text>${sb&&!sm?`<text class="sb" x="12" y="48">${esc(short(sb,30))}</text>`:''}${body}</g>`;
  }
  
  /* ---- 렌더 ---- */
  function renderGraph(){
    const V=buildView(); G.view=V; const {P,E,bands}=V;
    if(G.focus&&!P.has(G.focus)) G.focus=null;
    if(G.sel&&!P.has(G.sel)) G.sel=null;
    if(G.focus){G.hitN=new Set([G.focus]); G.hitE=new Set(); E.forEach(e=>{if(e.a===G.focus||e.b===G.focus){G.hitN.add(e.a); G.hitN.add(e.b); G.hitE.add(pk(e.a,e.b));}});}
    let h;
    if(V.net){h=renderNet(V);} else {
    h=`<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse"><path class="ahp" d="M0,1L9,5L0,9z"/></marker><marker id="ahh" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse"><path class="ahh" d="M0,1L9,5L0,9z"/></marker></defs>`;
    h+=`<g class="lanes"><rect class="laneA" x="6" y="4" width="1868" height="${V.lbTop-28}" rx="14"/><text class="ltitle" x="22" y="24">업무 흐름</text>`;
    COLA.forEach(([x,t])=>h+=`<text class="lh" x="${x}" y="44" text-anchor="middle">${t}</text>`);
    h+=`<text class="ltitle" x="22" y="${V.lbTop-2}">BOM 버전 이력 · 원문 근거</text>`;
    COLB.forEach(([x,t])=>h+=`<text class="lh" x="${x}" y="${V.lbTop+22}" text-anchor="middle">${t}</text>`);
    bands.forEach(b=>{const Vr=VER[b.i], B=NB.get(Vr.b), sd=Vr.sd?NB.get(Vr.sd):null, ss=Vr.ss?A(Vr.ss):{}; const apps=Vr.lines.filter(l=>LINE_APP.has(l)).length;
      const same=VER.filter((o,j)=>j!==b.i&&o.ss&&A(o.ss).sha256===ss.sha256).map(o=>'v'+o.v);
      const reg=(B.evidence||{}).registered_at; const regK=reg?new Date(reg).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'';
      h+=`<g class="band"><rect x="6" y="${b.top}" width="1868" height="${b.H}" rx="12"/><text class="vnum" x="22" y="${b.top+34}">v${Vr.v}</text>
        <text class="vt" x="64" y="${b.top+30}">${esc(sd?sd.label:'')}</text><text class="vs" x="22" y="${b.top+54}">원장 v${Vr.v} · 문서 v${Vr.docv} · sha ${h8(ss.sha256)} · ${Vr.lines.length}행 · 적용 ${apps}행</text>
        <text class="vs" x="22" y="${b.top+70}">${same.length?'원본 SHA '+same.join('·')+'와 같음':'다른 원본'}${regK?' · 등록 '+esc(regK):''}</text></g>`;});
    h+='</g><g class="edges">';
    E.forEach(e=>{const S=P.get(e.a),T=P.get(e.b); const r=edgeRoute(e,S,T,V); const hit=G.hitE.has(pk(e.a,e.b));
      const w=e.n>1?Math.min(6,1.4+Math.log2(e.n)*0.8):null;
      h+=`<g class="eg ${e.tier} ${hit?'hit':''}"><path d="${r.d}" marker-end="url(#${hit?'ahh':'ah'})"${w?` style="stroke-width:${w.toFixed(1)}"`:''}/>`
        +(e.n>1?`<g class="cnt" transform="translate(${r.lx},${r.ly})"><rect x="-17" y="-9" width="34" height="16" rx="8"/><text y="3" text-anchor="middle">×${e.n}</text></g>`:'')
        +`<text class="el" x="${r.lx}" y="${r.ly-(e.n>1?13:5)}" text-anchor="${r.anc}">${esc(PRED_KO[e.p]||e.p)}</text></g>`;});
    h+='</g><g class="nodes">';
    P.forEach(b=>{h+=nodeSVG(b);});
    }
    const iso=GD.nodes.filter(n=>!(OUT.get(n.id)||[]).length&&!(IN.get(n.id)||[]).length&&P.has(n.id));
    iso.forEach(n=>{const b=P.get(n.id); h+=`<text class="isonote" x="${b.x}" y="${b.y+b.h/2+15}" text-anchor="middle">연결된 관계 없음</text>`;});
    h+='</g>';
    svg.innerHTML=h;
    svg.classList.toggle('dimming',G.hitN.size>0);
    svg.classList.toggle('labels',LAY.labels); svg.classList.toggle('net',!!V.net);
    const folded=[...P.values()].filter(b=>b.group).reduce((s,b)=>s+b.members.length,0);
    $('snapinfo').innerHTML=`원본 노드 <b>${GD.nodes.length}</b> · 관계 <b>${GD.edges.length}</b> → 화면 상자 <b>${P.size}</b> · 선 <b>${E.length}</b>${V.derN?` · 파생 클래스 ${V.clsN} · 속성값 ${V.litN}`:''}${folded?` · 묶음 안 ${folded}개`:''}<br>원장 v${META.ledger_version} · 그래프 v${META.projection_version} · 원천 v${META.source_ledger_version}`;
    if(!G.fitted&&svg.getBoundingClientRect().width>0){G.fitted=true; if(svg.getBoundingClientRect().width<700){const c=chainIds(), ap=itemOrder.length?appByItem(itemOrder[0]):null; fitTo([c.round,c.job,c.cfg].filter(Boolean).concat(ap?[ap.app]:[]),0);} else fitAll(0);}
    if(G.sel) showDetail(G.sel); else $('detail').hidden=true;
    if(!$('rowx').hidden) renderRowx();
  }
  
  /* ---- 화면 이동 ---- */
  function applyVB(){svg.setAttribute('viewBox',`${G.vb.x} ${G.vb.y} ${G.vb.w} ${G.vb.h}`);}
  function setVB(t,ms=600){
    cancelAnimationFrame(G.anim); const from={...G.vb}, t0=performance.now();
    if(!ms||matchMedia('(prefers-reduced-motion: reduce)').matches){G.vb={...t}; applyVB(); return;}
    const step=now=>{let k=Math.min(1,(now-t0)/ms); k=k<.5?2*k*k:1-Math.pow(-2*k+2,2)/2;
      G.vb={x:from.x+(t.x-from.x)*k,y:from.y+(t.y-from.y)*k,w:from.w+(t.w-from.w)*k,h:from.h+(t.h-from.h)*k}; applyVB(); if(k<1) G.anim=requestAnimationFrame(step);};
    G.anim=requestAnimationFrame(step);
  }
  function bbox(keys){const P=G.view?G.view.P:new Map(); let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9,n=0;
    keys.forEach(k=>{const b=P.get(k); if(!b) return; n++; x0=Math.min(x0,b.x-b.w/2); x1=Math.max(x1,b.x+b.w/2); y0=Math.min(y0,b.y-b.h/2); y1=Math.max(y1,b.y+b.h/2);});
    return n?{x0,y0,x1,y1}:null;}
  function fitTo(keys,ms){if(!G.view) renderGraph(); const ks=keys.map(k=>resolveKey(k)?slot(resolveKey(k))||k:k); const b=bbox(ks); if(!b) return;
    let w=Math.max(b.x1-b.x0+180,640), h=Math.max(b.y1-b.y0+160,380); let cx=(b.x0+b.x1)/2; const cy=(b.y0+b.y1)/2;
    const r=svg.getBoundingClientRect(); let cw=r.width||1000; const ch=r.height||600;
    const side=[$('detail'),$('rowx')].filter(x=>!x.hidden&&r.width>980).reduce((s,x)=>s+x.offsetWidth+12,0);
    const avail=Math.max(320,cw-side), ar=avail/ch; if(w/h>ar) h=w/ar; else w=h*ar;
    const W=w*cw/avail; const det=$('detail'), rx=$('rowx');
    const shift=((rx.hidden||r.width<=980?0:rx.offsetWidth+12)-(det.hidden||r.width<=980?0:det.offsetWidth+12))/2*W/cw;
    setVB({x:cx-W/2-shift,y:cy-h/2,w:W,h},ms);}
  function fitAll(ms=600){if(!G.view) return; if(G.view.net){fitTo([...G.view.P.keys()],ms); return;} setVB({x:0,y:0,w:1880,h:G.view.H},ms);}
  function svgPt(e){const p=svg.createSVGPoint(); p.x=e.clientX; p.y=e.clientY; const m=svg.getScreenCTM(); return m?p.matrixTransform(m.inverse()):{x:0,y:0};}
  let zT=null;
  function zoomAt(p,k){if(typeof MODE!=='undefined'&&MODE==='rdf'){clearTimeout(zT); zT=setTimeout(renderGraph,180);} cancelAnimationFrame(G.anim); const v=G.vb, w=Math.min(5200,Math.max(260,v.w*k)), kk=w/v.w; G.vb={x:p.x-(p.x-v.x)*kk,y:p.y-(p.y-v.y)*kk,w,h:v.h*kk}; applyVB();}
  on(svg,'wheel',e=>{e.preventDefault(); zoomAt(svgPt(e),Math.exp(e.deltaY*0.0015));},{passive:false});
  let drag=null;
  on(svg,'pointerdown',e=>{if(e.target.closest('.nd')||e.button!==0) return; const m=svg.getScreenCTM(); drag={x:e.clientX,y:e.clientY,vb:{...G.vb},s:m?1/m.a:1,moved:false}; svg.setPointerCapture(e.pointerId);});
  on(svg,'pointermove',e=>{if(!drag) return; const dx=e.clientX-drag.x, dy=e.clientY-drag.y; if(Math.abs(dx)+Math.abs(dy)>4) drag.moved=true; if(drag.moved){cancelAnimationFrame(G.anim); G.vb={...drag.vb,x:drag.vb.x-dx*drag.s,y:drag.vb.y-dy*drag.s}; applyVB(); svg.classList.add('panning');}});
  on(svg,'pointerup',e=>{if(!drag) return; const moved=drag.moved; drag=null; svg.classList.remove('panning'); if(!moved){G.sel=null; clearHits(); renderGraph();}});
  $('zin').onclick=()=>zoomAt({x:G.vb.x+G.vb.w/2,y:G.vb.y+G.vb.h/2},0.78);
  $('zout').onclick=()=>zoomAt({x:G.vb.x+G.vb.w/2,y:G.vb.y+G.vb.h/2},1.28);
  $('fit').onclick=()=>{clearHits(); G.sel=null; renderGraph(); fitAll();};
  
  /* ---- 선택 · 초점 ---- */
  function clearHits(){G.hitN.clear(); G.hitE.clear(); G.focus=null;}
  function resolveKey(x){if(x==null) return null; if(G.view&&G.view.P.has(x)) return x; if(NB.has(x)) return x;
    const n=GD.nodes.find(n=>n.display_id===x&&n.type!=='BOMLine'&&n.type!=='SourceRow'); if(n) return n.id;
    const ap=APPS.find(a=>a.line&&(A(a.line).designators||[]).includes(x)); if(ap) return ap.line;
    const l=VER[0]&&VER[0].lines.find(l=>(A(l).designators||[]).includes(x)); return l||null;}
  function focusAny(x,center){const k=resolveKey(x); if(!k) return; if(NB.has(k)) ensureVisible(k);
    stopTrace(); clearHits(); G.sel=k; G.focus=k; renderGraph(); if(center) fitTo([...G.hitN]); if(NB.has(k)) onSelect(NB.get(k));}
  on(svg,'click',e=>{if(suppressClick){suppressClick=false; return;} const g=e.target.closest('.nd'); if(!g) return; const k=g.dataset.k; if(k.startsWith('gB:')||k.startsWith('gI:')){stopTrace(); clearHits(); G.sel=k; G.focus=k; renderGraph(); showDetail(k); return;} if(k[0]==='g'&&k[2]===':'){openRowx(+k.slice(3)); stopTrace(); clearHits(); G.sel=null; G.focus=k; renderGraph(); return;} focusAny(k,false);});
  on(svg,'keydown',e=>{if(e.key==='Enter'||e.key===' '){const g=e.target.closest('.nd'); if(g){e.preventDefault(); g.dispatchEvent(new MouseEvent('click',{bubbles:true}));}}});
  
  /* ---- 근거 경로 (원본 관계만 따라감) ---- */
  function evChain(id){const n=NB.get(id); if(!n) return []; const H=[];
    const fromRow=r=>{const ss=(outs(r,'rowInSnapshot')[0]||{}).target; if(ss){H.push([r,ss]); const sd=(outs(ss,'snapshotOfDocument')[0]||{}).target; if(sd) H.push([ss,sd]);}};
    const fromLine=l=>{const vi=LINE_V.get(l); if(vi!=null) H.push([VER[vi].b,l]); const r=LINE_SRC.get(l); if(r){H.push([l,r]); fromRow(r);}};
    if(n.type==='BOMApplication'){const l=APP_LINE.get(id); if(l){H.push([l,id]); fromLine(l);}}
    else if(n.type==='BOMLine') fromLine(id);
    else if(n.type==='SourceRow'){const l=SRC_LINE.get(id); if(l) H.push([l,id]); fromRow(id);}
    else if(n.type==='BOMSnapshot'){const V=VER.find(v=>v.b===id); if(V&&V.ss){H.push([id,V.ss]); if(V.sd) H.push([V.ss,V.sd]);}}
    else if(n.type==='InternalItem'){ins(id,'applicationMapsItem').forEach(e=>{H.push([e.source,id]); const l=APP_LINE.get(e.source); if(l){H.push([l,e.source]); fromLine(l);}});}
    return H;}
  function trace(hops,done){
    stopTrace(); clearHits();
    hops.flat().forEach(ensureVisible);
    if(!hops.length){renderGraph(); done&&done(); return;}
    renderGraph(); const s0=slot(hops[0][0]); G.hitN.add(s0); renderGraph(); fitTo([s0,slot(hops[0][1])]);
    const add=hp=>{const a=slot(hp[0]),b=slot(hp[1]); if(a&&b){G.hitE.add(pk(a,b)); G.hitN.add(a); G.hitN.add(b);}};
    if(reduced()){hops.forEach(add); renderGraph(); fitTo([...G.hitN],0); done&&done(); return;}
    const steps=hops.length>10?[...hops.slice(0,8).map(h=>[h]),hops.slice(8)]:hops.map(h=>[h]);
    steps.forEach((group,i)=>{traceTimer.push(setTimeout(()=>{group.forEach(add); renderGraph(); fitTo([...G.hitN]); if(i===steps.length-1) done&&done();},420*(i+1)));});
  }
  
  /* ---- 상세 패널 ---- */
  function fmtV(v){if(v==null) return '<span class="nul">null</span>'; if(Array.isArray(v)) return v.length?esc(v.join(', ')):'<span class="nul">[]</span>';
    if(typeof v==='object') return `<dl class="ikv sub">${Object.entries(v).map(([k,x])=>`<dt>${esc(k)}</dt><dd>${fmtV(x)}</dd>`).join('')}</dl>`;
    const s=String(v); if(/^[0-9a-f]{40,}$/.test(s)) return `<span title="${s}">${s.slice(0,16)}…</span>`; if(v===''||s==='') return '<span class="nul">""</span>'; return esc(s);}
  function kvHTML(o){const e=Object.entries(o||{}); return e.length?`<dl class="ikv">${e.map(([k,v])=>{const f=v&&typeof v==='object'&&!Array.isArray(v)?' class="full"':''; return `<dt${f}>${esc(k)}</dt><dd${f}>${fmtV(v)}</dd>`;}).join('')}</dl>`:'<p class="note">없음</p>';}
  function nlabel(id){const n=NB.get(id); if(!n) return id; if(n.type==='BOMLine') return `#${ordOf(id)} ${short(desig(id),14)} · v${VER[LINE_V.get(id)].v}`; if(n.type==='SourceRow') return `row=${A(id).source_row} · v${VER[ROW_V.get(id)].v}`; return n.display_id;}
  function vOf(id){return LINE_V.has(id)?LINE_V.get(id):ROW_V.has(id)?ROW_V.get(id):VER.findIndex(v=>v.b===id||v.ss===id);}
  function relHTML(id){
    const grp=(list,dir)=>{const m=new Map(); list.forEach(e=>mpush(m,e.predicate,dir==='out'?e.target:e.source));
      return [...m.entries()].map(([p,ids])=>{const show=ids.slice(0,6), more=ids.length-show.length, vi=vOf(ids[0]);
        return `<div class="rg"><div class="rh"><span class="dir">${dir==='out'?'나감 →':'← 들어옴'}</span><b>${esc(PRED_KO[p]||p)}</b><span class="mono pr">${esc(p)}</span><span class="n">${ids.length}</span></div><div class="rc">${show.map(o=>`<button class="chip" type="button" data-node="${esc(o)}" title="${esc(TYPE_KO[NB.get(o).type]||'')}">${esc(nlabel(o))}</button>`).join('')}${more>0?`<button class="chip more" type="button" data-rowx="${vi}">외 ${more}개 · 행 탐색기</button>`:''}</div></div>`;}).join('');};
    const o=OUT.get(id)||[], i=IN.get(id)||[];
    if(!o.length&&!i.length) return '<p class="note">이 객체에는 데이터상 연결된 관계가 없습니다. 계산·답변의 근거로 쓰지 않습니다.</p>';
    return grp(o,'out')+grp(i,'in');
  }
  function showDetail(key){
    const d=$('detail'); if(!key){d.hidden=true; return;}
    const close=`<button class="x" type="button" data-act="close" aria-label="닫기">×</button>`;
    if(typeof DERM!=='undefined'&&DERM.has(key)){d.innerHTML=derDetail(key,close); d.hidden=false; return;}
    if((key.startsWith('gB:')||key.startsWith('gI:'))&&G.view&&G.view.P.get(key)){const b=G.view.P.get(key), pr=NB.get(PRODS[b.pi]);
      d.innerHTML=`<div class="ih"><div><div class="k">${b.group==='B'?'보드 묶음':'장착 기록 묶음'} · 화면 표시용</div><h4>${esc(pr?pr.display_id:'생산 실적')} · ${b.members.length}${b.group==='B'?'대':'건'}</h4></div>${close}</div>
        <p class="note" style="margin-top:4px">같은 생산 실적의 ${b.group==='B'?'BoardUnit':'InstallationEvent'} 노드 ${b.members.length}개를 한 상자로 접었습니다. 노드와 관계는 그대로이며, 선의 ×N은 묶인 관계 수입니다. 항목을 누르면 따로 펼쳐 보입니다.</p>
        <div class="rc" style="padding-left:0">${b.members.slice(0,60).map(o=>`<button class="chip" type="button" data-node="${esc(o)}">${esc(nlabel(o))}</button>`).join('')}${b.members.length>60?`<span class="note">외 ${b.members.length-60}개</span>`:''}</div>`; d.hidden=false; return;}
    if(key[0]==='g'&&key[2]===':'){const b=G.view&&G.view.P.get(key); if(!b){d.hidden=true; return;} const V=VER[b.vi];
      d.innerHTML=`<div class="ih"><div><div class="k">${b.group==='L'?'BOM 행 묶음':'원문 행 묶음'} · 화면 표시용</div><h4>v${V.v} · ${b.members.length}행</h4></div>${close}</div>
        <p class="note" style="margin-top:4px">같은 버전의 ${b.group==='L'?'BOMLine':'SourceRow'} 노드 ${b.members.length}개를 한 상자로 접었습니다. 노드와 관계는 그대로이며, 선의 ×N은 묶인 관계 수입니다.</p>
        <div class="acts"><button class="btn sm pri" type="button" data-rowx="${b.vi}">행 탐색기 열기</button></div>`; d.hidden=false; return;}
    const n=NB.get(key); if(!n){d.hidden=true; return;}
    const a=n.attributes||{}, acts=[];
    if(evChain(key).length) acts.push(`<button class="btn sm pri" type="button" data-act="ev">근거 따라가기</button>`);
    if(PIN.has(key)) acts.push(`<button class="btn sm" type="button" data-act="unpin">고정 해제</button>`);
    if(vOf(key)>=0&&(LINE_V.has(key)||ROW_V.has(key)||n.type==='BOMSnapshot')) acts.push(`<button class="btn sm" type="button" data-rowx="${vOf(key)}">행 탐색기</button>`);
    let sp='';
    if(n.type==='DeliveryRound') sp=`<div class="qbox"><div class="qrow"><span>약속</span><b>${a.promised_qty}</b></div><div class="qrow"><span>도착</span><b>${a.arrived_qty}</b></div><div class="qrow"><span>운송 중 · 창고 · 입고 대기</span><b>${a.in_transit_qty} · ${a.warehouse_qty} · ${a.good_not_received_qty}</b></div><div class="qrow"><span>남은 생산 (remaining_good_target_qty)</span><b>${a.remaining_good_target_qty}</b></div><p class="note">생산·출하·도착은 이 데이터에서 별도 노드가 아니라 회차의 수량 속성입니다.</p></div>`;
    if(n.type==='DocumentVersion'||n.type==='ApprovalGrant') sp=`<p class="note">BOM 스냅샷과 직접 잇는 관계는 없습니다. bom_id 속성이 같은 버전 줄에 함께 배치했습니다.</p>`;
    if(n.type==='BOMApplication'&&a.evidence_note) sp=`<p class="evn">${esc(a.evidence_note)}</p>`;
    d.innerHTML=`<div class="ih"><div><div class="k">${esc(TYPE_KO[n.type]||n.type)} · <span class="mono">${esc(n.type)}</span></div><h4 class="mono">${esc(n.display_id)}</h4>${n.label!==n.display_id?`<div class="lbl">${esc(n.label)}</div>`:''}</div>${close}</div>
      <div class="ogrow"><span class="pill og og-${esc(n.origin)}">${esc(ORIGIN_KO[n.origin]||n.origin)}</span><span class="mono uid" title="${esc(n.id)}">${esc(short(n.id,38))}</span></div>
      ${acts.length?`<div class="acts">${acts.join('')}</div>`:''}${sp}
      <h5>관계</h5>${relHTML(key)}<h5>속성 · attributes</h5>${kvHTML(a)}<h5>근거 · evidence</h5>${kvHTML(n.evidence)}`;
    d.hidden=false;
  }
  $('detail').addEventListener('click',e=>{
    const c=e.target.closest('[data-node]'); if(c){focusAny(c.dataset.node,true); return;}
    const rx=e.target.closest('[data-rowx]'); if(rx){openRowx(+rx.dataset.rowx); return;}
    const b=e.target.closest('[data-act]'); if(!b) return; const a=b.dataset.act;
    if(a==='close'){G.sel=null; clearHits(); renderGraph();}
    if(a==='ev'){const k=G.sel; trace(evChain(k),()=>{G.sel=k; showDetail(k);});}
    if(a==='unpin'){const k=G.sel; PIN.delete(k); G.sel=null; clearHits(); renderGraph();}
  });
  
  /* ---- 레이어 · 검색 ---- */
  function syncLayerBtns(){root.querySelectorAll('[data-layer]').forEach(b=>b.setAttribute('aria-pressed',String(!!LAY[b.dataset.layer])));}
  root.querySelector('.layers:not(.modes)').addEventListener('click',e=>{const b=e.target.closest('[data-layer]'); if(!b) return; LAY[b.dataset.layer]=!LAY[b.dataset.layer]; syncLayerBtns(); renderGraph();});
  const gfind=$('gfind'), gres=$('gres');
  function searchNodes(q){q=q.trim().toLowerCase(); if(!q) return [];
    const sc=[]; GD.nodes.forEach(n=>{const a=n.attributes||{}; const hay=[n.display_id,n.label,a.display_name,(a.designators||[]).join(' '),a.source_description,a.filename].filter(Boolean).join(' ').toLowerCase();
      if(!hay.includes(q)) return; let s=n.display_id.toLowerCase()===q?0:(a.designators||[]).some(x=>x.toLowerCase()===q)?1:2; if(n.type==='SourceRow') s+=0.5; if(LINE_V.has(n.id)) s+=LINE_V.get(n.id)*0.1; sc.push([s,n]);});
    return sc.sort((x,y)=>x[0]-y[0]).slice(0,14).map(x=>x[1]);}
  let gsel=0;
  function renderRes(){const r=searchNodes(gfind.value); if(!gfind.value.trim()){gres.hidden=true; return;}
    gres.innerHTML=r.length?r.map((n,i)=>`<button type="button" data-node="${esc(n.id)}" class="${i===gsel?'on':''}"><span class="t">${esc(TYPE_KO[n.type]||n.type)}${LINE_V.has(n.id)||ROW_V.has(n.id)?' · v'+VER[vOf(n.id)].v:''}</span><span class="mono">${esc(nlabel(n.id))}</span><span class="d">${esc(short((n.attributes||{}).source_description||(n.attributes||{}).display_name||n.label,40))}</span></button>`).join(''):'<div class="none">일치하는 객체가 없습니다</div>';
    gres.hidden=false;}
  gfind.addEventListener('input',()=>{gsel=0; renderRes();});
  gfind.addEventListener('keydown',e=>{const bs=[...gres.querySelectorAll('button')]; if(e.key==='ArrowDown'){gsel=Math.min(bs.length-1,gsel+1); renderRes(); e.preventDefault();} if(e.key==='ArrowUp'){gsel=Math.max(0,gsel-1); renderRes(); e.preventDefault();}
    if(e.key==='Enter'&&bs[gsel]){bs[gsel].click(); e.preventDefault();} if(e.key==='Escape'){gres.hidden=true;}});
  gres.addEventListener('click',e=>{const b=e.target.closest('[data-node]'); if(!b) return; gres.hidden=true; gfind.blur(); focusAny(b.dataset.node,true);});
  on(document,'click',e=>{if(!e.target.closest('.gsearch')) gres.hidden=true;});
  
  /* ---- BOM 행 탐색기 ---- */
  const RX={vi:0,f:'all',q:''};
  function openRowx(vi){if(!(vi>=0)) vi=0; RX.vi=vi; $('rowx').hidden=false; renderRowx();}
  function renderRowx(){
    const el=$('rowx'), V=VER[RX.vi]; if(!V) return;
    const others=VER.map((_,i)=>i).filter(i=>i!==RX.vi);
    const rows=V.lines.map(l=>{const a=A(l), k=verKey(l); return {l,a,k,cmp:others.map(i=>{const d=VKEY[i].get(k); return d==null?'–':d===a.source_description?'=':'≠';}),app:LINE_APP.get(l),src:LINE_SRC.get(l)};});
    const isDiff=r=>r.cmp.some(c=>c!=='=');
    const cnt={all:rows.length,app:rows.filter(r=>r.app).length,diff:rows.filter(isDiff).length};
    let list=rows.filter(r=>RX.f==='all'||(RX.f==='app'&&r.app)||(RX.f==='diff'&&isDiff(r)));
    if(RX.q){const q=RX.q.toLowerCase(); list=list.filter(r=>(r.k+' '+r.a.source_description).toLowerCase().includes(q));}
    const ss=V.ss?A(V.ss):{};
    el.innerHTML=`<div class="rxh"><div><div class="k">BOM 행 탐색기 · ${esc(NB.get(V.b).label)}</div><h4>${esc(verLabel(RX.vi))} <span class="mono" style="font-size:11px;color:var(--fg3);font-weight:500">sha ${h8(ss.sha256)}</span></h4></div><button class="x" type="button" data-rx="close" aria-label="닫기">×</button></div>
      <div class="rxbar"><span class="seg2" role="group" aria-label="버전">${VER.map((W,i)=>`<button type="button" data-rxv="${i}" aria-pressed="${i===RX.vi}">v${W.v}</button>`).join('')}</span>
      <span class="seg2" role="group" aria-label="필터">${[['all','전체'],['app','적용'],['diff','다른 버전과 차이']].map(([k,l])=>`<button type="button" data-rxf="${k}" aria-pressed="${RX.f===k}">${l} ${cnt[k]}</button>`).join('')}</span>
      <input data-lg="rxq" type="search" placeholder="위치·원문 값" value="${esc(RX.q)}" aria-label="행 검색"></div>
      <div class="rxt"><table><thead><tr><th>#</th><th>시트 행</th><th>위치</th><th>원문 값</th><th>수량</th><th>적용</th>${others.map(i=>`<th title="같은 위치 조합의 v${VER[i].v} 원문 값 비교">vs v${VER[i].v}</th>`).join('')}</tr></thead><tbody>
      ${list.map(r=>`<tr data-line="${esc(r.l)}" class="${G.sel===r.l?'on':''} ${PIN.has(r.l)||r.app?'pin':''}" tabindex="0"><td class="n">${ordOf(r.l)}</td><td class="n">${r.src?A(r.src).source_row:''}</td><td class="v">${esc(r.k)}</td><td class="v">${esc(r.a.source_description)}</td><td class="n">${esc(r.a.source_quantity)}</td><td>${r.app?`<span class="pill info">${esc(did((outs(r.app,'applicationMapsItem')[0]||{}).target||r.app))}</span>`:`<span class="pill mute">${esc((r.a.decision||{}).application_status||'')}</span>`}</td>${r.cmp.map(c=>`<td class="cmp ${c==='='?'cs':c==='≠'?'cd':'cm'}">${c}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="${6+others.length}"><div class="empty"><b>조건에 맞는 행이 없습니다</b></div></td></tr>`}</tbody></table></div>
      <div class="note">행을 누르면 그래프에 펼쳐 고정되고, 상세 패널의 “근거 따라가기”로 원문 행 → 원본 스냅샷 → 원본 문서까지 이어서 볼 수 있습니다. 비교 열(= 같음 · ≠ 다름 · – 같은 위치 조합 없음)은 위치 조합으로 대조한 화면 계산이며 데이터의 관계가 아닙니다.</div>`;
  }
  const rowxEl=$('rowx');
  rowxEl.addEventListener('click',e=>{
    if(e.target.closest('[data-rx="close"]')){rowxEl.hidden=true; return;}
    const v=e.target.closest('[data-rxv]'); if(v){RX.vi=+v.dataset.rxv; renderRowx(); return;}
    const f=e.target.closest('[data-rxf]'); if(f){RX.f=f.dataset.rxf; renderRowx(); return;}
    const tr=e.target.closest('[data-line]'); if(tr){focusAny(tr.dataset.line,true);}
  });
  rowxEl.addEventListener('keydown',e=>{if(e.key==='Enter'){const tr=e.target.closest('[data-line]'); if(tr) focusAny(tr.dataset.line,true);}});
  rowxEl.addEventListener('input',e=>{if(e.target.dataset.lg!=='rxq') return; RX.q=e.target.value; const pos=e.target.selectionStart; renderRowx(); const q=$('rxq'); q.focus(); q.setSelectionRange(pos,pos);});
  
  
  /* ================= 네트워크 보기들 (힘 기반 배치 · 같은 노드/관계를 퍼진 형태로) =================
     net  : 원 노드 네트워크
     card : 후보 A — 같은 배치에 카드형 노드(유형·ID·핵심 속성·상태)
     rdf  : 후보 B — 원본 노드 + type에서 만든 클래스 노드 + attributes에서 만든 속성값 노드(파생, 원본 관계 아님) */
  try{const m=localStorage.getItem('ontologics-mks2-graph-mode'); if(MODES.includes(m)) MODE=m;}catch(e){}
  const NETS={};
  function NETm(){return NETS[MODE]||(NETS[MODE]={pos:new Map(),sig:'',first:true});}
  const ROWT=new Set(['BOMLine','SourceRow']);
  const DERM=new Map();
  const isGrp=k=>k[0]==='g'&&k[2]===':';
  function isRowKey(k){const n=NB.get(k); return !!(n&&ROWT.has(n.type));}
  function hashJ(s){let h=2166136261; for(let i=0;i<s.length;i++){h^=s.charCodeAt(i); h=Math.imul(h,16777619);} return ((h>>>0)%1000)/1000;}
  function netR(k,deg){
    const d=DERM.get(k); if(d) return d.kind==='cls'?20:3.4;
    if(isGrp(k)){const g=G._grpN&&G._grpN.get(k)||1; return 13+Math.sqrt(g)*1.5;}
    if(isRowKey(k)) return LINE_APP.has(k)?7:4.5;
    const t=NB.get(k).type; const base={DeliveryRound:15,ProductConfiguration:15,ProductionJob:12,InternalItem:11,BOMSnapshot:12,ContractLine:11,Customer:11}[t]||9; return base+Math.min(5,(deg||0)*0.15);}
  function cardSize(k){if(isGrp(k)) return [168,56]; const n=NB.get(k); return n?boxSize(n):[120,30];}
  function netSeeds(){const save={...LAY}; const m=MODE; MODE='lane'; LAY.rows=true; LAY.docs=true; const L=buildLane(); MODE=m; Object.assign(LAY,save); return L.P;}
  function netSim(keys,E){
    const NET=NETm(), card=MODE==='card', rdf=MODE==='rdf', SC=card?1.6:1;
    const sig=keys.length+':'+hashJ(keys.join('|'))+':'+E.length;
    if(sig===NET.sig) return; NET.sig=sig;
    const seeds=netSeeds(), deg=new Map(); keys.forEach(k=>deg.set(k,0)); E.forEach(e=>{deg.set(e.a,(deg.get(e.a)||0)+1); deg.set(e.b,(deg.get(e.b)||0)+1);});
    let added=0; const ks=new Set(keys);
    const seedOf=k=>{const s=seeds.get(k); return s?{x:(s.x-940)*SC+940,y:(s.y-520)*SC+520}:null;};
    keys.forEach(k=>{if(NET.pos.has(k)) return; added++; let s=seedOf(k); const d=DERM.get(k);
      if(!s&&d&&d.kind==='lit'){const o=NET.pos.get(d.owner)||seedOf(d.owner); s=o||{x:940,y:520};}
      if(!s&&d&&d.kind==='cls'){s={x:940+(hashJ(k)-.5)*900,y:520+(hashJ(k+'!')-.5)*700};}
      if(!s&&isRowKey(k)){const vi=LINE_V.has(k)?LINE_V.get(k):ROW_V.get(k); const gk=(LINE_V.has(k)?'gL:':'gR:')+vi; s=NET.pos.get(gk)||seedOf(gk)||{x:1100,y:800};}
      if(!s) s={x:940,y:520}; const j=hashJ(k), j2=hashJ(k+'#'), sp=d&&d.kind==='lit'?40:60;
      NET.pos.set(k,{x:s.x+(j-.5)*sp,y:s.y+(j2-.5)*sp,vx:0,vy:0});});
    [...NET.pos.keys()].forEach(k=>{if(!ks.has(k)) NET.pos.delete(k);});
    const N=keys.map(k=>{const p=NET.pos.get(k), d=DERM.get(k); p.k=k; p.r=netR(k,deg.get(k));
      if(card){const [w,h]=cardSize(k); p.hw=w/2; p.hh=h/2;}
      p.s=d?(d.kind==='lit'?-120:-900):isRowKey(k)?(card?-120:-50):isGrp(k)?(card?-1300:-900):(card?-1400:-800); return p;});
    const idx=new Map(N.map((p,i)=>[p.k,i]));
    const L=E.map(e=>{const a=idx.get(e.a), b=idx.get(e.b), rowish=isRowKey(e.a)||isRowKey(e.b), ta=(NB.get(e.a)||{}).type, tb=(NB.get(e.b)||{}).type;
      let len=rowish?(card?95:34):(e.p==='lineHasApplication'?(card?70:40):(DOCS.has(ta)||DOCS.has(tb)?(card?180:100):(card?200:135)));
      if(e.tier==='der') len=e.p==='rdf:type'?(isRowKey(e.a)?60:200):92;
      const da=deg.get(e.a)||1, db=deg.get(e.b)||1; return {a,b,len,str:(e.tier==='der'&&e.p==='rdf:type'?0.35:1)/Math.min(da,db),bias:da/(da+db)};});
    const iters=NET.first?(rdf?360:320):(added?160:0); if(!iters) return;
    let alpha=NET.first?1:Math.min(1,0.25+added/80); const decay=1-Math.pow(0.001,1/iters), RANGE=card?1e6:300000;
    for(let it=0;it<iters;it++){
      for(const l of L){const A_=N[l.a],B_=N[l.b]; let x=B_.x+B_.vx-A_.x-A_.vx, y=B_.y+B_.vy-A_.y-A_.vy; let d=Math.sqrt(x*x+y*y)||1e-3; const f=(d-l.len)/d*alpha*l.str; x*=f; y*=f; B_.vx-=x*l.bias; B_.vy-=y*l.bias; A_.vx+=x*(1-l.bias); A_.vy+=y*(1-l.bias);}
      for(let i=0;i<N.length;i++){const p=N[i]; for(let j=i+1;j<N.length;j++){const q=N[j]; let x=q.x-p.x, y=q.y-p.y; let l2=x*x+y*y; if(l2<1){x=(hashJ(p.k+q.k)-.5); y=.5; l2=1;}
          if(l2<RANGE){const w=alpha/l2; p.vx+=x*q.s*w; p.vy+=y*q.s*w; q.vx-=x*p.s*w; q.vy-=y*p.s*w;}
          if(card){const ox=p.hw+q.hw+14-Math.abs(x), oy=p.hh+q.hh+12-Math.abs(y); if(ox>0&&oy>0){if(ox/(p.hw+q.hw)<oy/(p.hh+q.hh)){const m=Math.sign(x)||1; p.vx-=m*ox*0.25; q.vx+=m*ox*0.25;} else {const m=Math.sign(y)||1; p.vy-=m*oy*0.25; q.vy+=m*oy*0.25;}}}
          else {const rr=p.r+q.r+(rdf?3:6); if(l2<rr*rr){const d=Math.sqrt(l2), o=(rr-d)/d*0.5; p.vx-=x*o*0.7; p.vy-=y*o*0.7; q.vx+=x*o*0.7; q.vy+=y*o*0.7;}}}}
      let cx=0,cy=0; N.forEach(p=>{cx+=p.x; cy+=p.y;}); cx=cx/N.length-940; cy=cy/N.length-520;
      N.forEach(p=>{p.vx-=(p.x-940)*0.004*alpha; p.vy-=(p.y-520)*0.004*alpha; if(p.fx){p.vx=0;p.vy=0;return;} p.vx*=0.6; p.vy*=0.6; p.x+=p.vx-cx*0.1; p.y+=p.vy-cy*0.1;});
      alpha*=1-decay;
    }
    NET.first=false;
  }
  /* 후보 B: 파생 노드 — 클래스(type)와 속성값(attributes). 원본 노드·관계는 그대로 두고 화면에서만 추가 */
  function litText(v){if(v==null) return 'null'; if(Array.isArray(v)) return v.join(', ')||'[]'; const s=String(v); if(/^[0-9a-f]{40,}$/.test(s)) return s.slice(0,10)+'…'; return s===''?'""':s;}
  function flatAttrs(a){const out=[]; Object.entries(a||{}).forEach(([k,v])=>{if(v&&typeof v==='object'&&!Array.isArray(v)) Object.entries(v).forEach(([k2,v2])=>out.push([k+'.'+k2,v2])); else out.push([k,v]);}); return out;}
  function addDerived(keys,EL){
    DERM.clear(); const add=[], ed=[];
    const clsCount=new Map();
    keys.forEach(k=>{let t, n=1; if(isGrp(k)){t=k[1]==='L'?'BOMLine':'SourceRow'; n=(G._grpN.get(k)||1);} else t=NB.get(k).type;
      const ck='cls:'+t; if(!DERM.has(ck)){DERM.set(ck,{kind:'cls',type:t,inst:[]}); add.push(ck);} const d=DERM.get(ck); d.inst.push(k); clsCount.set(ck,(clsCount.get(ck)||0)+n);
      ed.push({a:k,b:ck,p:'rdf:type',n,tier:'der'});
      const node=NB.get(k); if(!node) return; if(ROWT.has(node.type)&&!LINE_APP.has(k)&&!PIN.has(k)) return;
      flatAttrs(node.attributes).forEach(([an,av])=>{const lk='lit:'+k+'|'+an; DERM.set(lk,{kind:'lit',owner:k,attr:an,value:av}); add.push(lk); ed.push({a:k,b:lk,p:an,n:1,tier:'der'});});
    });
    DERM.forEach((d,k)=>{if(d.kind==='cls') d.total=clsCount.get(k);});
    return {add,ed};
  }
  function buildNet(){
    const keys=new Set(); GD.nodes.forEach(n=>{const s=slot(n.id); if(s) keys.add(s);});
    const grpN=new Map(), members=new Map(); GD.nodes.forEach(n=>{const s=slot(n.id); if(s&&s!==n.id){grpN.set(s,(grpN.get(s)||0)+1); mpush(members,s,n.id);}}); G._grpN=grpN;
    const E=new Map();
    GD.edges.forEach(e=>{const a=slot(e.source), b=slot(e.target); if(!a||!b||a===b) return; const k=a+'|'+b+'|'+e.predicate; let x=E.get(k); if(!x){x={a,b,p:e.predicate,n:0}; E.set(k,x);} x.n++;});
    let EL=[...E.values()]; EL.forEach(x=>{x.tier=(isRowKey(x.a)||isRowKey(x.b)||isGrp(x.a)||isGrp(x.b)||DOCS.has((NB.get(x.a)||{}).type)||DOCS.has((NB.get(x.b)||{}).type))?'ev':'core';});
    let KL=[...keys]; DERM.clear(); let derN=0, litN=0, clsN=0;
    if(MODE==='rdf'){const D=addDerived(KL,EL); KL=KL.concat(D.add); EL=EL.concat(D.ed); derN=D.add.length; D.add.forEach(k=>{if(DERM.get(k).kind==='cls') clsN++; else litN++;});}
    netSim(KL,EL);
    const deg=new Map(); EL.forEach(e=>{deg.set(e.a,(deg.get(e.a)||0)+1); deg.set(e.b,(deg.get(e.b)||0)+1);});
    const NET=NETm(), P=new Map();
    KL.forEach(k=>{const p=NET.pos.get(k); let ex;
      if(MODE==='card'){const [w,h]=cardSize(k); ex={key:k,x:p.x,y:p.y,w,h,r:Math.max(w,h)/2,rect:true};}
      else {const r=netR(k,deg.get(k)); ex={key:k,x:p.x,y:p.y,w:2*r,h:2*r,r};}
      if(isGrp(k)){ex.group=k[1]; ex.vi=+k.slice(3); ex.members=members.get(k)||[];}
      P.set(k,ex);});
    let y1=0; P.forEach(b=>{y1=Math.max(y1,b.y+b.h/2);});
    return {P,E:EL,bands:[],lbTop:0,laneA:new Set(),H:y1+40,net:true,derN,clsN,litN};
  }
  function netNodeSVG(b){
    const k=b.key, d=DERM.get(k), hit=G.hitN.has(k), sel=G.sel===k;
    if(d){const cls=['nd','dn',d.kind]; if(hit) cls.push('hit'); if(sel) cls.push('sel');
      if(d.kind==='cls'){const t=TYPE_KO[d.type]||d.type, w=Math.max(70,d.type.length*7+22);
        return `<g class="${cls.join(' ')} cat-${CAT[d.type]||'ev'}" data-k="${esc(k)}" transform="translate(${b.x.toFixed(1)},${b.y.toFixed(1)})" tabindex="0" role="button" aria-label="클래스 ${esc(d.type)}"><title>클래스 ${esc(d.type)} · 인스턴스 ${d.total}개 (type에서 파생)</title><rect class="bg" x="${-w/2}" y="-15" width="${w}" height="30" rx="15"/><text class="ct" y="-1" text-anchor="middle">${esc(d.type)}</text><text class="cs" y="10" text-anchor="middle">${esc(t)} · ${d.total}</text></g>`;}
      const txt=short(d.attr+' = '+litText(d.value),34), showL=hit||sel||G.hitN.has(d.owner)||(!G.hitN.size&&G.vb.w<1300);
      return `<g class="${cls.join(' ')}" data-k="${esc(k)}" transform="translate(${b.x.toFixed(1)},${b.y.toFixed(1)})" tabindex="0" role="button" aria-label="${esc(txt)}"><title>${esc(d.attr+' = '+litText(d.value))} (attributes에서 파생)</title><rect class="bg" x="-3.2" y="-3.2" width="6.4" height="6.4" rx="1.2"/>${showL?`<text class="lt" x="6" y="3">${esc(txt)}</text>`:''}</g>`;}
    const cls=['nd','nn']; let cat='ev', lab='', kd='';
    if(b.group){cat=b.group==='L'?'bom':'ev'; cls.push('grp'); lab=`v${VER[b.vi].v} ${b.group==='L'?'BOM 행':'원문 행'} 묶음`; kd='묶음';}
    else {const n=NB.get(k); cat=CAT[n.type]||'ev'; kd=TYPE_KO[n.type]||n.type; lab=ROWT.has(n.type)?nlabel(k):n.display_id;
      if(n.type==='BOMApplication'){const l=APP_LINE.get(k); lab='적용 · '+(l?short(desig(l),12):'');} if(n.type==='ApprovalGrant') lab='승인 v'+A(k).approved_version; if(n.type==='DocumentVersion') lab='문서 v'+A(k).version; if(n.type==='SourceSnapshot') lab='스냅샷 '+h8(A(k).sha256); if(n.type==='BOMLine'&&LINE_APP.has(k)) lab=short(desig(k),14)+' · v'+VER[LINE_V.get(k)].v;
      if(ROWT.has(n.type)&&!LINE_APP.has(k)) cls.push('sm');
      if(n.origin==='synthetic_assumption'||(n.type==='SupplierOffer'&&A(k).supplier_confirmation===false)) cls.push('tent');
      if(n.type==='ProductConfiguration') cls.push('st-warn');}
    cls.push('cat-'+cat); if(hit) cls.push('hit'); if(sel) cls.push('sel'); if(PIN.has(k)) cls.push('pin');
    const showL=!cls.includes('sm')||sel||PIN.has(k)||(hit&&G.hitN.size<=20);
    return `<g class="${cls.join(' ')}" data-k="${esc(k)}" transform="translate(${b.x.toFixed(1)},${b.y.toFixed(1)})" tabindex="0" role="button" aria-label="${esc(kd+' '+lab)}"><title>${esc(kd+' · '+lab)}</title>${b.group?`<circle class="gring" r="${(b.r+5).toFixed(1)}"/>`:''}<circle class="bg" r="${b.r.toFixed(1)}"/>`
      +(b.group?`<text class="gc" y="4" text-anchor="middle">${b.members.length}</text>`:'')
      +(showL?`<text class="nl" x="${(b.r+4).toFixed(1)}" y="4">${esc(short(lab,26))}</text>`:'')+`</g>`;
  }
  function clipPt(B,ux,uy,pad){if(B.rect){const tx=Math.abs(ux)>1e-6?B.w/2/Math.abs(ux):1e9, ty=Math.abs(uy)>1e-6?B.h/2/Math.abs(uy):1e9; return Math.min(tx,ty)+pad;} const d=DERM.get(B.key); if(d&&d.kind==='cls'){const w=Math.max(70,d.type.length*7+22)/2; const tx=Math.abs(ux)>1e-6?w/Math.abs(ux):1e9, ty=Math.abs(uy)>1e-6?15/Math.abs(uy):1e9; return Math.min(tx,ty)+pad;} return B.r+pad;}
  function renderNet(V){
    const {P,E}=V;
    let h=`<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path class="ahp" d="M0,1L9,5L0,9z"/></marker><marker id="ahh" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path class="ahh" d="M0,1L9,5L0,9z"/></marker></defs><g class="edges">`;
    E.forEach(e=>{const S=P.get(e.a),T=P.get(e.b); if(!S||!T) return; const dx=T.x-S.x, dy=T.y-S.y, d=Math.hypot(dx,dy)||1, ux=dx/d, uy=dy/d;
      const s0=clipPt(S,ux,uy,0), t0=clipPt(T,ux,uy,2); if(s0+t0>=d) return;
      const sx=S.x+ux*s0, sy=S.y+uy*s0, tx=T.x-ux*t0, ty=T.y-uy*t0, mx=(sx+tx)/2, my=(sy+ty)/2, hit=G.hitE.has(pk(e.a,e.b));
      const w=e.n>1?Math.min(5,1.2+Math.log2(e.n)*0.7):null, der=e.tier==='der', lit=der&&e.p!=='rdf:type';
      h+=`<g class="eg ${e.tier} ${lit?'lit':''} ${hit?'hit':''}"><path d="M${sx.toFixed(1)},${sy.toFixed(1)} L${tx.toFixed(1)},${ty.toFixed(1)}"${lit?'':` marker-end="url(#${hit?'ahh':'ah'})"`}${w?` style="stroke-width:${w.toFixed(1)}"`:''}/>`
        +(e.n>1?`<g class="cnt" transform="translate(${mx.toFixed(1)},${my.toFixed(1)})"><rect x="-17" y="-9" width="34" height="16" rx="8"/><text y="3" text-anchor="middle">×${e.n}</text></g>`:'')
        +(lit?'':`<text class="el" x="${mx.toFixed(1)}" y="${(my-(e.n>1?13:5)).toFixed(1)}" text-anchor="middle">${esc(PRED_KO[e.p]||e.p)}</text>`)+`</g>`;});
    h+='</g><g class="nodes">';
    const order=[...P.values()].sort((a,b)=>((G.hitN.has(a.key)?2:0)+(DERM.has(a.key)?0:1))-((G.hitN.has(b.key)?2:0)+(DERM.has(b.key)?0:1)));
    order.forEach(b=>{h+=MODE==='card'&&!DERM.has(b.key)?nodeSVG(b):netNodeSVG(b);});
    return h;
  }
  function derDetail(key,close){const d=DERM.get(key);
    if(d.kind==='cls') return `<div class="ih"><div><div class="k">클래스 · type에서 파생</div><h4 class="mono">${esc(d.type)}</h4><div class="lbl">${esc(TYPE_KO[d.type]||'')}</div></div>${close}</div>
      <p class="note">원본 노드의 <span class="mono">type</span> 값이 같은 객체를 한 클래스 노드에 잇는 화면 표시입니다. 원본 JSON의 관계(edges)가 아닙니다.</p>
      <h5>인스턴스 ${d.total}개</h5><div class="rc" style="padding-left:0">${d.inst.slice(0,40).map(o=>`<button class="chip" type="button" data-node="${esc(o)}">${esc(isGrp(o)?'묶음 '+o.slice(3)+' ('+(G._grpN.get(o)||0)+')':nlabel(o))}</button>`).join('')}</div>`;
    return `<div class="ih"><div><div class="k">속성값 · attributes에서 파생</div><h4 class="mono">${esc(d.attr)}</h4></div>${close}</div>
      <dl class="ikv" style="margin-top:8px"><dt>값</dt><dd>${fmtV(d.value)}</dd><dt>소유 객체</dt><dd><button class="chip" type="button" data-node="${esc(d.owner)}">${esc(nlabel(d.owner))}</button></dd><dt>유형</dt><dd>${esc(NB.get(d.owner).type)}</dd></dl>
      <p class="note">원본 노드의 속성 하나를 값 노드로 펼친 화면 표시입니다. 원본 JSON의 관계(edges)가 아닙니다.</p>`;}
  function syncModeUI(){const m=MODE;
    root.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===m)));
    const cv=$('canvas'); cv.classList.toggle('netmode',m!=='lane'); cv.classList.toggle('rdfmode',m==='rdf'); cv.classList.toggle('cardmode',m==='card');
    $('relayout').hidden=m==='lane';
    root.querySelector('[data-layer="rows"]').title=m!=='lane'?`끄면 BOM 행·원문 행 ${GD.nodes.filter(n=>ROWT.has(n.type)).length}개를 모두 펼쳐 보입니다`:'끄면 BOM 행 묶음을 숨깁니다';}
  function setMode(m){MODE=m; try{localStorage.setItem('ontologics-mks2-graph-mode',m);}catch(e){} syncModeUI();
    stopTrace(); G.hitN.clear(); G.hitE.clear(); if(G.sel&&(DERM.has(G.sel))) G.sel=null; if(G.sel) G.focus=G.sel; else G.focus=null;
    renderGraph(); if(G.sel) fitTo([...G.hitN],0); else fitAll(0);}
  root.querySelector('.modes').addEventListener('click',e=>{const b=e.target.closest('[data-mode]'); if(b&&b.dataset.mode!==MODE) setMode(b.dataset.mode);});
  $('relayout').onclick=()=>{const N=NETm(); N.pos.clear(); N.sig=''; N.first=true; renderGraph(); fitAll();};
  let ndrag=null, suppressClick=false;
  on(svg,'pointerdown',e=>{if(MODE==='lane'||e.button!==0) return; const g=e.target.closest('.nd'); if(!g) return; ndrag={k:g.dataset.k,x:e.clientX,y:e.clientY,moved:false};});
  on(window,'pointermove',e=>{if(!ndrag) return; if(!ndrag.moved&&Math.abs(e.clientX-ndrag.x)+Math.abs(e.clientY-ndrag.y)>4) ndrag.moved=true;
    if(ndrag.moved){const p=svgPt(e), q=NETm().pos.get(ndrag.k); if(q){q.x=p.x; q.y=p.y; q.vx=0; q.vy=0; q.fx=true; renderGraph();}}});
  on(window,'pointerup',()=>{if(!ndrag) return; if(ndrag.moved) suppressClick=true; ndrag=null; setTimeout(()=>{suppressClick=false;},0);});
  syncModeUI();
  
  
  on($('bom'), 'click', () => onBom());
  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { if (!destroyed && !G.fitted) renderGraph(); }) : null;
  resizeObserver?.observe(svg);
  renderGraph();
  return {
    focus(id) { if (!destroyed) focusAny(id, true); },
    /** Replay supplied answer evidence only; never fetches, infers or writes. */
    replay({ hops = [], nodeIds = [] } = {}) {
      if (destroyed) return Promise.resolve({ completed: false });
      const pairs = hops.filter(h => Array.isArray(h) && NB.has(h[0]) && NB.has(h[1]));
      return new Promise(resolve => {
        if (!pairs.length) {
          stopTrace(); clearHits();
          nodeIds.filter(id => NB.has(id)).forEach(id => { ensureVisible(id); });
          nodeIds.filter(id => NB.has(id)).forEach(id => { const s = slot(id); if (s) G.hitN.add(s); });
          renderGraph(); if (G.hitN.size) fitTo([...G.hitN], reduced() ? 0 : 600);
          resolve({ completed: true }); return;
        }
        trace(pairs, () => { traceDone = null; resolve({ completed: true }); });
        traceDone = completed => resolve({ completed });
      });
    },
    cancelReplay() { if (!destroyed) { stopTrace(); renderGraph(); } },
    reset() { if (destroyed) return; stopTrace(); clearHits(); G.sel = null; PIN.clear(); $('rowx').hidden = true; renderGraph(); fitAll(0); },
    resize() { if (!destroyed && !G.fitted) renderGraph(); },
    /** A newer ledger version exists but its graph projection is not ready. */
    setLag(text) { const banner = $('lagban'); banner.hidden = !text; banner.textContent = text || ''; },
    destroy() {
      destroyed = true; stopTrace(); cancelAnimationFrame(G.anim); resizeObserver?.disconnect();
      for (const [target, type, handler, options] of listeners) target.removeEventListener(type, handler, options);
      root.remove();
    },
  };
}
