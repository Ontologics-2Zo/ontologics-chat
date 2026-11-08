# 충전 후 실제 모델 재연결 기록

## 범위와 승인

2026-11-09 사용자가 충전을 완료하고 연결 재확인을 요청했다(DEC-H30). `codex/agent-foundation`의 `4ece8ad`를 기준으로 제한된 실제 모델 경로를 다시 점검한다. 기존 DEC-H29 예산 `mks2-agent-20261108`의 $10/질문당$0.50/재시도 최대1을 유지하며 사용액·실패 이력·미확정 예약을 초기화하지 않는다. main 병합·배포·외부 업무 실행은 포함하지 않는다.

## 확인된 문제와 수정

- 계정 인증200, credits 충전액$5/검사 전 사용액$0 확인. 앱 예산 $10과 충전액 $5는 별도다.
- Docker Desktop이 수동 일시정지돼 `/health`가 DATABASE_UNAVAILABLE/503이었다. 사용자가 Docker를 재실행한 뒤 같은 DB·runtime으로 health200 복구를 확인했다.
- 기존 OpenRouter adapter는 요청 model alias만 허용해 공식 catalog의 날짜 포함 `canonical_slug` 응답도 거절했다. 선택한 model의 공식 catalog에 있는 정확한 두 ID만 허용하고, 실제 반환 모델과 catalog digest를 기록하도록 수정했다. 임의 prefix나 다른 모델은 허용하지 않는다.
- 첫 실제 요청은 HTTP404였다. `provider.only=openai`와 `zdr=true`의 조합은 현재 OpenRouter 정책상 사용할 수 없는 경로다. 같은 승인 모델 `openai/gpt-6.1-sol`의 **OpenRouter→Azure 단일 경로**로 명시적으로 고정하고 자동 fallback은 계속 끈다. 공개 ZDR 목록의 정확한 모델·Azure·미보관 상태·가격·필수 파라미터가 맞아야 시작한다. Azure가 지원하는 `max_completion_tokens`로 출력 상한을 전달한다.
- 데이터 수집 금지·ZDR·플러그인 없음·가격 상한을 유지한다. 외부 전송은 7개 고정 기능 플래그뿐이며 질문 원문·BOM·원장·비밀은 보내지 않는다. 수량/부족은 여전히 서버 원장 계산이다.

근거: [모델 catalog](https://openrouter.ai/docs/api/api-reference/models/get-models), [ZDR와 OpenAI/Azure 경로](https://openrouter.ai/docs/guides/features/zdr), [공급사 고정과 가격 상한](https://openrouter.ai/docs/guides/routing/provider-selection). 실제 승인 모델의 공개 catalog와 `/api/v1/endpoints/zdr`도 직접 조회했다.

## 실행 증거

- Mac Python 3.12 / 실제 PostgreSQL·Neo4j·Runner, 업무 DB와 분리한 일회성 테스트 DB에서 `.venv/bin/python -m unittest discover -s tests -q`: **286 PASS, 40.360초**. 최신 catalog/alias/ZDR/파라미터 회귀 검사를 포함하며 유료 모델을 부르지 않는다.
- 수정한 코드로 앱을 기존 runtime에 재시작하고 `/health` 200 / `agent.mode=live`를 확인했다.
- 작성자 A의 68대 자연어 질문→실제 모델 기능 선택→동일 서버 원장 조회가 **COMPLETED**. 실제 모델1회/Fake0회/도구1회/업무 쓰기0, 같은 실행 재요청 시 모델·도구 호출 추가0, SSE 진행 이벤트 확인.
- 반환 공급사 **Azure**, 모델 **openai/gpt-6.1-sol** 확인. 공급자가 반환한 토큰은 입력135/출력16/전체151, 비용 **$0.00043**, 해당 성공 요청의 held0/unknown0이다. 공개 credits 조회의 사용액 반영 여부와 별개로 canonical 비용에 이 실제 반환액을 기록했다.
- 68은 남은 고객 도착 약속, 추가 양품40·가정 투입43을 기존 원장 근거로 유지한다. 이 호출은 기능 선택 연결 검증이며 모델이 수량이나 실제 납기를 계산·확정한 것이 아니다.
- 업무 snapshot·BOM74행 및 원장/그래프v2 불변, `.env`·runtime·Runner hash 일치. 기존 DB 초기화/원문 변경 없음.
- 이전 두 실패 요청의 미확정 예약 합계 **$0.010932**는 보존했다. `/health` 누적 실제 요청3건은 전날 실패1 + 오늘 수정 전404 1 + 오늘 성공1이며 성공3건을 뜻하지 않는다.

로컬 Langfuse의 실제 성공 trace **5 spans / generation1개**도 PostgreSQL과 대조했다. 공급사·모델·135/16/151토큰·$0.00043·성공 건 held0가 일치하고, 부모/자식·시간 범위·원문 IO 없음·중복 없음 PASS. 비공개 관측 증거 SHA256: `26fc8f111288a82e582296757dccceb19d56fd3259a054d482f8e85e0db50147`.

최종 비공개 검증 증거 SHA256: `f3b4931c28ba0d5b78087f89e27b3bdde7ee7a642a1f03979444fb8959173ae0`.

현재 앱은 live 모드이며 새 질문에 승인된 제한 모델 경로를 사용한다. `.env` 자체는 수정하지 않았다. 재시작 시 같은 예산 환경을 유지하고 `ONTO_MODEL_MODE=live`를 명시한다. 공개 `.env.example`의 기본값은 Fake로 보존한다.

오늘 수정 전 HTTP404와 전날 실패 요청은 각각 `outcome_unknown`, 비용 null, 예약$0.005466으로 보존한다. HTTP404를 임의로 무과금 확정하지 않는다. 오늘 실패 건은 로컬 Langfuse 3 spans/1 generation과 실제 PostgreSQL attempt를 대조했다. raw IO 없음/부모·자식/시간/미확정 비용 검증 PASS. 비공개 증거 SHA256: `0c7593764d9ef8f263642d89edcc9ad35d8bde975c218dcd8f9903622dc8bc83`.

전체 T04~T07 부모와 T00~T23 전체 인수는 IN_PROGRESS/미완료 상태를 유지한다. 단일 연결 성공을 전체 자연어 품질·전체 CQ·업무 승인 실행 인수로 확대하지 않는다.
