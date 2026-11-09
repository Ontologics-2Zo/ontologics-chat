# 제한된 조회 Agent 기반 실행 기록

> 후속 2026-11-09: 충전 후 실제 연결을 재개해 승인 모델의 고정 Azure ZDR 경로를 검증했다. 현재 앱은 live다. 이 문서의 당시 BLOCKED/보류 기록은 보존하며 최신 결과는 [재연결 기록](SERVICE-MODEL-20261109.md)을 따른다.

대상은 `codex/agent-foundation`, 기반 main은 `848fe79`(PR #3 병합)다. DEC-H29에 따라 MD의 남은 기반 작업을 이어간다. **전체 T00~T23 완료가 아니다.** T04~T07 부모는 IN_PROGRESS이며 이번 기록의 읽기 범위만 별도로 판정한다.

## 구현과 데이터 경계

| Task / 검증 ID | 구현 | 보존된 잔여 범위 |
|---|---|---|
| T04 / AGENT-CONTRACT | strict Agent/Model DTO, 기능별 `ledger.query` 제한 포트, 독립 이행·자재 조회 모듈. UI/Agent가 같은 Domain 도구 사용 | 전체 Evidence SDK와 Product/업무 모듈 |
| T05 / AGENT-MODEL, AGENT-OBS | Fake/OpenRouter 경계, PostgreSQL canonical attempt·예산·미확정 예약, metadata-only 로컬 Langfuse OTLP, 중복 계측 방지·관측 실패 격리 | 실제 AI 의미 품질·전체 검색/재시도 관측·LangSmith 비교 |
| T06 / AGENT-GRAPH, AGENT-API, AGENT-UI | 실제 LangGraph 부모/2개 subgraph, 명시 capability의 모델 우회, 허용 기능 선택, SSE 진행·같은 실행 ID | Product 추출/매핑·구매 등 전체 전문 Agent |
| T07 / AGENT-RECOVERY | PostgreSQL Checkpointer·영속 실행/도구 예산·artifact, clarification interrupt/resume, 권한/버전/정책 재검사·동시 실행 잠금 | 업무 쓰기 승인 HITL·원장 반영 대사 전체 |

업무 에이전트는 모델에 질문 원문을 보내지 않는다. 기존 규칙 해석기가 만든 7개 고정 기능 플래그를 모델이 허용 capability로 분류하고, 전문 모듈이 같은 스냅샷의 결정적 원장 계산을 조회한다. 모델은 수량·부족·납기 답변을 생성하거나 승인/반영하지 않는다. 범용 자연어 기능이나 전체 Agent 인수로 확대 해석하지 않는다. 그래프 화면의 기존 직접 질의는 모델 호출 없이 유지한다.

74행 실제 BOM과 선택5품목 조건부 적용, 나머지69행 UNRESOLVED, 합성 초기 재고, 68대 도착 잔여/추가양품40/가정투입43을 유지한다. 실제 납기 가능성은 UNKNOWN이다. 입력·결과 artifact는 로컬 인증 범위의 PostgreSQL에 저장하며 checkpoint에는 opaque 참조만 둔다. 자동 LangSmith tracing은 비활성화한다. 관측에는 원문·질문·BOM·정답·키가 들어가지 않는다.

## 설정과 실행

`.env.example`을 참고하여 키를 기존 `.env`에 보관한다. `.env`는 Git 비추적이고 `--agent-env-file .env`로 명시적으로 읽는다. 쉘 실행·변수 확장을 하지 않으며 `DATABASE_URL`, `NEO4J_*`, `PRIVATE_*`를 Agent 설정으로 반입하지 않는다. 기존 `--runtime-file`이 DB의 유일한 기준이다.

```bash
uv sync --locked --python 3.12
.venv/bin/python -m service --runtime-file "$ONTO_RUNTIME_DIR/runtime.json" \
  --agent-env-file .env --dev-mode --reference-preview --port 8870
```

기존 `PRIVATE_INPUT_ROOT`·Runner 환경을 함께 지정한다. 재시작에 `--import-initial`은 필요 없다. `ONTO_MODEL_MODE=fake`가 기본이다. `live`는 승인된 모델 목록/가격 조회·키·예산·전송 정책을 모두 통과해야 한다. 키 존재만으로 live로 전환하거나 다른 모델로 fallback하지 않는다. `.env.example`은 새 DEC-H29 예산을 구분하며 기존 예산 행의 초기화를 허용하지 않는다. OpenAI 직접 연결과 LangSmith 키는 보관할 수 있지만 해당 비교 adapter/exporter는 아직 실행 경로가 아니다.

DEC-H29 새 범위는 `mks2-agent-20261108`, 한도$10, 질문당$0.50, 전체 질문의 재시도 최대1이다. 과거 사용액은 별도 UNKNOWN으로 남긴다. Fake token/cost는 null이며 실제 호출로 세지 않는다. timeout/결과 불명은 예약을 임의 환급하지 않는다. 비용이 반환되지 않아도 0으로 쓰지 않는다.

## 검증 기록

2026-11-08 Mac Python 3.12 / 실제 Docker PostgreSQL·Neo4j·제한 Runner에서 아래 범위만 검증했다. 대상은 이 기록과 함께 커밋되는 `codex/agent-foundation` 변경이다. 기존 업무 DB와 별도의 일회성 테스트 DB를 만들고 테스트 전용 schema로 검사했으며, 업무 DB의 초기화·삭제는 하지 않았다.

| 검증 ID | 실행 및 결과 | 판정 범위 |
|---|---|---|
| AGENT-CONTRACT / MODEL / GRAPH / API / RECOVERY | `TEST_DATABASE_URL`을 테스트 전용 DB에 연결한 `.venv/bin/python -m unittest discover -s tests -q`: **280 PASS, 44.674초** | typed ports·예산·권한·동시성·취소·영속 checkpoint·독립 프로세스 강제 종료/재개. 모델 adapter는 Fake/HTTP 대역 검사이며 실제 모델 성공 증거가 아님 |
| AGENT-UI | `node --test tests/test_query_ui.mjs tests/test_quantity_format.mjs`: **12 PASS** | 수량/가정/미확인 및 실행별 모델 호출 표시 |
| AGENT-API | Mac 8870에서 자동 이행/자재 조회 및 명시 기능 선택 **3 PASS**, 동일 요청 재실행·SSE 확인 | Fake 기능 선택 2회, 명시 선택 모델 우회. 도구 결과는 서버 원장 조회 |
| CQ-REGRESSION | 기존 직접 질의 **9 PASS** | 남은 약속/양품/투입/자재/납기/What-if·쓰기 거절·구버전409, 모델 호출 및 업무 쓰기0 |
| AGENT-OBS | 실제 로컬 Langfuse에서 통합 Fake **14 spans / generation 2개** 조회 | 부모·자식/시간/실행·attempt 대조, 원문 IO 없음, Fake 비용/토큰을 꾸미지 않음 |
| AGENT-OBS-BLOCKED | 실패한 실제 요청의 **3 spans / generation 1개** 조회 | 실패·미확정 사용량·보존된 예약을 canonical 원장과 대조. 실제 모델 성공 검증은 BLOCKED |
| AGENT-UI-MAC | 작성자 A, `도와줘` clarification 대기→기능 선택→같은 실행 재개, 새 자연어 68대 질문 완료 | Fake 기능 선택1회 표시, 잔여68/추가양품40/가정투입43·합성/시연 근거 구분. 그래프/대시보드 정상 조회 |
| AGENT-PRESERVE | 최종 `/health` ok, `agent.mode=fake`; 업무 snapshot·BOM 비교, 원본 무결성 재검사 | 실제 BOM74행·선택5품목·미해결69행, 원본155개, 전체 ontology52/74/79, 참조7파일, 기존 runtime/Runner/`.idea`·컨테이너/볼륨 보존. 원장/투영v2 불변 |

검증 과정에서 신규 migration005/006과 Agent/checkpoint 테이블만 추가했다. 시작 전 PostgreSQL 백업을 비공개 runtime에 남겼다. Langfuse의 기존 DB 호스트 포트 충돌은 기존 볼륨을 읽기 전용으로 복제한 별도 내부 DB로 해결했으며 기존 DB 컨테이너/볼륨은 삭제하지 않았다. 관측용 추가 자원은 비공개 자원 목록에 기록했다. 공개 후보 파일의 실제 자격증명·개인 경로 검사는 누출0이다.

비공개 실행 증거는 Git 밖에 보존한다. 공개 대조용 SHA256:

- Fake 관측: `95b1e1df051d1f571d926f9de01f48867460987a92ab79c148bde54b8cec20aa`
- 실패한 실제 요청의 관측: `ac42edae792ba69dfd97a31daf6b7f590e050a26635a7d74cf9a82d8d1268678`
- 최종 원본 무결성 검사 결과: `9f1c86d3b122860682b5a78e3b9e395aaf4398ffc0cd1956d746cad327eaadea`

## 실제 모델 보류와 재개 조건

OpenRouter 인증·모델 목록 조회는 성공했으나 실제 모델 요청1회는 provider 오류로 실패했다. 계정 credits 조회는 충전액0/사용액0을 반환했다. 이 조회값만으로 해당 실패 요청의 실제 비용을 0으로 확정하지 않는다. canonical attempt는 `outcome_unknown`, 사용량/비용 null, 예약 **$0.005466**을 유지한다. `/health`의 누적 `model_calls=1`은 이 실패한 요청 이력이며 현재 모드는 Fake다. 성공한 실제 모델 응답과 의미 품질 검증은 **BLOCKED**다.

사용자가 “다음에 충전하고 이후 사용”하기로 결정했으므로 실제 모델 요청을 중단하고 Fake로 재시작했다. 이후 새 공급자 요청·OpenAI 우회·충전은 수행하지 않았다. 기존 `.env`는 수정하지 않았다. 사용자가 충전 완료 후 재개를 요청하면 잔액·미확정 예약·기존 예산을 확인한 뒤 live를 명시적으로 켜고 제한된 실모델 검증을 재개한다. `.env.example` 기본값은 계속 Fake이며 키만 넣어도 유료 호출이 켜지지 않는다.

새 변경의 main 병합은 수행하지 않았다. 선행 PR #3 main 병합 상태와 이번 Agent 브랜치 상태를 구분한다.

## 다음 작업

T11 권한 내 Hybrid RAG와 T12 Product 추출·매핑을 공통 계약에 연결한 뒤 T13 예약·수급/T14 견적→PR/PO→입하→수입검사→가용 재고로 이어간다. 실제 제조 기술검수·전체 BOM·납기 근거는 사용자 자료와 별도 검토가 필요하다. T15~18 생산/도착/추적/클레임과 T20~23 비교·규모·전체 인수는 삭제하지 않는다.

구현 근거: [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [Langfuse OpenTelemetry](https://langfuse.com/integrations/native/opentelemetry), [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection). 실제 설치 버전은 `uv.lock`과 실행 기록을 기준으로 한다.
