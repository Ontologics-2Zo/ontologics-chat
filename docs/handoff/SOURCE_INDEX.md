# 인계 기준 자료 인덱스

공식 원문과 데이터는 공개 저장소에 포함하지 않는다. 실행 환경에서 `PRIVATE_INPUT_ROOT`를 Git 밖 비공개 입력 폴더로 설정하고, 아래 슬롯을 찾아 읽는다. 이 변수는 프로젝트 설정이며 자동 자료 전송 기능이 아니다.

[자료 카탈로그](SOURCE_CATALOG.json)는 논리 위치와 hash를 제공한다. 비공개 폴더의 `SOURCE_MANIFEST.json`에는 개별 파일별 SHA-256·크기가 있다. 이번 준비에서 복사본과 원본 hash, 공식 3문서와 사용자 ZIP의 바이트 일치를 확인했다. Cloud 접근과 서비스 실행은 아직 검증하지 않았다.

## SRC-V17-01

`v1.7/01_COMMON_REQUIREMENTS.md` — 공식 전체 기능·도메인·승인·거버넌스·AI·수용 기준. v1.7 요구 버전은 시연 데이터 v1.3과 별개다.

## SRC-V17-02

`v1.7/02_SCREEN_DESIGN.md` — SD-001~036, PNL-01~24의 업무 의미와 수용 기준. 과거 UI v2.6 배치 고정은 최신 MK-S2 결정으로 대체하고 기능 추적은 보존한다.

## SRC-V17-03

`v1.7/03_DEVELOPMENT_DESIGN.md` — §2~3 SDK/계약, §4 원장, §5 Harness/Context/패러다임/Checkpoint/LangGraph/Langfuse, §6 Sandbox, §7 온톨로지/RAG, §8 모델, §10 평가, §12 T00~T23.

## SRC-PLAN

`current-plan/`의 PHASE_1_SCOPE.md, IMPLEMENTATION_PLAN.md, PRE_IMPLEMENTATION_REVIEW.md, REALTIME_UPDATE_DESIGN.md — 최신 1차 자재 확보 우선순위·전체 이행 보존·실시간 상태 반영.

## SRC-AI

`ai-assurance/` — AI_ASSURANCE_REQUIREMENTS.md, METRICS_AND_EXPERIMENTS.md, CQ_EVALUATION_MATRIX.md, 평가 계약 JSON과 기존 오프라인 검토 결과. 기존 20 CQ·추가 C/D 후보와 110개 사례 계약을 보존한다. 서비스 통합·실제 LLM 평가 결과로 사용하지 않는다. 원답·정답·상세 평가 결과는 공개 Git 밖에 둔다.

## SRC-MKS2

`mk-s2/` — MK-S2.html과 데이터 구조·백엔드 규약·기능 시나리오·용어집, fixtures·schema. 원본의 한글 경로는 정규화 형식이 다를 수 있으므로 manifest의 실제 경로를 사용한다. 화면 동선의 기준이며 고정 응답·메모리 사건을 서버 구현으로 인정하지 않는다.

## SRC-DATA17

`v1.7-data/` — `data/raw`, `data/normalized`, `data/source_manifest.json`, 출처·검수 대기 자료. 실제 BOM 4모델·5스냅샷 전체 보존. 코드에 특정 원본 hash를 유일한 입력 조건으로 고정하지 않는다.

## SRC-CQ11

`cq-v1.1/` — 초기 MK-S2 수량과 기존 3→2→1 계약·근거. 기존 프로필을 최신 94% 가정에 덮어쓰지 않는다.

## SRC-CQ12

`cq-v1.2/` — 기존 CQ 전수 목록·신규 후보 중복 검토·관계 확장·정책·원문 요구 ID 스캔. 제안·미결을 확정 규칙으로 승격하지 않는다.

## SRC-V13

`full-delivery-v1.3/` — 전체 고객 도착 시연 bundle·가정·기대 결과·RDF/SHACL·분석 도구·기존 검증 보고서. 전체 bundle은 서버 초기 seed가 아니다. 정상·지연·누락·잘못된 입력을 분리한다.

## SRC-GOV

`governance/` — 기존 데이터·온톨로지·업무·AI 정책과 화면 대응 검토. 기존 확정과 신규 제안 구분을 보존한다.

## 추적 정보와 이식 경계

[공식 식별자 인벤토리](REQUIREMENT_ID_INVENTORY.json)는 원문에서 찾은 349개 literal ID의 파일·행 위치를 보존한다. 요구뿐 아니라 화면·Task·근거·역사적 표기를 포함하며, 숫자 349를 독립 요구 수 또는 인수 통과 수로 쓰지 않는다. 범위 표기의 개별 전개·동의 ID 정리·ID 없는 서술 요구의 최종 추적은 T00에서 공식 원문과 대조한다.

원본 사본은 hash 보존을 위해 과거 상대경로·개인 절대경로·당시 실행 기록을 그대로 포함할 수 있다. 공개 인계는 그 경로를 실행 위치로 사용하지 않는다. 과거 폴더명은 PRIVATE_RELOCATION_MAP.json(비공개 입력 폴더)에서 현재 슬롯으로 해석한다. 과거 분석 스크립트는 그대로 실행 가능한 Cloud 초기화 도구가 아니며, 서비스용 이식·변환은 별도 구현 대상이다.

이전 GitHub 구현은 필수 입력 묶음에 포함하지 않았다. 필요한 코드 재사용은 T00에서 원래 저장소의 정확 커밋·파일·라이선스·검증 결과를 따로 확인한다. 그 UI나 제약을 신규 목표 요구로 삼지 않는다.
