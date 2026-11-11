# 일정 What-if 시나리오 (T12) · DEC-H57

상태: **VERIFIED (8874 격리 시연, v9→v10)** · 실제 납기 판단·전체 What-if 인수와 구분

## 배경

일정 역산 카드에서 수입검사·생산능력·출하 준비·운송 기간은 원장 근거가 없어 "근거 없음"이었다. 초기 이관 실적에는 날짜가 없고(`recorded_at: null`), 공식 T-Box에는 기간·능력 속성이 없다. T-Box에는 What-if 구조(`ScenarioRun —usesProfile→ ScenarioProfile —usesAssumption→ ScenarioAssumption`, `ScenarioRun —targetsRound→ DeliveryRound`, `ProductionJob —hasPlan→ ProductionPlan`)가 있으나 인스턴스는 없었다(v9 기준 52 클래스 중 11개만 인스턴스 보유).

## 구현

- **SCENARIO 문서**(`service/procurement_contracts.py` `ScenarioContent`, `migrations/012_scenario_documents.sql`): 회차 하나에 대한 가정 4개(수입검사 영업일, 하루 생산 대수, 출하 준비 영업일, 운송 일수), `provenance=DEMO_ASSUMPTION`. 기존 문서 흐름 그대로: 작성자 작성 → 다른 계정 검토·승인 → 확정(원장 사건 `ProcurementPosted`). 자기승인 금지·정확 버전·멱등·감사 동일.
- **그래프 투영**(`service/procurement_state.py`): 확정 시 `ScenarioRun`(이름 = 시나리오 번호, `assumption_status=APPROVED_ASSUMPTION_NOT_FACT`) → `targetsRound` 회차, `usesProfile` → `ScenarioProfile` → `usesAssumption` → `ScenarioAssumption` 4개(값·단위), 작업지시 → `hasPlan` → `ProductionPlan` → `usesAssumption` 생산능력. T-Box 클래스·관계만 사용하며 T-Box를 바꾸지 않았다. 값은 엔티티 속성(`assumption_key`, `assumption_label`, `value`, `unit`)에 둔다(`label` 속성은 `rdfs:label`과 충돌해 SHACL 위반 → `assumption_label`).
- **일정 역산 카드**(`service/query_answers.py`): 최근 확정 시나리오가 있으면 수량 충족 가장 빠른 견적의 부품 입하 예상일부터 앞으로(검사 → 생산 = 계획 투입 ÷ 하루 대수 올림 → 출하 준비 → 운송) 예상 도착을, 도착기한에서 거꾸로 부품 입하 최소 필요일을 계산한다. 행 상태는 "시나리오 가정 SCN-…", 판정은 계속 "납기 판단 보류"이고 판정 목록에 "가정 시나리오 기준 예상 도착 · 기한 대비 N일 여유/초과 (가정, 실제 판단 아님)"를 더한다. 시나리오가 없으면 이전처럼 "근거 없음".
- **화면**(`web/mks2/procurement.js`): 대시보드 확보 카드 아래 `일정 가정 시나리오` 카드. 없음 → `일정 가정 작성`(기본값 검사 1영업일·하루 20대·출하 준비 1영업일·운송 2일, 수정 가능) → 다른 계정 승인 → 확정. 원장 사건 목록에 "SCN-… 일정 가정" 단계가 보인다. 두 그래프에 `가정·계획`(청록) 색과 한국어 이름을 붙였다.

## 실행 증거

- Python 506 PASS / 13 skip(시나리오 문서 확정·그래프 엔티티·`label` 충돌 방지, 앞/뒤 계산과 기한 초과 사례 포함), Node 221 PASS(시나리오 카드 승인 분리).
- 8874 v9: 화면에서 작성자 A 작성(SCN-0344) → 검토자 B 승인·확정 → v10. 이 첫 시도는 `label` 충돌로 투영 NOT_READY가 되어 수정 후 시연을 초기화했다. 수정 후 API로 같은 흐름 → v10 투영 READY, 그래프에 ScenarioRun·ScenarioProfile·ScenarioAssumption 4·ProductionPlan과 `targetsRound`·`usesProfile`·`usesAssumption`·`hasPlan` 관계.
- 68대 질문 일정 역산: 부품 2026-11-13(SUP-A 5영업일) → 검사 11-16 → 생산 43대 3영업일 11-19 → 출하 11-20 → 도착 11-22, 도착기한 11-27 대비 5일 여유, 부품은 늦어도 11-18 입하. 판정 "자재 부족 23개 · 납기 판단 보류".

## 경계

- 가정값은 사람이 입력하고 다른 계정이 승인한 What-if 값이며 원장 사실이 아니다. 실제 납기 판단·약속 저장은 하지 않는다.
- 영업일은 주말만 제외(휴일 달력 없음). 비교 기준 run(`comparesBaselineRun`)·여러 시나리오 비교·자동 재계획은 미구현.
- 원래 8870 원장은 바꾸지 않았다. 새 main 병합·배포 포함하지 않음.
