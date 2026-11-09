# MK-S2 정량·정성 기준선 측정

2026-11-10 · DEC-H42 · T22.1 부분 실측. **전체 CQ20/110·실제 AI 답변 품질·B0~B5·규모·업무 효과 인수는 미완료다.**

## 기준과 대상

사용자의 평가 요청에 따라 `ACCEPTANCE_GATES.md` M-01~12, `IMPLEMENTATION_TASKS.md` T20~23, 비공개 SRC-AI 지표/평가 계약과 공식 v1.7 개발 설계 §10을 확인했다. 기존 CQ20의 5유형100개와 확장 후보10개는 계약110개이며 실행110건이 아니다. 독립 승인 gold는0건/PENDING이다. 과거 offline 후보30건 검사나 테스트 assertion 수를 실제 AI 정확도 분모로 합치지 않았다.

서비스 대상 코드는 `0dce620fecda0d069bf3184a3b4cdad26a2739e4`다. 서비스/UI 코드는 이번 평가에서 변경하지 않았다. 공개 공통 측정기는 `dev/evaluate_service.py`, 합성 검사는 `tests/test_evaluation.py`에 추가했다. 측정기 SHA-256은 `856a768c05d4492c7f80da272d0cfeb37d3edd3c17646e24081ff2c995f5edaa`다. 평가 질문·기대값·응답·개별 점수·trace·DB dump·인증 정보는 Git/Docker/CI 밖에만 보관했다.

환경은 Mac arm64, Darwin25.6.0, CPU10개, RAM64GiB, Python3.12.14, PostgreSQL17/Neo4j5.26이다. 원래 원장v4의31테이블을 읽기 전용 exported snapshot으로 복제하고, 전용 Neo4j에 v1~v4를 재투영했다. v4는482노드/704관계이며 10만 core 객체 측정이 아니다. 현재 활성 BOM은74행 모두 적용 미확인이고 계산 품목0개다. 과거5품목의 승인 결과를 현재 답으로 사용하지 않았다.

- 직접 조회: 격리 복제 앱, 모델 disabled, 동시성1, 사례24개, 사례별 warmup1회 제외 후5회씩120회. 문서의 수량·보류 계약을 개발자가 사전 고정한 검사이며 독립 사람 gold 기반 정확도가 아니다.
- 실제 Agent: 원래 앱의 같은 canonical US$10 계정, 동시성1, 기능 선택7유형×2회=14회와 모델 호출 전 보류2유형×2회=4회. 추가 재시도0·fallback OFF. 같은 승인 모델/Azure ZDR 경로이며 모델에는7개 허용 기능 플래그만 전송했다. 질문·원장·BOM·gold는 외부 모델에 보내지 않았다.
- 정성 검토: 출력 열람 전5축×1~5 행동 기준 고정, 사전 지정8개 사례의 첫 measured 응답 검토. **개발 협력 AI 리뷰이며 독립 사람 평가나 외부 LLM Judge가 아니다.**
- 지연: monotonic client dispatch→전체 HTTP body 수신, nearest-rank `ceil(p×n)`. UI 문자 표시/그래프 재생/사용자 대기는 제외했다. 서버 전용 시간·TTFT·provider streaming 시간이 아니다.
- 직접 조회는 복제 준비와 live 호출이 끝난 뒤 측정했다. live의 초기 표본은 같은 Mac의 복제 준비와 겹쳤다. 두 사용자 서비스가 가동 중인 개발 환경의 작은 기준선이며 엄격한 paired 비교·부하 SLO 판정이 아니다. CPU/RSS peak, cold-cache 성능, 동시 부하, 개선율은 측정하지 않았다.

## 정량 결과

