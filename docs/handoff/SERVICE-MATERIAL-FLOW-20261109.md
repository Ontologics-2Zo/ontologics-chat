# 1차 자재 확보 연결 검증 · 2026-11-09

DEC-H31의 제한된 구현/검증 기록이다. 전체 1차 서비스나 부모 T00~T23의 완료 기록은 아니다. 구현 기준 커밋은 `8ba67fd80b8adeed0bf82bdba05aaa4a91b6f25e`, 브랜치는 `codex/phase1-material-flow`다. 이후 문서·구매 문서 없음 표시 보완은 같은 브랜치에 기록한다. main 병합·배포·실제 공급사 송신은 수행하지 않았다.

## 사용자 결정과 화면

- MD의 잔여 1차 작업을 이어가되 기존 파일/DB와 기존 US$10 예산을 보존한다.
- 부품 등록은 SRC-MKS2의 기존 8단계(원본 접수→개정 식별→추출·검증→차이 확인→품목 연결→등록 초안→검토·승인→반영)를 유지한다. 별도 Product 화면·메뉴를 추가하지 않는다. 후보·근거 조회는 기존 품목 연결 단계 안에서만 표시한다. 조회 응답은 검수 입력·포커스·저장·승인 상태를 바꾸지 않는다.
- 구매는 원형의 본문 공급 흐름 카드에서 상태별 조치를 연결한다. 별도 구매 메뉴/드로어를 만들지 않는다. 원형의 간단한 조치에 서버의 검토/승인/반영 분리·정확 버전·반려 사유 표시를 보완한다.
- 자재 표에 품목명, 단위 설명, 대당 수량, `목표 × 대당 수량`, 수량 판정과 검수 상태를 분리했다. `EA`는 낱개 단위이며 부품 상태가 아니다.
- `material-calculation/1`은 현재 서비스 산식을 명시한다. 사내 규정 승인은 `NOT_RECORDED`다. 규칙을 명확히 표시한 것이 미검수 수량·매핑·재고를 실제 확정값으로 승격한 것은 아니다. 수율 반영 43은 여전히 가정이며 기본 목표40과 구분한다.

## 구현 범위

| 요구/Task | 연결한 코드와 행동 | 이번 검증 | 남은 수용 |
|---|---|---|---|
| EV01~02, AG16, AI03 / T11.1 | `retrieval.py`, `retrieval_embedding.py`, `material_api.py`: 현재 권한·회사·snapshot·시점·문서hash를 검사한 exact/BM25/로컬 다국어 dense 검색, RRF, 기록된 관계만 R3 확장, 과거 근거 재열기 | MAT-RAG | 광범위 검색 품질·전체 corpus/CQ, 선택 실험 R2/R4/R5 |
| T06, T09.1, T12.1 | `product.py`, `agents/product.py`, 기존 `bom.js` 품목 연결: Runner 파싱 결과 재사용, 초안/반영 문서의 정확 버전 후보 검토, 모호성·단위·구성 충돌 표시 | MAT-PRODUCT | 전체 형식 일반화, 실제 AI 추출/매핑 품질, 제조 기술검수 |
| T13.1 | `materials.py`, `views.py`, `query_answers.py`: 같은 품목 합산, 가용/보류/예약, 현재 입하·검사 overlay, 품목/LOT별 근거와 기준 시점 | MAT-STOCK | 동시 예약·불출/소비/반납, 일반 대체 승인, 실제 재고 확정 |
| T03, T14.1 | `procurement*.py`, migration008, `ledger.py`: 견적→PR→PO→내부 모의 공급확인→입하→IQC, 독립 검토/승인/반려·명시 반영, 부분 수량·취소·멱등·감사·outbox | MAT-PROC / MAT-E2E | 다업무 일반화·전체 예외·운영 역할, 쓰기 Agent의 영속 업무 승인 HITL |
| T04~07, T19.1 | 명시 typed `product_review` / `procurement_review` 읽기 subgraph, 서버 도구·scope 검사와 checkpoint/결과 재사용 | MAT-AGENT | 전체 업무 Agent·모델 품질·실시간 관련 대화 갱신·최종 회복/규모 인수 |

