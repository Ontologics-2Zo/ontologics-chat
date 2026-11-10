# 모델 경로 Azure US 고정 · DEC-H54

상태: **VERIFIED (경로 연결·실제 기능 선택 1회)** · 전체 Agent/AI 답변 품질 인수와 구분

## 배경

- 2026-11-10 기본 `azure` 경로가 OpenRouter 공개 ZDR 목록에서 status -5가 되어 8870을 모델 끔 상태로 운영했다.
- 2026-11-11 같은 목록에서 같은 승인 모델의 `azure/us`·`azure/eu`는 status 0, 미보관(`safety_retention=false`), 필수 파라미터(`max_completion_tokens`·`response_format`·`structured_outputs`)를 충족했다. 지역 경로 가격은 카탈로그의 1.1배다.

## 변경

- `service/agent_host.py`: 고정 경로 `ROUTE_TAG = "azure/us"`. ZDR 목록에서 정확히 이 tag 1건만 허용하고, 경로 자체 가격을 반환해 예산 예약과 가격 상한에 사용한다. 카탈로그는 canonical 모델 확인에 계속 사용한다. 요청당 요금·장문 가격 구간 검사는 유지한다.
- `service/model_gateway.py`: 공급사 slug에 지역 tag 하나(`이름/지역`)를 허용한다. 응답 공급사는 기본 이름(Azure)으로 확인한다. `provider.only` 단일 경로·`allow_fallbacks=false`·`zdr=true`·`data_collection=deny`는 그대로다.
- `tests/test_model_gateway.py`: 지역 경로 요청 본문, 잘못된 slug 거부, 기본/다른 지역 tag 거부, 경로 자체 가격 반환, 0·음수·NaN 가격 거부.

## 예산 잠금

- 예산 계정이 `blocked=true`여서 실제 호출 전에 `MODEL_BUDGET_RECONCILIATION_REQUIRED`로 거부됐다. 이 잠금은 코드상 호출 비용이 예약을 넘을 때만 설정되는데, 기록된 live 시도 43건 중 해당 건은 없었고 설정 원인 기록도 없었다.
- 사용자 승인으로 `blocked`만 false로 바꿨다. 결과 미확인 3건의 보류액 $0.016414와 과거 확정 비용은 수정하지 않았다.

## 실행 증거

- 일회성 테스트 DB에서 Python 전체 `unittest discover -s tests`: **468 PASS / 12 skip**.
- 8870 live 재시작: 시작 시 `azure/us` ZDR·가격·파라미터 검사 통과, `/health` ok · `agent.mode=live`. 가져오기·초기화 없음.
- 운영 대시보드 업무 에이전트에서 작성자 A의 68대 질문 1회: 실행 **COMPLETED**, 공급사 **Azure**, 모델 `openai/gpt-6.1-sol`, 토큰 135/16, 비용 **$0.000473**(지역 가격 계산값과 일치), held 0, 선택 기능 `fulfillment_read`. 원장 v4·그래프 v4 불변.
- 잠금 해제 전 같은 질문 1회는 모델 호출 전에 거부되어 비용 0이었다.

## 8874 시연·화면 연결 · DEC-H55

- `--model-budget-runtime-file`/`--model-budget-postgres-port`: 모델 예산 기록을 다른 로컬 runtime의 PostgreSQL에 둔다. 두 옵션은 함께만 허용하고 자신의 포트와 겹칠 수 없다. 그 DB는 migrate하지 않고 `model_budget_accounts`·`model_executions`·`model_attempts` 존재만 확인한다(`MODEL_BUDGET_DATABASE_UNAVAILABLE`).
- 시연 launcher는 live 모드·같은 예산 ID·기존 `.env`(복사 없음)·8870 예산 DB를 사용한다. 관측은 8870과 같다: 로컬 Langfuse 선택 본문 기록, LangSmith 메타데이터만(입력·출력 본문 비움), 자동 SDK tracing OFF. `DEMO_MODEL=off`이면 이전처럼 모델 없이 시작한다.
- 화면: 모델이 켜져 있으면 운영 대시보드·온톨로지 질의·프로젝트 그래프의 질문이 AI 경로를 쓴다. 디지털 트윈은 근거 강조 계약 때문에 결정적 조회를 유지한다.
- AI 경로에서 완료된 고객 도착 답변도 시연 자동 예약 신호를 보낸다(격리 시연 플래그가 켜진 서버만).

### 실행 증거

- Python 전체 **478 PASS / 12 skip**(일회성 테스트 DB), Node **213 PASS**. 공유 예산 DB 기록 분리, 표 없는 예산 DB 거부, 시작 옵션 검증, AI 경로의 도착 답변 신호, 온톨로지 질의 AI 경로·트윈 결정적 조회 회귀를 포함한다.
- 8874 `ready`(v9)에서 작성자 A가 온톨로지 질의에 68대 질문: 모델 호출1회·COMPLETED, 이어 다른 주문 예약 자동 반영(v10, 가용 20→15·계획 부족 23→28), 이전 답변 재검토 표시.
- 같은 상태의 운영 대시보드 질문: 모델 호출1회, 원형 카드(68대 의미·불량률·부족 표 IM-0016 28·일정 가정·견적 4·판정) 표시.
- 두 호출 모두 Azure·$0.000473. 8870과 8874 `/health`의 누적이 같다(46건·$0.019319). 시연을 `start`(v5)로 초기화한 뒤에도 예산 기록은 유지됐고, 8870 원장 v4는 불변이다.

- 관측 확인: 8874 운영 대시보드 질문 1회 → 로컬 Langfuse `ontologics.execution` trace와 `model.route` generation(입력 기능 플래그 7개, 출력 `capability=fulfillment_read`, $0.000473), `/health`의 Langfuse exported 6·LangSmith exported 6·실패0.

## 남은 범위

- 디지털 트윈 질문은 모델을 쓰지 않는다.
- 온톨로지 질의 화면은 원래부터 그래프 표현을 사용한다. 원형 카드는 운영 대시보드 답변에만 표시된다.
- LLM은 기능 선택만 하며 답변 본문·수량은 서버 원장 계산이다. 이 기록은 AI 답변 품질 인수가 아니다.
