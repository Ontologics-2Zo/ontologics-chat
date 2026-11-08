# SERVICE-BOM-MAC-20261108 — Mac 실제 BOM 등록과 조건부 소요 조회

2026-11-08 · Mac arm64 · `codex/mks2-bom-integration` · 검토 기준 PR #2 head `60d52fd82d8bf355300dd6f6af7dcd031efa4340` · 검증 코드 commit `c0167b26c4a118d02dd820c192abdf8f01eb7925`.

**상태: 실제74행·선택5품목 조건부 등록/조회 slice VERIFIED — 서버·수동 UI·독립 보존·재시작 검증 통과, main 병합 대기.** DEC-H26은 두 수정, 실제 74행 원본의 선택 5품목 조건부 검토·독립 승인·개발 원장 반영과 관련 검증 후 PR #2 병합을 명시적으로 승인했다. 이 VERIFIED는 아래 명시한 조건부 개발 원장 slice에만 적용하며 전체 제조 기술검수·전체 CQ/Agent 수용을 뜻하지 않는다. 아직 수행하지 않은 main 병합은 완료로 기록하지 않는다.

과거 [Cloud BOM 기록](SERVICE-BOM-20261108.md)과 [그 증거 JSON](SERVICE_BOM_EVIDENCE.json)은 당시 환경의 자원 강제 미지원·실제 원본 0건·합성 검사 증거로 그대로 보존한다. 이번 Mac의 성공은 그 과거 결과를 덮어쓰지 않는다. 별도 [Mac 증거 인덱스](SERVICE_BOM_MAC_EVIDENCE.json)에 환경·코드 hash·비식별 검증 ID를 연결한다.

## 확인한 범위

- Docker Desktop의 실제 메모리·PID·CPU 제한 강제 검사를 통과했다. 해당 immutable Runner의 소스 hash는 `7d1751eaaa9592807a12be0639a4e278977898029cb20b97328360a4118144cf`다. 원본은 이 Runner로만 파싱했으며 호스트 파서로 우회하지 않았다.
- 실제 원본 74행을 모두 보존했다. 선택 5품목의 매핑·RefDes·원문 수량을 기존 후보 메타데이터와 대조하고 명시적 조건부 시연 결정으로 등록했다. 나머지 69행은 UNRESOLVED이며 원문 행·근거를 삭제하지 않았다.
- 작성자 A가 접수·수정하고 자기 검토 요청은 HTTP 403으로 거절됐다. 검토자 B가 정확 문서 버전/hash를 검토·승인했다. 승인 후에도 원장은 v1이었으며 작성자 A의 별도 반영 요청에서만 v2가 됐다. 같은 반영 요청 재시도는 동일 결과·사건 1건을 유지했다.
- PostgreSQL 원장과 Neo4j 그래프가 v2로 일치했다. 전체 176노드/252관계에 SourceRow 74개, BOMLine 74개, BOMApplication 5개가 포함된다. 선택 5품목 소요·부족 산술이 서버 결과와 일치했다.
- 초기 재고와 약속 100·도착 32·잔여 68·추가 양품 목표 40은 변하지 않았다. 기존 원문과 합성 초기 잔액, 선택 품목의 조건부 적용을 구분한다. 전체 결과는 `PARTIAL`, `full_coverage=false`, 제조 가능성 `UNKNOWN`이다.
- 원본의 구성 표기 충돌과 제조 기술검수·현장 재고 근거는 미해결이다. 원문 등록 승인으로 이를 확정하지 않는다. 실제 모델 호출은 0회다.

## PR #2 검토 지적의 수정