| 측정 | 결과 | 해석 |
|---|---|---|
| 사전 고정 조회 계약 | 고유24/24, 반복120/120 일치 | warmup24 제외. 수량68/추가양품40/94%계획투입43·What-if·미확인 BOM·대상/수량/버전/쓰기 요청의 기대 판정 |
| 직접 조회 전체 P50 / P95 | 295.84 / 334.39ms | 예상 거부 응답10회도 지연 분모에 포함 |
| ANSWERED 응답 P50 / P95 | 301.91 / 334.61ms,35회 | 상태 자체와 평가 계약 통과는 별개 |
| PARTIAL/UNKNOWN/확인 필요 P50 / P95 | 298.53 / 335.42ms,75회 | 올바른 보류를 업무 완료로 세지 않음 |
| 예상 HTTP 거부 P50 / P95 | 3.39 / 260.31ms,10회 | 과거 버전409·유효하지 않은 버전422. 예상 밖 HTTP/통신 실패0 |
| 버전·근거 참조 정합성 | HTTP200인110/110 응답 | 원장/투영v4·snapshot 일치, 노드/관계/경로가 같은 그래프에 존재. 실제 근거 참조가 있는9유형45응답 포함. 의미적 Faithfulness와 다름 |
| 실제 모델 기능 선택 | 7유형×2회=14/14 일치 | 세 capability enum의 선택만 평가. 답변 수량/서술을 LLM이 생성한 결과가 아님 |
| Agent 전체 계약 | 9유형×2회=18/18 일치 | 모델14회, 읽기 도구12회, 보류6회·도구0. 발행 도구의 전체 인수/순서 의미 평가를 대체하지 않음 |
| 모델 사용 Agent E2E P50 / P95 | 7.425 / 10.650초,14회 | create 요청 직전→run 최종 응답. 보류를 포함한 전체18회는7.198 / 10.650초 |
| 모델 dispatch→accounting finish P50 / P95 | 2.503 / 5.807초,14회 | provider transport만의 시간이 아니며 기록 확정 과정 포함 |
| 실제 모델 usage | input1,890 / output279 tokens | 현재 run14회 모두 provider usage 확인, 새 미확정 usage0 |
| 이번 비용 / 올바른 기능 선택당 비용 | US$0.00657 / 약US$0.0004693 | 이번 run 실패·재시도0. 운영 업무 완료당 비용과 다름 |
| 경계 검사 | 거부7/7, 정상1/1 | 인증 누락·CSRF·오래된 신원·다른 origin·임의 actor 필드·다른 회사·비활성 계정. 실제 허용0, 정상 오차단0. 전체 보안 공격률로 일반화 금지 |
| 중단 복구 | 2개 고유 fault×3회=6/6 | 실제 별도 프로세스 종료·재시작, 같은 실행/중복 도구 효과 없음. 복구 latency는 미측정 |

복구는 기존 `tests/test_agent_workflow.py`의 실제 프로세스 중단 계약을 재사용했다. artifact commit 후 exit86은 도구 호출1을 유지하며 COMPLETED/segment2로 복구했다. interrupt 후 WAITING 기록 직전 exit87은 새 세션에서 WAITING/tool0을 복구하고 같은 execution에서 COMPLETED/tool1/segment3으로 재개했다. 업무 승인 HITL 전체 인수는 아니다. 전용 PG의 UUID schema만 사용했고 모델/키/trace 설정을 상속하지 않았다. 복제 public31테이블/1,187행 전후 hash와 테스트 schema0→0을 확인했다.

## 정성 결과

8/8사례를 채점했고 UNSCORED0, 총40개 축 점수의 평균은 **3.925/5**다. 점수는 서술적 기준선이며 사후 합격선이나 사람 만족도로 사용하지 않는다.

| 사전 고정 축 | 평균 / 5 |
|---|---:|
| 목표 이해 | 3.875 |
| 필요한 확인 질문 | 4.500 |
| 제조 용어·수량 의미 | 4.000 |
| 근거 설명 | 3.750 |
| 보류·다음 행동 적절성 | 3.500 |

