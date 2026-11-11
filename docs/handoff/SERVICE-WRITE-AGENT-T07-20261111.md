# 쓰기 Agent 승인 흐름 (T07·T14 일부)

상태: **VERIFIED (disposable PostgreSQL 체크포인터 + 8874 격리 시연 v9)** · 업무 승인·원장 반영·발주·공급사 송신과 구분

## 범위

업무 에이전트가 구매 요청(PR) 초안 하나를 제안하고, 요청한 사용자가 그 정확한 제안을 승인하면 초안만 저장한다. 저장 후 문서를 다시 읽어 대사한다. 초안의 검토·승인·확정(원장 반영)은 기존처럼 다른 계정이 화면에서 한다. Agent는 승인·확정·발주·송신 도구를 받지 않는다.

## 구현

- **요청 인식**(`service/agents/procurement_draft.py` `draft_request`): “구매 요청 + 초안/만들어/작성…”을 결정적으로 알아보고 `SUP-X`/“견적 A”/“공급사 A”로 견적을 고른다. 모델은 쓰기 기능을 고르지 않는다. “발주해·승인해·확정해·송신” 등이 섞이면 범위 밖으로 저장 없이 끝낸다.
- **워크플로**(`service/agent_workflow.py`, `WORKFLOW_VERSION=agent-v5`): `route → propose → approve_write(interrupt) → write → reconcile`. 
  - `propose`: 읽기 도구 `procurement.options`만 받아 계획 투입 기준 견적의 MOQ·배수 적용 수량으로 PR 내용을 만든다. 견적 미지정·여러 개면 확인 질문(견적 목록), 만료·공급한도 미충족·승인 권한 계정(작성하면 승인할 계정이 없음)·작성 권한 없음은 저장 없이 종료.
  - `approve_write`: 쓰기 전 LangGraph `interrupt`. 재개는 같은 질문·`proposal_hash`·`decision(approve|decline)`이 정확히 맞아야 한다(`AgentResume` 계약). 재개 시 기존 Host 검사로 신원·권한 정책·고정 원장/투영 버전을 다시 확인하므로 대기 중 원장이 바뀌면 `AGENT_SNAPSHOT_CHANGED`로 쓰기 없이 거부한다.
  - `write`: 쓰기 도구 `procurement.create`만 받는다. 멱등키는 (계정·회사·제안 hash)로 고정해 응답 유실·재시도·같은 제안의 새 실행에서도 도메인이 같은 문서를 돌려준다(중복 초안 없음).
  - `reconcile`: 새 읽기 도구 `procurement.document`로 저장 문서를 다시 읽어 종류·작성자·승인한 내용·기준 원장 버전·payload hash·미반영 상태를 대조한다. 하나라도 다르면 `AGENT_WRITE_RECONCILIATION_FAILED`.
  - 단계 결과는 실행별 불변 artifact로 저장하고 재실행 시 재사용한다. 확인 질문은 직전 단계의 안내(견적 목록)를 우선 보인다.
- **API·관측**: `AgentResume`에 `decision`·`proposal_hash`, 실행 상태에 `document_writes` 수, `execution_kind=langgraph_agent`. 관측 단계 이름에 `propose/approve_write/write/reconcile`. Agent가 저장한 PR 초안도 화면 초안과 같은 시연 신호(`ONTO_DEMO_AUTO_RESERVATION`)를 보낸다.
- **화면**(`web/mks2/app.js`): 업무 에이전트에 승인 카드(제안 문장·견적·수량·공급한도·총액·기준 원장 표, `초안 저장 승인`/`취소`). 승인하면 같은 실행을 재개하고, 저장되면 진행 현황을 새로 읽는다.

## 실행 증거

- Python 514 PASS / 13 skip(disposable `TEST_DATABASE_URL`). 신규 `tests/test_write_agent.py` 8개: 승인 전 쓰기 0·도구 1회·모델 0 → 승인 후 쓰기 1·대사 MATCHED, 거절·잘못된 제안 hash는 쓰기 0, 대기 중 원장 변경 시 거부·쓰기 0, 견적 미지정 확인 질문·공급한도 미충족·승인 권한 계정·범위 밖 요청은 쓰기 0, 확인 질문 재개 후 같은 승인·쓰기, 같은 제안 재시도는 같은 멱등키(문서 1개), 저장 내용 변조 시 대사 실패. 기존 Agent 워크플로·Product 테스트 통과. Node 227 PASS.
- 8874 v9(작성자 A): “견적 A로 구매 요청 초안 만들어줘” → 승인 카드(SUP-A · IM-0016 · 23→30개 · 공급한도 충족 · 기준 v9) → `초안 저장 승인` → PR 초안 30개 저장·대사 일치, 원장 v9 유지, 모델 호출 0, 진행 현황 `검토자 승인 대기`. 이어 시연 자동 예약으로 v10, 카드 `초안 재검토 필요`(기존 규칙).

## 경계

- Agent 쓰기는 PR 초안 한 종류뿐이다. 수정·반려·승인·확정·발주·입하 등 다른 쓰기, 여러 업무의 일반화, 운영 역할, 전체 복구 인수는 남아 있다.
- 사용자의 Agent 승인은 “이 초안을 저장해도 된다”는 동의이며 업무 승인이 아니다. 원래 8870/8872·T-Box·예산은 바꾸지 않았다. 새 main 병합·배포·공급사 송신은 포함하지 않는다.
