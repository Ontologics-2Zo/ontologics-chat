# 대체 후보 적용 승인 (T13 일부)

상태: **VERIFIED (8874 격리 시연, v9→v11)** · 제조 기술검수·품목 동일성 확정·대체 품목 입고/불출/장착과 구분

## 배경

초기 이관의 대체 후보(예: IM-0016 → IM-0017, 후보 보유 120)는 모두 `NOT_REVIEWED`, 이번 수요 인정 0이었다. 승인하는 절차가 없어 What-if(T12)에서 “대체품까지 쓰면”이라고 물어도 항상 “승인 전이라 제외”로만 답했다. 공식 T-Box에는 대체 전용 클래스·관계가 없다(126개 클래스·관계 확인).

## 구현

- **SUBSTITUTION 문서**(`service/procurement_contracts.py` `SubstitutionContent`, `migrations/014_substitution_documents.sql`): 회차·원래 품목·후보 품목·인정 수량, `provenance=DEMO_APPLICATION_APPROVAL`. 기존 문서 흐름(작성 → 다른 계정 검토·승인 → 확정)·자기승인 금지·정확 버전·멱등·감사를 그대로 쓴다.
- **서버 검사**(`service/procurement.py`): 원장에 기록된 후보만, 같은 단위, 인정 수량 ≤ 후보 보유 − 이미 인정한 수량, 그리고 ≤ 원래 품목의 현재 계획 부족. 오류 코드 `SUBSTITUTE_CANDIDATE_UNCONFIRMED`·`SUBSTITUTE_EXCEEDS_CANDIDATE_STOCK`·`SUBSTITUTE_EXCEEDS_SHORTAGE`·`SUBSTITUTE_SHORTAGE_UNKNOWN`.
- **확정 반영**(`service/procurement_state.py`): `current_substitutes`의 해당 후보를 `APPROVED`, 인정 수량 누적, `approval_scope=DEMO_APPLICATION_NOT_TECHNICAL_REVIEW`. 재고 행·초기 이관 사실은 바꾸지 않는다. 그래프에는 T-Box를 바꾸지 않고 `SourceDocument`(속성: 원래·후보 품목, 수량) → `supportedBy` → `Evidence`, 문서 버전·승인만 투영한다.
- **부족 계산**(`service/materials.py`): 품목 가용 = 재고 가용 + 확정된 대체 인정 수량. `stock_available_qty`·`substitute_qty`를 따로 남긴다. 대시보드·답변·구매 요청 검사·예약 영향(`service/reservations.py`, 현재 승인 대체 포함 기준)이 같은 값을 쓴다.
- **답변**(`service/query_answers.py`): 68대 답변·부족 카드에 “재고 가용 N + 승인된 대체 M”을 밝힌다. What-if의 “대체품 포함”은 이미 반영된 수량을 다시 빼지 않고 남은 후보 재고는 추가 승인 전이라 제외한다. 부족 품목이 없으면 회차 대상 품목으로 비교한다. 대체 후보 조회는 승인 수량이 시연 적용 승인이며 기술검수가 아님을 말한다.
- **화면**(`web/mks2/procurement.js`): 진행 현황에 `IM-0016 대체` 줄. 작성자 A의 한 번 클릭 `대체 승인 요청 · IM-0017 N개`(N = min(현재 부족, 남은 후보 재고)) → 배너 `검토자로 전환` → B 승인·확정. 반려된 요청은 제외하고 다시 요청할 수 있다. 추천 질문은 예약 전 부족이 0이면 “기존 0개 안” 대신 견적 비교를 보인다.

## 실행 증거

- Python 505 PASS / 13 skip(disposable `TEST_DATABASE_URL`): 대체 승인 도메인 검사(부족·후보 재고 한도, 다른 후보 거부, 자기 검토 금지, 확정 후 부족 감소·재고 불변·그래프 문서), 답변의 대체 포함 설명, What-if 이중 차감 없음, 예약 영향의 대체 포함. Node 227 PASS(`tests/test_substitute_lane.mjs`: 후보 없음 숨김, 한 번 요청 수량, 승인자 작성 금지, 승인 분리·배너, 확정 후 인정 표시, 반려 후 재요청).
- 8874 v9: A `대체 승인 요청 · IM-0017 23개` → 배너로 B 전환 → 승인 → 확정 → v10 투영 READY, IM-0016 재고 가용 20 + 대체 23 = 43, 부족 0, 대시보드 `부족 없음 · 가용 43 / 필요 43`. 이후 시연 자동 예약(다른 주문 5개) → v11 부족 5, 68대 답변 “예약으로 0→5개”, 대체 줄 `대체 승인 요청 · IM-0017 5개`.

## 경계

- 시연 적용 승인이다. 제조 기술검수·품목 동일성·실장 확정이 아니며, 대체 품목의 LOT 입고·불출·장착(생산 실적의 LOT 소비)은 기록하지 않는다. 생산 실적은 여전히 원래 품목 LOT로만 기록한다.
- 대체 승인 취소·인정 수량 축소는 미구현. 원래 8870/8872 원장·T-Box는 바꾸지 않았다. 새 main 병합·배포·공급사 송신은 포함하지 않는다.