관측한 개선점은 다음과 같다. 이번 측정 대상 코드는 유지했고, 수정 후에는 같은 계약으로 다시 측정해야 한다.

1. **확인 범위 과장1건:** 현재 적용 품목0·전체 적용 미확인인데 상세 conclusion이 조건부 부족을 확인했다고 요약한다. 본문/구조화 수치는 미확인·납기 보류를 유지하지만 요약 문구는 그보다 강하다. `service/query_answers.py`의 납기 결론 분기는 실제 계산 가능 품목/미확인 상태를 구분해야 한다. 고정 수량 검사 PASS가 이 표현 오류까지 검출했다는 뜻은 아니다.
2. **핵심 결과 우선:** 재고 등의 직접 질문에서는 숫자와 대상이 상세에 있고 본문은 계산 원칙 위주다. 현재 원장 목표와 별도 What-if 목표도 첫 문장에서 구분하면 이해가 쉽다.
3. **다음 행동 구체화:** 보류 원인은 명시하지만, 현재 부족한 자료 중 무엇을 먼저 확인할지와 대상 선택 방법을 더 구체화할 여지가 있다.

8개 검토에서 실제 납기/업무완료를 허위로 확정하거나 업무 쓰기를 실행한 중대 안전 위반은 관측0이다. 이를 모든 오류 확률0이나 서비스 품질 통과로 일반화하지 않는다. 확인 범위 과장1건은 위 별도 결함으로 유지한다.

## 지표별 측정/미측정

| 지표 | 이번 범위 | 아직 측정하지 않은 것 |
|---|---|---|
| M-01 정확성 | 문서 기반 고정 조회 계약24개·기능 선택14회 | 독립 사람 gold/CQ20 전체/LLM 원답·제공답 정확도. 정성 표현 오류를 계약 PASS로 덮지 않음 |
| M-02 Faithfulness | 버전·근거 참조 정합성110응답 | 주장별 의미적 지원율; 실제 근거 존재와 의미적 지지는 다름 |
| M-03/04 검색 P/R | NOT_MEASURED | 고정k·독립 필수 근거 gold가 필요. 반환 건수로 precision 분모 대체 금지 |
| M-05 매핑 P/R/F1 | NOT_MEASURED | 독립 사람이 검수한 품목/관계 매핑 gold. SHACL 통과는 대체 지표가 아님 |
| M-06 목표 완료율 | NOT_MEASURED | 실제 업무 후조건과 적격 업무 표본. 올바른 보류/단순 조회를 제조·조달 완료로 세지 않음 |
| M-07 도구 정확성 | 기능 선택과 기대 호출 수의 제한 검사 | 전체 tool 인수·순서·의미 결과·누락/불필요 호출의 독립 평가 |
| M-08 정책 | 경계7개/정상1개 실측 | 전체 Prompt Injection·데이터 유출·업무 승인·보안 제거 실험 |
| M-09 복구 | 두 명시 중단 시나리오6회 | 전체 장애 유형/업무 쓰기 멱등 복구·복구 latency |
| M-10 지연 | 위 P50/P95와 실패·보류 분리 | TTFT, 사용자 대기, cold/warm 비교, 부하·10만 객체·CPU/RSS peak |
| M-11 비용 | 현재14회 기능 선택의 확정 비용 | 완성된 업무 성공당 비용·다른 provider/model 비교·Judge 비용 |
| M-12 업무 효과 | NOT_MEASURED | 실제 담당자의 paired 작업시간·오류·재처리·만족도 |

기존 B0~B5 및 EX-CP/ROUTE/LOOP/CONTEXT/RAG/PROVIDER/SEC/ONTOLOGY 계약은 보존한다. 이번은 **단일 경로 기능 선택과 기존 checkpoint 복구의 부분 측정**이며 paired 기술 효과 비교가 아니다. CQ110 API 연결·독립 gold/holdout·사람 평가·전체 기능/규모 선행이 남아 있어 T22.1/T22 및 전체1차 인수를 완료 처리하지 않는다. 연속 지표 합격선은 기존 OPEN-H10처럼 사전에 별도 고정해야 한다.

