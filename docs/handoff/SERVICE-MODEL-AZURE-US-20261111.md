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

## 남은 범위

- 온톨로지 질의 화면은 `main`에서 모델을 쓰지 않는다(대시보드 업무 에이전트만 연결).
- 8874 격리 시연은 모델 미연결이다. 연결하려면 기존 예산을 복제하지 않는 방식의 별도 결정이 필요하다.
- LLM은 기능 선택만 하며 답변 본문·수량은 서버 원장 계산이다. 이 기록은 AI 답변 품질 인수가 아니다.
