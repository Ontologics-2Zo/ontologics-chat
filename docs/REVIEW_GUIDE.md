# 검토 안내

코드 검토·평가를 위한 안내입니다. 기준은 2026-11-13의 `main`(PR #41까지)입니다. 검토 링크에 커밋이 적혀 있으면 그 커밋을 기준으로 봐 주세요.

## 무엇을 만들었나

제조 회차 하나(ORD-26041-01, 약속 100대 중 남은 68대)에 대해 "납기 안에 고객에게 도착할 수 있어?"라는 질문에서 시작해, 고객 도착 100대까지 이어지는 업무를 실제 서버 원장 위에서 처리하는 서비스입니다.

1. BOM 원본 등록 → 검토·승인·원장 반영 → 자재 부족 계산
2. 다른 주문의 예약이 들어오면 부족 수량이 실시간으로 바뀜(23 → 28)
3. 견적을 다시 비교해 고정 기준으로 하나를 추천 → 구매 요청 → 승인·확정
4. 이행 계획을 한 번 승인하면 Agent가 다음 문서 초안을 쓰고 검토자 계정이 승인·확정하는 과정을 반복(발주 → 공급확인 → 입하 → 수입검사 → 생산 → 입고 → 출하 → 도착)
5. 디지털 트윈에서 원장 이력 재생, 프로젝트 그래프에서 온톨로지 논리 검사

사람은 승인과 확정만 합니다. 화면의 숫자는 모두 서버 원장 조회 값이고, 모델이 숫자를 만들지 않습니다.

## 설계에서 지킨 것

| 원칙 | 구현 |
|---|---|
| 확정 원장은 PostgreSQL 하나 | 문서·사건·버전은 `service/ledger.py`, `service/procurement.py`. RDF와 Neo4j는 원장 버전별 투영(`service/projections.py`)입니다. |
| 승인과 반영 분리 | 작성자 ≠ 승인자를 서버가 검사하고, 반영 때 정확한 버전과 해시를 다시 확인합니다. 모든 쓰기는 트랜잭션·멱등키·감사 기록을 씁니다. |
| 온톨로지 검사 | 전체 T-Box(클래스 52, 관계 74, SHACL shape 79)로 버전마다 SHACL 검사를 하고, OWL 2 RL 추론으로 모순과 유형 불일치를 따로 보고합니다(`service/reasoning.py`). |
| 모르는 값은 모른다고 표시 | 미확인 매핑·단위·재고는 0이 아니라 UNKNOWN으로 남깁니다. 가정(예: 계획 수율 94%, 일정 가정)은 원장 사실과 구분해 표시합니다. |
| 모델 사용 범위 제한 | Agent는 허용된 기능 목록 안에서만 움직이고, 모델에는 기능 플래그와 근거 ID만 보냅니다. 수량은 서버가 채우고 검증에 실패하면 원장 답변만 보여 줍니다. |

## 어디부터 보면 되나

| 영역 | 경로 | 보면 좋은 점 |
|---|---|---|
| 화면 | `web/mks2/` | `app.js`(탭·신원·실시간 반영), `dashboard.js`·`procurement.js`(진행 현황·일괄 승인), `twin2.js`(디지털 트윈·이력 재생), `graph.js`·`reasoning_panel.js`(그래프·논리 검사) |
| API | `service/main.py`, `service/procurement_api.py` | 신원·CSRF·버전 확인을 거치는 엔드포인트 |
| 원장·문서 | `service/ledger.py`, `service/procurement.py` | 승인 규칙, 정확 버전 검사, 한 번에 하나만 대기하는 이행 문서 |
| 자재 계산 | `service/materials.py` | 계획 투입·소요·부족 계산(정확한 유리수 올림) |
| 온톨로지 | `service/ontology.py`, `service/projections.py`, `service/reasoning.py` | T-Box 로드, RDF/SHACL 투영, OWL RL 검사 |
| Agent | `service/agent_workflow.py`, `service/agents/` | LangGraph 흐름, 승인 카드, 초안 작성 |
| 질의 답변 | `service/query_intents.py`, `service/query_answers.py` | 질문 해석과 원장 기반 답변 구성 |
| 테스트 | `tests/` | Python 테스트와 Node 화면 로직 테스트 |

## 직접 실행해 볼 수 있는 것

화면 전체를 실행하려면 저장소 밖에 둔 비공개 입력(온톨로지 스키마, 초기 원장, BOM 원본)이 필요합니다. 검토에는 아래 테스트와 별도로 전달하는 시연 영상을 봐 주세요.

Node 테스트(화면 로직)는 준비 없이 돌아갑니다:

```bash
node --test tests/*.mjs
```

Python 테스트는 Python 3.12와 uv가 필요합니다:

```bash
uv sync --locked
uv run pytest -q tests
```

테스트 DB 없이 실행하면 DB가 필요한 테스트는 건너뜁니다. 일회용 PostgreSQL을 띄우면 대부분이 돌아갑니다:

```bash
docker run -d --rm --name onto-test-db -e POSTGRES_PASSWORD=test -p 55499:5432 postgres:17
TEST_DATABASE_URL=postgresql://postgres:test@localhost:55499/postgres uv run pytest -q tests
```

| 실행 조건 | 결과 (2026-11-13) |
|---|---|
| Node | 248 pass |
| Python, 테스트 DB 없음 | 368 passed, 175 skipped |
| Python, 일회용 테스트 DB | 530 passed, 13 skipped (비공개 입력·BOM Runner·로컬 재정렬 모델이 필요한 검사) |

## 측정한 것

| 항목 | 결과 | 기록 |
|---|---|---|
| OWL RL 논리 검사 | 모의 오류 128건 탐지: SHACL+RDFS만 6.3% → OWL RL 추가 99.2%. 버전당 약 0.3초 | [SERVICE-OWL-REASONING-20261112.md](handoff/SERVICE-OWL-REASONING-20261112.md) |
| 정량·정성 평가 | 고정 사례 조회, 실제 기능 선택, 복구 시험, 정성 리뷰 | [SERVICE-EVALUATION-20261110.md](handoff/SERVICE-EVALUATION-20261110.md) |
| 전체 시연 | BOM 미등록(v5)에서 도착 100대(v23)까지 한 번에 진행, 일괄 진행 15개 문서 무중단 | [SERVICE-BATCH-PLAN-20261112.md](handoff/SERVICE-BATCH-PLAN-20261112.md) |

## 범위 밖과 한계

- 개발·시연용 서비스입니다. 배포, 실제 공급사 송신, 운영 원장 변경은 하지 않습니다.
- 데이터는 합성 초기 원장과 실제 BOM 원본 하나입니다. BOM 74행 중 5행만 시연 가정으로 연결했고, 나머지 69행은 미확인입니다.
- 납기 준수 여부는 개별 도착일 근거가 없어 판단을 보류합니다.
- 평가는 모의 오류와 고정 사례 기준입니다. 독립된 사람 정답(gold)이나 실제 업무 효과는 측정하지 않았습니다.
- 공식 원문, 실제 BOM, 평가 정답 같은 비공개 자료는 저장소에 넣지 않았습니다(`AGENTS.md`의 "데이터와 공개 경계").

## 더 읽을 문서

- `AGENTS.md`: 개발 기준과 결정 요약
- `docs/handoff/ARCHITECTURE_BASELINE.md`: 아키텍처 기준
- `docs/handoff/ACCEPTANCE_GATES.md`: 수용 기준
- `docs/handoff/DECISIONS_AND_BLOCKERS.md`: 결정 기록(DEC-H…)