| 검증 ID | 변경과 확인 |
|---|---|
| MAC-BOM-REQUEST-LIMIT | 모든 행 결정을 함께 보내는 `POST /api/mks2/bom/documents/{bom_id}/revise`만 2 MiB로 제한한다. 다른 경로/메서드는 기존 128 KiB를 유지한다. Content-Length·실제 스트림·불일치·중복/잘못된 길이를 검사한다. 실제 74행 요청은 17,534바이트, 더 긴 독립 합성 한국어 74행은 248,879바이트였다. ASGI 경계 검사 8 PASS |
| MAC-BOM-QUANTITY-FORMAT | 작은 양수 소요·부족을 0으로 반올림하지 않도록 MK-S2 수량 표시를 수정했다. 독립 Node 검사 4 PASS. 실제 원문 수량이나 기대값은 공개 검사에 넣지 않았다 |

2 MiB는 이 검토 경로의 요청 상한이며, 최대 2,000행 각각에 최대 길이 메모를 넣은 모든 조합을 수용한다는 뜻은 아니다. 실제 측정 요청과 독립 합성 큰 요청의 통과, 상한 초과의 거절을 별도로 확인했다.

## 실행 증거와 재현 범위

환경은 Darwin arm64, Python 3.12.14, Docker Desktop 엔진 29.8.2(linux/arm64), PostgreSQL 17.11, Neo4j 5.26.32 Community다. 정확한 실행 파일·보호 입력·runtime·이미지 ID 값과 원본/DB/캡처는 Git 밖에 유지한다. 아래 환경변수 이름과 `<private-runtime>`은 비밀 값을 대신하는 논리 참조다.

| 명령 또는 실행 계약 | 결과 | 범위 |
|---|---|---|
| `dev.build_bom_runner.build(docker_socket, runtime_dir / 'bom-runner.json')` | PASS | 공개 Runner 소스로 immutable 이미지 준비 후 실제 memory/PID/CPU enforcement probe 통과. 상세 환경 결과는 MAC-BOM-RUNNER |
| `.venv/bin/python dev/verify_service.py --runtime-file <private-runtime>` | **182 PASS / 0 SKIP** | 독립 합성 계약·실제 PostgreSQL/Neo4j·Runner 포함 suite. 비공개 환경변수 `PRIVATE_INPUT_ROOT`, `TEST_NEO4J_URI`, `TEST_NEO4J_PASSWORD`, `ONTO_BOM_RUNNER_IMAGE`, `ONTO_BOM_DOCKER_SOCKET` 공급. 실제 원본 성공은 별도 행 |
| `node --test tests/test_quantity_format.mjs` | **4 PASS** | 소수 수량 표시 회귀. 실제 원본 계산 수용과 구분 |
| `node --check dev/check_bom_ui.mjs` | PASS | 브라우저 검사 코드 구문만 확인. 자동 브라우저 suite 결과로 집계하지 않음 |
| `.venv/bin/python -m unittest discover -s tests -p test_request_limits.py -v` | **8 PASS** | 실제 HTTP middleware와 합성 요청. DB·원본·Runner 사용 없음 |
| 실제 `POST /api/mks2/bom/documents` 및 `/{bom_id}/revise` | PASS | MAC-BOM-INTAKE: 제한 Runner로 실제74행 접수·보존, 선택5품목 조건부 결정·69행 보류. 비공개 로컬 HTTP helper 사용 |
| 실제 `/{bom_id}/review`, `/{bom_id}/approve`, `/{bom_id}/post` | PASS | MAC-BOM-APPROVAL / MAC-BOM-POST: 독립 검토·정확 버전/hash·승인과 반영 분리·멱등 반영 |
| 같은 버전 원장·그래프 및 소요 재조회 | PASS | MAC-BOM-READBACK: v2, 176노드/252관계, 선택5품목 산술, PARTIAL/UNKNOWN 유지 |
| CQ68 관련 읽기 전용 확인 | PASS · 제한 범위 | MAC-BOM-CQ68: 약속100·도착32·잔여68·추가 양품 목표40·수율 가정 투입43,선택5품목 조건부 범위,원장/그래프v2·모델 호출0 확인. 전체 CQ20 인수나 납기 보장·전체 자재 확보 검증은 아님 |
| 기존 DB 백업 후 migration 3→4 | PASS | MAC-BOM-MIGRATION: 기존6테이블 동일 필드의 내용/건수 불변, runtime 변경 없음. 이 비교는 업무 반영 전 업그레이드 구간에 한정 |
| Mac 브라우저 수동 확인 | PASS | MAC-BOM-UI: 작성자 A, 원장/그래프v2·서버 연결,176노드/252관계,동일 버전 질의와 전체 스키마 표시. 대시보드5품목·전체 중 일부 범위/가정/합성 재고 표시,전체74행·5조건부/69미검토,POSTED v2 드로어·기술검수 미완료 경고 확인. 기본 목표40으로 복귀. 자동 브라우저 suite 통과 수와 합산하지 않음 |
| 독립 보존 대사 | PASS | MAC-BOM-FINAL-PRESERVATION: 원본155/155·literal349/349·위치1,171/1,171,기존11컨테이너/11볼륨,기존6테이블의 과거 행,보존 화면7파일 byte 동일,runtime 자격증명 hash 동일 |
| 앱 재시작 후 반영 결과 대사 | PASS | MAC-BOM-RESTART: /health200,원장/그래프v2,저장 snapshot의 요약·재고·소요·객체·관계와 POSTED v2 정확 hash·74행 불변. 전체 스키마의 원본hash/버전/개수 유지,T-Box/SHACL RDF 동형성 확인. 재생성되는 개발 신원 revision과 RDF blank-node 식별자는 의미 동등성으로 구분 |
| PR #2 main 병합 | **IN_PROGRESS** | 이 증거 작성 시점 pending. DEC-H26으로 승인된 사전 검증은 통과했으며 최종 병합 상태·merge commit은 [GitHub PR #2](https://github.com/Ontologics-2Zo/ontologics-chat/pull/2)를 기준으로 확인 |