로컬 임베딩은 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`의 고정 hash ONNX/tokenizer를 사용한다. 384차원 CPU 추론이며 weights는 Git 밖에 보관한다. 런타임 자동 다운로드·remote code·원문 외부 전송은 없다. `model-settings.json`의 `retrieval` 설정이 없으면 R0만 사용 가능하고 R1/R3은 미완료 색인 상태를 반환한다. 의미 검색 결과는 품목 동일성/기술 적합성의 승인이 아니다.

## 실행 환경과 명령

Mac arm64, Python3.12 `.venv`, 실제 PostgreSQL/Neo4j/Docker Runner, 로컬 ONNX를 사용했다. 전체 검사에는 별도로 생성한 disposable PostgreSQL DB의 `TEST_DATABASE_URL`, 기존 검증 설정의 `TEST_NEO4J_URI`/`TEST_NEO4J_PASSWORD`, `PRIVATE_INPUT_ROOT`, Runner 설정, `ONTO_RETRIEVAL_MODEL_SETTINGS`를 child environment로 전달했다. 키/DSN/로컬 비공개 경로는 기록하지 않는다.

- `.venv/bin/python -m unittest discover -s tests -q`: **345 PASS / 0 SKIP**, 58.423s. 실행 private log `full-final.log`.
- 마지막 구매 중복 수요 보완 후 `unittest discover -s tests -p 'test_procurement*.py' -q`: **13 PASS**, 12.201s. 같은 품목의 이전 BOM 구매가 새 BOM 등록으로 중복 허용되지 않음.
- 마지막 LOT/납기 근거 범위 보완 후 `.venv/bin/python -m unittest tests.test_query_inventory tests.test_query_answers -q`: **27 PASS**.
- `node --test tests/*.mjs`: **18 PASS**. 부동소수/미확인, 원문 escape, 기존 BOM 단계 내 조회, stale 응답, 초안·과거 답변 보존 포함.
- Node 구문 검사와 `git diff --check` PASS. 공급 카드의 UI 계약 별도 검사에서 escape·독립 승인 조치·IQC 수량/금액 구분·destroy 확인.

## 실제 데이터의 격리 복제 검증

기존 PostgreSQL dump를 **새 전용 DB**에 복원하고 **새 임시 Neo4j 컨테이너**로 분리했다. 기존 DB/Neo4j에 검증 업무 사건을 기록하지 않았다. 실제74행과 기존 선택5품목의 조건부 결정을 사용하되 신규 견적/공급/입하/검사는 모두 명시적 내부 시연 조건이다.

| 순서 | 복제 원장 버전 | 관측 |
|---|---:|---|
| 기존 기초/실제 BOM 재투영 | 1 / 2 | 18/11 → 176/252 노드/관계 |
| 견적·구매요청·내부발주 | 3 / 4 / 5 | 계획 부족23, MOQ/배수 적용, 운임 미확인은 총액 미확인 |
| 모의 공급확인 | 6 | 실제 송신/공급확약 아님, 확약 납기 미확인 |
| 입하23 | 7 | 가용20 유지, 검사대기23, 계획 부족23 유지 |
| IQC 합격23 | 8 | 가용43, 계획 부족0, 그래프197노드/277관계 |

모든6개 업무 반영 단계에서 독립 검토/승인·post 분리, post 재시도 결과, 실제 RDF/SHACL CONFORMS, Neo4j roundtrip을 확인했다. 기초 원장/BOM hash, 원문74행·적용 미확인69행·조건부5품목, 고객 도착잔여68·추가양품40이 보존됐다. 전체 제조 가능성은 UNKNOWN이다.

새 재고의 발생 시점과 기초 생산·납품 시점을 나누고, 원장 응답은 합성 초기 재고와 개발 입하·IQC 근거를 구분한다. 선택 품목/LOT와 무관한 다른 품목의 검사 근거를 붙이지 않는다. LOT에서 시작한 자재/납기 계산이 품목 전체 재고를 사용하면 근거도 같은 범위로 제공한다.

격리 DB의 두 Agent 읽기 실행은 각각 COMPLETED, 도구1회/checkpoint8개/artifact1개, live/Fake 모델0회였다. 생성·실행 재요청·결과 조회가 같았고 업무 snapshot·구매 문서·기존 모델 원장이 불변이었다. 원래 DB에 Agent 실행 이력을 새로 쓰는 확인은 자동 승인 검사에서 차단되어 격리 복제본으로 옮겼다.

## 원래 Mac 앱 보존

`8ba67fd`로 8870 앱만 재시작했고 `/health` 정상, 원래 업무 원장/투영 **v2**, 176노드/252관계다. 실제 BOM74행·기존 문서·전체 온톨로지52클래스/74관계/79shape를 보존했다. 기존 CQ 읽기9건, 새 Product 후보·로컬 dense 검색·빈 구매 목록의 읽기 API PASS, 조회 전후 snapshot 불변. 기존74행을 다시 등록하거나 승인/반영하지 않았다.

`web/` 직속7개 보존 화면은 `b3ee599`와 동일하다. 기존 runtime/Runner 설정과 기존 컨테이너14개·볼륨18개가 유지됐다. `.env`를 변경하지 않았다. 기존 `.idea/workspace.xml`의 동시 변경이 관측되어 그 상태를 그대로 보존했고, 에이전트는 `.idea`를 편집하거나 Git에 추가하지 않았다. 이를 hash 불변 PASS로 기록하지 않는다.

브라우저에서 작성자A의 v2 원장/그래프/대시보드, 품목명·단위·40/43 기준, 기존 BOM8단계 안의 후보 조회를 확인했다. 격리8871에서도 합격23→가용43과 기존74행 화면을 확인했다. 브라우저 수동 확인은 자동 전체 업무 UI 인수나 픽셀 동일의 증거가 아니다.

## 예산과 원본 경계

추가 유료 모델 호출 **0**. 작업 전 dump와 원래 DB를 대조한 누적 값은 live5회(완료3, 결과 미확정2), 확정비용 **US$0.00128**, 유지예약 **US$0.010932**다. 같은 `mks2-agent-20261108` 한도 US$10, 질문당US$0.50·재시도1·자동fallback OFF·기존 전송 범위를 유지했다. 과거 기록의3회라는 시점값을 현재값으로 재사용하지 않는다. 두 미확정 요청을 무료/환불로 처리하지 않았다.

## 비식별 실행 증거

상세 원문/응답/trace/백업은 Git 밖에 남긴다. 아래는 private evidence 파일의 hash이며 공개 코드에 원문을 복사하지 않는다.

| 검사 | SHA-256 |
|---|---|
| MAT-REGRESSION | `e7b9f0c8f0b4161c72e14c32d97b0aaa477eee183843d71cfb0500e4e6268301` |
| MAT-PROC | `060dbb4dfee59a83cd4977d69d299954f2479e35684b0917d23ae6bc86869af1` |
| MAT-PRESERVE | `25a2543db6dfa97d51035412f5a66664b2b1b41580fad0331efb42a3f6013e2f` |
| MAT-LIVE-READ | `080313b87391b7782f3b926ea1cb21e627a8256c233e318733ac795e1079a9bf` |
| MAT-BUDGET | `ef4a6b30af8eb0d329dadbd0e34bd21b444ddbc67b379ce434fda6d69f5d4585` |
| MAT-E2E | `55350f90e31dfb08ea417f4655b905f4c9d1f509c3906c6c1a3180e577b394c6` |
| MAT-HISTORICAL-READ | `8f85f8e2d38e449c3ce3d5b48cde373a87b80dd30bed00b415567868f6c244e7` |
| MAT-AGENT | `3510f818dc6e29c8595e4af94e246388c16e81de6af6d6058afdb28bfb55b7ec` |