## 예산·관측·보존

같은 예산의 누적 확정 사용액은 **US$0.01214**, 기존 미확정 예약은 **US$0.016414**, 예약까지 차감한 잔여 한도는 **US$9.971446**이다. 기존 실패/미확정 기록을0으로 고치지 않았고 새 계정을 만들지 않았다. 평가 복제본의 과거 예산 기록은 dump의 이력으로 보존했으며 Agent disabled·키 없음·새 모델/예산 기록 없음이다.

원래8870의31테이블 기존 모든 행과 업무 snapshot/BOM/전체 그래프는 보존됐고, 허용한 평가 Agent·모델·인증 기록만 추가됐다. INITIAL8872의27테이블 전체·그래프·설정·모델0은 그대로다. 원본155개, 보호 화면7개, 전체 기존web, 제공 참조11개, 기존 사용자 파일9개도 보존됐다. 원래 두 앱은 초기화/재시작하지 않았다.

평가 복제본은 업무29테이블/기존 행이 그대로이며 경계 검사에 필요한 인증 session/audit 각1행만 추가됐다. 이 인증 효과를 전체 테이블 불변으로 보고하지 않는다. 복구 테스트의 UUID schema는 모두 정리됐고 dump/증거/volume은 보존한다.

평가 앱과 정확히 소유한 PG/Neo4j 컨테이너2개만 종료했다. 삭제는0이며 컨테이너2개·전용 volume3개·dump/증거를 보존했다. 기존16개 컨테이너/21개 volume 상태와 두 사용자 앱 PID는 그대로이고 `/health`는 둘 다200이다. 원래 앱에는 평가 로그인/로그아웃 각1건과18execution/14model 기록만 허용 범위로 추가됐다.

로컬 관측 receipt에서 평가18 execution의 metadata span74/74와 모델 generation14/14가 `exported`임을 확인했다. 로컬 OTLP 수집 응답 성공이며 Langfuse UI/저장소 재조회 검증이나 LangSmith 수신 증거로 확대하지 않는다. 원문/답변/정답의 외부 tracing은 하지 않았다.

## 재현과 증거

공개 공통 측정기는 원래 서비스8870을 거부하고 loopback·명시적 격리 확인·일치하는 인증 origin·사전 계약 hash를 검사한다. `--isolated`는 작업자 확인이며 격리 증명 자체가 아니므로 위 복제/전용DB 증거를 함께 확인해야 한다. 질의 endpoint 외의 모델/업무 API, redirect/proxy, 임의 executable assertion은 사용하지 않는다. 응답이 결정적 조회·모델0·업무변경0 계약을 벗어나면 즉시 중단한다. 개인 입출력은 신규0700 디렉터리/0600 파일이며 stdout은 건수만 출력한다.

실행한 CLI의 비식별 표현은 다음과 같다. 아래 변수의 실제 값과 인증 파일은 비공개 실행 증거에 있다.

```sh
.venv/bin/python dev/evaluate_service.py \
  --base-url "$EVAL_ORIGIN" --isolated \
  --cases "$EVAL_CASE_FILE" \
  --expected-cases-sha256 bd74d00be7999891b77320be3a9b7a395dea9268fc3495e72f5600a248079633 \
  --auth-file "$EVAL_AUTH_FILE" --output-dir "$EVAL_NEW_OUTPUT" \
  --warmup 1 --repetitions 5
```

합성 MockTransport 검사12건은 수치 정밀도/null·missing, nearest-rank/실패 포함/warmup 제외, 사전 계약/인증/경로 경계, private 보관, 모델/업무변경 계약 위반 중단을 검증했다. 실제 API 성능 표본과 합산하지 않았다.