기존6테이블은 `ledger_state`, `ledger_events`, `service_snapshots`, `opening_imports`, `documents`, `outbox`다. migration에서 추가한 nullable `bom_document_id`를 제외한 동일 필드로 비교했다. 이후 승인된 BOM 반영은 새 문서·사건·snapshot·투영과 원장 v2를 추가하므로 그 의도된 변경을 DB 불변이라고 기록하지 않는다. 과거 잔액·원본·snapshot 보존은 독립 보존 검사와 재시작 대사에서 각각 확인했다.

## 남은 범위

T08/T09와 T09.1은 현재 Mac slice에서 Cloud 자원 제한만을 이유로 BLOCKED로 남기지 않고 IN_PROGRESS로 관리한다. T03/T10/T13/T19/T22 부모도 전체 VERIFIED가 아니다. 실제 원본·선택 5품목 조건부 경로의 실행 증거는 전체 제조 기술검수, 4모델·5스냅샷, 구매·입하·수입검사, Agent·영속 복구·관측·실모델, 전체 CQ·T00~T23 인수를 대신하지 않는다. 후속 생산·출하·고객 도착·추적·품질 요구를 유지한다.

현재 질문 처리는 결정적 키워드 분기다. 수율94%의 투입량을 묻는 뜻이 같아도 다른 표현에서는 `UNKNOWN`이 반환될 수 있다. 납기 보장을 묻는 질문은 현재 회차 통계를 반환하는 범위이며 납기 예측·보장 답변을 구현한 것으로 보지 않는다. CQ68 읽기 전용 관측은 위 수량과 범위 표시만 확인한 것이며 전체 CQ20 또는 자연어 표현 범위의 통과를 뜻하지 않는다. 69행의 기술검수·매핑·단위와 실제 현장 재고 근거도 계속 미확인이다.

원문·원본 파생 행/수량표·DB·runtime·키·캡처·상세 로그는 공개 Git/PR/CI artifact로 전송하지 않는다. 이번 승인은 개발 원장에 한정하며 환경 Publish·배포·실제 공급사 송신·운영 원장 변경은 포함하지 않는다.
