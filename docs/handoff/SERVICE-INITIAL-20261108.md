# SERVICE-INITIAL-20261108 — 초기 원장과 전체 스키마 조회 연결

2026-11-08 · `codex/mks2-ui-tabs` · 기준 부모 `bc51aa0`.
검증한 코드의 개별 SHA-256은 [실행 증거](SERVICE_INITIAL_EVIDENCE.json)에 기록한다.

## 구현한 범위

MK-S2의 표시 데이터와 질문 응답을 참조 fixture에서 로컬 서비스로 전환했다. PostgreSQL의 검증된 INITIAL 기초 이관 → 사건/outbox → RDF·전체 SHACL → Neo4j 동일 버전 투영 → 신원·권한 적용 API → MK-S2 화면을 연결했다. 화면에서 조회할 때도 Neo4j의 실제 노드·관계·내용·버전을 대조하며, 장애를 저장 JSON으로 대체하지 않는다.

- `현재 구현` 직속 7파일은 `b3ee599`와 바이트 단위로 동일하다. 명시적 참조 모드에서만 기존 `/api/preview`를 사용한다.
- MK-S2의 메뉴·그래프·상세·근거 경로 재생·대시보드·드로어 구성을 유지했다. 실제 서비스 상태에 필요한 계정·조회 버전·새 사건 표시를 추가했다. 모든 상태의 픽셀 동일을 주장하지 않는다.
- 전체 T-Box·SHACL·adapter를 버전/hash로 보존한다. 클래스 52개, 관계 74개, shape 79개, T-Box 420 triple과 shape 604 triple을 내보낸다. 전체 스키마 보기에는 시연 미사용 항목도 포함한다.
- 초기 그래프는 기초 원장에서 확인된 18개 객체/11개 관계다. 이는 전체 스키마를 줄인 것이 아니라 초기 운영 사실의 범위다. 미래 생산·승인·장착·도착이나 EXPECTED 답변을 섞지 않았다.
- 기초 수량·LOT와 품목 집계·자기/타수요 예약·BOM 미등록·미검수·UNKNOWN을 서버에서 계산/표시한다. 원천 버전과 서버 버전은 별도 필드다. 과거 사건 시각·serial은 만들어 넣지 않는다.
- 개발 신원은 명시적인 local dev 모드와 loopback에서만 제공한다. HttpOnly/Strict 쿠키, CSRF, 회사·행위 권한, 현재 정책·신원 revision을 검사한다. 계정 전환/회수/공유 쿠키 변경/종료 시 이전 화면·대화를 무효화한다.
- `evidence_note`의 초안→검토→다른 계정 승인→정확 hash/version 반영을 실제 DB에서 구현했다. 멱등·감사·outbox·동시 실행·응답 유실 대사 계약을 검증했다. BOM·PR·PO 승인으로 확대 해석하지 않는다.
- 공통 typed ToolGateway를 실제 원장/질의 조회에 사용한다. 현재 질문 처리는 결정적 업무 도구이며 실제 모델·LangGraph Agent가 아니다. 미지원 질문은 UNKNOWN이다.
- 사건 polling은 변경 알림만 표시한다. 새 조회 후에도 과거 답변의 기준 버전을 보존하며 다른 버전의 그래프로 과거 근거를 재생하지 않는다. 원장 사건 때문에 모델을 호출하지 않는다.

## 실행·검증

Linux x86_64, Python 3.12.14, Node 24.22.1, Chromium 156.0.8078.79. Python 의존성은 `pyproject.toml`과 `uv.lock`, DB 구조는 checksum 고정 migration 3개를 사용했다. PostgreSQL 17/Neo4j 5.26의 실제 로컬 컨테이너와 별도 영속 볼륨을 사용했다. 자격증명·DB·비공개 원본·캡처는 Git 밖에 있다.

아래는 실행한 명령이며 비공개 경로 인자는 논리 슬롯으로 치환했다.

