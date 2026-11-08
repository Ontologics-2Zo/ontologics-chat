# SERVICE-CQ-NL-20261108 — 자연어 CQ의 원장 조회와 판단 범위

2026-11-08 · 작업 브랜치 `codex/cq68-natural-language` · 검증 코드 `aecd23a` · 기준 main `2e6f24c` (PR #2 병합).

**상태: VERIFIED — 아래의 제한된 규칙 기반 읽기 slice에 한정.** Mac의 실제 PostgreSQL·Neo4j·제한 Runner 환경에서 Python 223개·Node 11개 검사, native Chrome 수동 확인, 최종 8870 재시작과 읽기 API 9질의를 통과했다. 전체 Agent·범용 자연어·CQ20·부모 Task 수용은 미완료다. DEC-H27은 기존 MD의 CQ·근거·가정 계약에 따른 읽기 기능 구현 요청이다. PR #2의 병합 승인을 이 후속 변경의 자동 병합 승인으로 해석하지 않으며 새 main 병합은 수행하지 않았다.

이전 [Mac BOM 실행 기록](SERVICE-BOM-MAC-20261108.md)의 실제 원문 74행·선택 5품목·69행 미확인, 원장/그래프 v2와 보존 증거는 해당 버전의 기록으로 유지한다. 그때 확인한 키워드 분기의 한계나 수량 검사를 이번 자연어 표현 범위의 통과 증거로 이월하지 않는다.

## 이번 구현 범위

- `service/query_intents.py`가 표현·복합 질문·명시적 수율/목표를 허용된 읽기 의도로 해석한다. 불분명한 수량·대상·지원 범위는 질문 확인 또는 UNKNOWN으로 반환한다. 임의 도구·SQL·업무 쓰기를 선택하지 않는다.
- `service/query_answers.py`는 현재 권한으로 조회한 같은 원장 snapshot과 `service/materials.py`의 공통 계산을 사용해 이행·추가 양품/투입·소요/부족·재고·납기 판단의 답변을 구성한다. 숫자와 답변을 비공개 정답·시나리오 결말·화면 fixture에서 가져오지 않는다.
- 기존 `ledger.query` ToolGateway의 회사·신원·정확 원장/투영 버전 검사를 유지한다. 명시적 수율·추가 양품 목표 변경은 읽기 전용 What-if이며 BOM·재고·계약 수량·생산·출하·도착 사건을 변경하지 않는다.
- `web/mks2/query.js`와 MK-S2 질의 패널은 서버의 구조화된 결론·범위·출처·근거·기준 시점을 표시한다. 실제 BOM 원문, 합성 초기 원장, 조건부 시연 가정, 질문에서 지정한 가정, 미확인 입력을 구분한다. 기존 화면 7파일과 전체 온톨로지는 보존한다.

현재 구현은 **범위가 제한된 규칙 기반 자연어 해석**이다. LLM·ModelGateway·LangGraph·영속 Agent/Checkpoint·관측·Hybrid RAG 또는 일반 자연어 품질의 완료를 뜻하지 않는다. 모델 호출 경로는 없으며 실제 모델 검증은 NOT_RUN이다. DEC-H18/19의 기존 예산·전송 제한은 그대로 유지한다.

## 기존 요구와의 대응

CQ ID의 namespace를 유지하며 “CQ68”이라는 새 공식 CQ를 만들지 않는다. 여기서 CQ68은 남은 고객 도착 약속을 묻는 현재 작업의 설명이다.

| 읽기 기능 | 기존 CQ 연결 | 요구·화면·Task | 구현 경로 | 이번 검증 ID |
|---|---|---|---|---|
| 회차 이행·잔여 약속 | DEMO-CQ-01, B-CQ02 | FUL-01~03, UX-H01; T04/T19.1 | `query_intents.py`, `query_answers.py` | CQ-NL-FUL |
| 추가 양품·수율 가정 투입 | DEMO-CQ-02, B-CQ03 | CM05, UX-H05; T13.1 | 위 해석/답변과 `materials.py` | CQ-NL-PLAN |
| 등록 BOM의 조건부 소요·부족 | CQ09, B-CQ04 | R12-INV-01~03, UX-H05; T13.1 | `query_answers.py`, `materials.py` | CQ-NL-MAT |
| 현재 가용 재고·예약 | B-CQ01, B-CQ04 | R12-INV-01~03; T13.1 | 같은 snapshot의 재고 읽기 | CQ-NL-INV |
| 납기 질문의 부분 답변·근거 부족 | DEMO-CQ-01, B-CQ01, B-CQ04 | FUL-01~03, AI-04~07; T19.1, T16의 잔여 계약 보존 | `query_answers.py` | CQ-NL-DELIVERY |
| 범위 확인·권한·버전·출처 표시 | 위 관련 CQ의 실패/미확인 사례 | AI-04~07, EV01~02; T04/T19.1/T22.1 | Gateway·API·`web/mks2/query.js` | CQ-NL-SCOPE, CQ-NL-UI |

전체 CQ20, B-CQ06의 전체 What-if/승인·위험 흐름, 추적·장착 및 후속 생산/출하/도착은 이 표의 구현 완료로 확대하지 않는다. 공식 T05~07·T11~12와 T19/T22의 선행·전체 수용은 남아 있다.

## 수량과 납기 답변 계약

- 현재 잔여 약속은 `약속 − 고객 도착`, 추가 양품 목표는 `약속 − 양품`으로 원장에서 조회/계산한다. 기존 기초 기준의 68과 40은 서로 다른 의미다. 질문에 적힌 68을 신규 생산량으로 사용하지 않는다.
- 기본 계획 수율 94%에서 투입은 `ceil(추가 양품 목표 / 0.94)`다. 명시적 다른 수율/목표는 질문 가정으로 표시한다. 계획 투입이 양품 수량을 보장하지 않으며 원장의 확정 목표를 덮어쓰지 않는다.
- 선택 품목의 소요·부족은 서버에 등록된 행별 가정·단위·매핑과 합성 초기 재고의 조건부 계산이다. 전체 원문과 미확인 행을 유지하고 미확인 재고·매핑을 0으로 대체하지 않는다.
- 납기 질문에는 확인 가능한 현재 수량과 부족을 제공하되, **실제 고객 도착 가능성은 UNKNOWN**, 전체 응답은 확인 범위를 밝힌 **PARTIAL**로 구분한다. 날짜·ETA·성공 확률을 만들어내지 않는다.
- 실제 납기 판단에는 전체 BOM 적용/기술검수, 실제 재고, 조달·공급사 확약, 작업 능력·일정, 검사·품질, 영업일/휴일 달력, 출하·운송, 계약상 기한 의미와 근거가 필요하다. 누락 항목을 사용자에게 표시한다.
- 기존 제안 일정·시간대/시각·합성 성공 결말은 이번 runtime 계산에 활성화하지 않는다. 실제 자료와 제안 시연 가정을 섞거나 미래 bundle/EXPECTED를 현재 답변 근거로 사용하지 않는다.

요구 근거는 [인수 게이트의 대표 CQ](ACCEPTANCE_GATES.md#4-대표-cq와-데이터-격리), [데이터 반입 계약](DATA_IMPORT_CONTRACT.md), [T13/T16/T19 작업표](IMPLEMENTATION_TASKS.md), [자료 인덱스](SOURCE_INDEX.md)의 SRC-V17-01 AI-01~07/LG17-01/FUL-01~03, SRC-V17-03 §3·§5·§12 및 SRC-CQ12다. 원문은 Git 밖에 유지한다.

## 검증 상태

대상은 기준 main `2e6f24c` 이후 `codex/cq68-natural-language`의 코드 커밋 `aecd23add18615c33a051b43686a75a426929d01`이다. 아래 결과는 2026-11-08 Mac 로컬에서 실행한 해당 범위만의 증거다. 부모 Task·전체 Agent·범용 자연어·전체 CQ20의 수용으로 확대하지 않는다.

| 검증 ID | 대상/명령 또는 관측 | 현재 결과 |
|---|---|---|
| CQ-NL-FUL/PLAN/MAT/INV/DELIVERY/SCOPE | `.venv/bin/python dev/verify_service.py --runtime-file <Git 밖 runtime>`; 비공개 환경 주입 후 실제 PostgreSQL·Neo4j·Runner를 연결한 Mac suite | VERIFIED — Python 223 PASS, 28.650초 |
| CQ-NL-UI | `node --test tests/*.mjs`; 구조화된 상태·출처·텍스트/근거와 수량 표시 회귀 | VERIFIED — Node 11 PASS, 실패0·SKIP0 |
| CQ-NL-API | 최종 로컬 서비스 읽기 질의 9개, 이전 8개 결과 동일, 오래된 버전 거절·업무 snapshot 불변 | VERIFIED — 9 PASS; 원장/투영 v2, 모델 호출0·업무 원장 변경0, stale version HTTP409 |
| CQ-NL-MAC | native Chrome의 작성자 A로 MK-S2 수동 관측; 복합 질문·가정·출처/상태·근거 재생·이전 답변 보존 | VERIFIED — 아래 관측 범위와 캡처 배치 확인 |
| CQ-NL-PRESERVE | 업무 snapshot·승인 BOM·전체 T-Box/SHACL·보존 화면·자격증명 파일 불변 | VERIFIED — 업무 snapshot 전체 필드 동일, BOM 승인 hash·74행·반영 v2 유지, T-Box/SHACL 동형 및52/74/79, 화면7파일 `b3ee599`와 byte 동일, 자격증명 파일 hash 동일 |
| CQ-NL-RESTART | 최종 코드의 8870 재시작·health·동일 원장 재조회·native Chrome 확인 | VERIFIED — `/health` HTTP200, 모델0, 9질의 및 작성자 A의 68대 납기/자재 응답 표시 재확인; 확인용 8871 프로세스 종료 |
| 실제 모델·전체 CQ20·일반 자연어/Agent 품질 | 공식 후속 검증 | NOT_RUN |

Python suite와 live API 검사는 비공개 환경 주입 helper로 실행했다. 위 runtime 표기는 경로를 가린 명령 형식이며 값·접속정보는 출력하지 않았다. 공개 테스트는 격리된 DB schema를 사용한다. 기존 업무 DB를 초기화하지 않았다. 보존 화면의 직접 byte 대조와 `git diff --check`도 통과했다.

수동 Chrome 확인은 확인용 8871에서 작성자 A로 수행했다. 68대 납기와 자재를 함께 묻는 질문은 PARTIAL로 표시하고 실제 원문74행·조건부5품목·미확인69행, 합성 초기 원장의 잔여68/추가 양품40, 수율94% 가정의 투입43을 구분했다. 명시적 목표50·수율90%의 복합 What-if는 투입56을 반환하면서 원장 기준 목표40을 보존했다. 실제 원본·합성 원장·시연 가정·미확인 카드/배지와 화면 배치를 확인했다.

답변은 근거 재생 완료를 기다리지 않고 표시됐다. 재생 중 새 질문은 이전 재생을 취소하고 두 답변과 각 답변의 v2 메타데이터를 유지했다. 최종 실행 주소는 `http://127.0.0.1:8870/#mks2`이며 최신 코드 재시작 뒤 같은 응답과 원장 불변을 재확인했다. 최종 8870도 native Chrome에서 작성자 A의 68대 납기/자재 응답이 보이는 상태로 확인하고 열어 두었다.

비공개 증거 ID는 `final-restart-verification.json`, `service-suite-browser-followup.log`이며 아래 hash로 연결한다. 비공개 runtime·접속정보·질문/응답·실제 원문·상세 로그·캡처는 Git 밖에 보존한다. T04/T13/T19/T22의 제한된 적용만 연결하고 부모 Task는 IN_PROGRESS로 유지한다.

| 비식별 증거 묶음 | SHA-256 |
|---|---|
| Python 최종 suite | `ec7a92939309c3742fa775185b6007b522147bd8939777501dcd26b1a9c46339` |
| 최종 재시작·9질의·보존 | `9e52976fca4a67346d03230c95a44e3a648b7cc4b3cb1e552ed9a08bc8b6176f` |
| native Chrome 관측 | `3c82e6cd0fded7ff883038be69bdd476fb97367b3f10c6fde3f58302aad65f22` |