실행 명령은 `.venv/bin/python -m unittest discover -s tests -p test_evaluation.py -v`이며12 PASS다. 복구는 `.venv/bin/python -m unittest tests.test_agent_workflow.AgentWorkflowTests.<method> -v`를 위 두 프로세스 중단 method에 각각3회 실행했다. `TEST_DATABASE_URL`은 비공개 복제 설정을 읽은 부모 프로세스가 자식 환경에만 주입했으며 명령줄/공개 로그에 DSN을 넣지 않았다. 공통 측정기/합성 테스트의 독립 읽기 검토 추가 blocker는0이다.

사전 설정 오류도 보존했다. 최초 live 로그인은 잘못된 요청 헤더로403이며 모델0회였다. 헤더를 서비스 계약에 맞춘 뒤 고정 사례/기대값을 바꾸지 않고 실제 run을 시작했다. 최초 조회 harness 호출은 인증 파일의 추가 필드 때문에 네트워크 전 거부됐고 허용4필드의 별도 사본으로 실행했다. setup 실패를 모델/조회 성공 표본으로 세지 않았으며 원시 기록은 보존했다.

| 증거 ID | SHA-256 / 범위 |
|---|---|
| EVAL-CONTRACT-24 | `bd74d00be7999891b77320be3a9b7a395dea9268fc3495e72f5600a248079633` — 24사례/5반복/1warmup/조건 사전 고정 |
| EVAL-RUBRIC-5 | `1f7e3fc41344997d312d63b90fbf62b08cbd477b08d2c548c923b0d2bcdd028c` — 응답 열람 전5축 기준 |
| EVAL-QUALITATIVE-8 | `3882df8608951dc4ef26fe6ebe4df283488c8d3134d0c59a42e8da6edc6a6296` — 개발 협력 AI 리뷰8사례/40축 점수 |
| EVAL-QUERY-120 | `05aeb487f4c189a546ea743dc71502ed7598223f05da795cbfdb2039575fa5ea` — warmup24+measured120 원시 응답 연결 |
| EVAL-LIVE-14 | `596548272c8915bdb1b1522286a53f0ffac9efbb886f84beba622d4af62badcd` — 실제14회/무모델4회 집계 |
| EVAL-LIVE-ATTEMPTS | `7d5f60088caf6f193d236eba92201f1071869f0bdee0e5e578364a034d26c761` — canonical14 attempts/usage |
| EVAL-LINKS | `6cabaa1c1095844de6ffd386507724a2b2c9b9478d1915b1eba6288fe5f38599` — 동일 그래프·버전 참조 |
| EVAL-BOUNDARY-8 | `dabb16b379d9e0ebd1dc373af454752de030c771ba2681de4741af3171318024` — 실제 거부7/정상1 |
| EVAL-RECOVERY-6 | `c6ba798e01d63b1951783e87220597e7fa92233e5896cd696344d8b5824f04b9` — 고유2fault/반복6 |
| EVAL-CLONE-READY | `d0d10ce5257454f219558512ba00579e6c7e4d5cdb8cd5c387a797ff17e69cc3` — 독립 복제/투영/키 제외 |
| EVAL-PRESERVATION-FINAL | `f2912ce0827cfdc085248f20061d63dce3c15f63197d33f4df93d38de445184e` — 양쪽 보존/관측/예산 및 평가 환경만 종료 |

원시 결과/정답/질문/trace는 공개하지 않으며 위 식별자와 검토된 비식별 집계만 PR #5에 반영한다. main 병합·배포·운영 업무 쓰기·전체 서비스 완료는 포함하지 않는다.

## 후속 수정 기록

DEC-H43의 [PR #5 검토](SERVICE-PR5-REVIEW-20261110.md)에서 위 확인 범위 과장1건을 수정했다. 같은24사례에 미확인 결론 검사를 추가해24/24 회귀 통과했다. 이 후속 결과는 당시3.925/5 점수와 지연·비용 기준선을 변경하거나 정성 전체 재평가로 대체하지 않는다.