| 명령/검증 | 결과 | 판정 범위 |
|---|---|---|
| `python dev/audit_sources.py` | PASS 155/155 파일 hash, 349/349 literal ID, 1,171/1,171 위치 | 원본 무결성·literal 위치 대응. 서술 요구 전수 추적은 미완료 |
| `uv lock`, `uv sync` (쓰기 가능한 별도 uv cache 지정) | PASS | 실제 설치·lock 생성. 이후 `uv sync --locked` 사용 |
| `.venv/bin/python dev/service_stack.py status --runtime-file <private-runtime>` 및 기존 스택의 `up` | PASS | 기존 컨테이너 보존, 실제 PostgreSQL/Neo4j 인증 연결. 새 스택 생성 경로의 Mac 실기는 미검증 |
| `.venv/bin/python -m service --runtime-file <private-runtime> --dev-mode --reference-preview --import-initial --port 8870` | PASS | 원본 hash 확인→INITIAL 검증→로컬 DEMO 원장→투영. 반복 실행·재시작 시 기존 반입 보존 |
| PostgreSQL 실제 프로세스 재시작 후 별도 Python 프로세스로 기초 probe 재조회 | PASS | DB 영속성. 메모리 mock으로 대체하지 않음 |
| 실제 RDF/SHACL 검증, Neo4j 투영/직접 읽기, 동일 입력 재실행/버전 분리/변조 거절 | PASS | 초기 18노드/11관계, CONFORMS, 동일 원장/투영 버전. 별도 시험 scope에서 수행 |
| `.venv/bin/python dev/verify_service.py --runtime-file <private-runtime>` | **124 PASS** | 소스·INITIAL·스키마·권한·승인·원장·Gateway·API·기존 참조 서버 계약 |
| `node dev/check_service_ui.mjs --url=http://127.0.0.1:8870/mks2/ --cdp=http://127.0.0.1:9233 --artifacts=<private-artifacts>` | **27 PASS** | 실제 서비스·Neo4j 대상 브라우저 통합, 전체 그래프 보존, 조회·근거·계정/버전 동선 |
| Python/JavaScript 구문 검사, `git diff --check`, 보존본 7파일 비교 | PASS | 실제 변경 코드·보존 경계 |

124개 중 API host의 투영 장애 계약은 명시적인 synthetic reader로 시험했다. 실제 Neo4j 검증과 브라우저 통합은 별도 행의 증거다. 공개 테스트에는 독립 합성 fixture만 사용한다. 브라우저의 NOT_READY/신원 오류 주입 시험과 실제 서버의 로그아웃·공유 쿠키 전환 시험도 구분한다.

기존 UI-LOCAL/UI-TABS의 참조 화면 검증은 해당 대상 버전의 역사적 증거로 보존한다. MK-S2가 서비스 API로 바뀌었으므로 과거 22/27개 참조 검사를 새 서비스 성공으로 이월하지 않는다.

## 남은 범위와 다음 순서

부모 Task의 전체 VERIFIED 선언은 하지 않는다. T00의 서술 요구 대응, T01~T04의 전체 계약/화면 인수는 남아 있다. T10은 전체 스키마 보존과 INITIAL 조회 준비 slice이며 T09 이후 전체 BOM·업무 사건 통합 인수를 우회한 완료가 아니다.

1. T00의 literal 밖 서술 요구 추적 및 T03/T04의 남은 UI/공통 소비자 계약을 마무리한다.
2. T05 관측·ModelGateway → T06 LangGraph → T07 영속 Checkpointer/HITL, 그리고 T08 제한 Runner를 공식 의존성에 따라 구현한다.
3. T08 검증 뒤 실제 BOM 접수·검수·정확 버전 승인, 전체 적용 소요·견적·PR/PO·입하·수입검사·가용 반영을 연결한다. 현재 추가 v1.3 BOM 후보 57개는 초기 원장에 반입하지 않았고 전체 BOM 확보를 판정하지 않는다.
4. 전체 공식 v1.7 의미 대응·CQ20·후속 생산/출하/고객 도착·추적/품질·규모 인수는 유지한다.

현재 환경에는 기존 모델/관측용 키 3개의 바인딩이 없다. 실제 모델 호출·외부 모델 비용은 이번 실행 **0회/0달러**이며 기존 누적 예산을 초기화하지 않았다. 이전 환경의 인증 조회 200을 현재 실행으로 이월하지 않는다. 모델·Langfuse/LangSmith·Runner·실제 BOM 파싱·전체 CQ·Mac 실기는 NOT_RUN이다.

현재 서버 주소는 Cloud 내부 loopback이다. 사용자 Mac의 접속 주소나 배포 주소가 아니다. main 병합·환경 Publish·배포·공급사 송신·운영 원장 변경은 하지 않았다.
