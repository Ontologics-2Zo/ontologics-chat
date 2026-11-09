# PR #5 최종 검토 · 로컬 관측과 연결형 근거 그래프

2026-11-10 · DEC-H43. 사용자는 로컬 Langfuse에서 가능한 실행 입출력을 확인하고, 업무별 층/격자를 연결형 그래프로 바꾸며, 검증 후 PR #5를 main에 병합하도록 승인했다. 기존 파일·DB·예산·보존 화면을 유지한다. 전체 1차 인수나 실제 납기 확정을 뜻하지 않는다.

## 변경과 경계

- 디지털 트윈, BOM 최초 등록 동선, 서버 답변의 점진 표시와 정량·정성 기준선은 PR #5의 기존 변경으로 유지한다. `web/` 직속 보호7파일과 비공개 제공 화면은 수정하지 않는다.
- `web/mks2/graph*`는 실제 관계 기반의 고정 연결 배치를 사용한다. 원문 행·BOMLine·문서/승인 이력은 한 BOM 군집으로 묶되 노드/관계 ID를 합치거나 삭제하지 않는다. 종류 버튼은 강조만 전환한다. 질문은 좌표를 바꾸지 않으며 근거의 핵심 관계마다 카메라를 이동하고 대량 BOM 관계는 한 번에 강조한다.
- 현재 활성 BOM의 적용 품목이 미확인일 때 납기 결론이 부족을 확인했다고 쓰던 표현을 수정했다. 계산 가능한 부족값이 없으면 `자재 소요·부족 미확인 · 납기 판단 보류`로 요약한다. 알려진 부족0과 UNKNOWN을 구분한다. 과거 평가 점수/수치는 재작성하지 않는다.
- 관측의 기본값은 metadata만이다. `ONTO_LANGFUSE_CONTENT_ENABLED=true`와 `ONTO_OBSERVABILITY_PURPOSE=interactive`를 명시한 로컬 실행에서만 질문·공개 답변·확인 요청을 기록한다. 질문/초기질문 각2,000자, 답변6,000자, 결론/확인요청 각2,000자로 제한하며 키·인증문자열·로컬 경로를 마스킹한다. BOM 파일/셀/첨부 원문·평가 gold는 기록 대상으로 추가하지 않는다.
- 모델 observation은 **실제 모델에 전달한7개 기능 플래그와 호출 설정, 선택 capability, 실제 canonical usage**를 기록한다. 사용자 질문/원장 답변이 모델 prompt/response였다고 표시하지 않는다. 외부 모델 전송 범위·동일 예산·Azure ZDR 단일 경로·fallback OFF는 그대로다.
- Langfuse는 기존 로컬 loopback 수집기로만 보낸다. LangSmith/외부 trace 연결을 새로 활성화하지 않았다. 완료·실패·취소의 immutable terminal root에 trace input/output을 한 번 기록한다. **WAITING 중에는 개별 observation에서 질문/확인 요청을 확인하며, terminal 이전 trace 목록의 요약은 비어 있을 수 있다. 과거 빈 기록은 보충하거나 재작성하지 않는다.**
- 평가 host는 `ONTO_OBSERVABILITY_PURPOSE=evaluation` 또는 content=false로 실행한다. 평가 목적은 본문 기록을 강제로 막는다. 현재 per-execution 평가 판별은 없으므로 content=true interactive host에 비공개 평가 질문을 보내지 않는다.

## 검증

검증 대상은 `codex/digital-twin-db`의 PR #5 최종 변경이다. 최종 commit/병합 결과는 [GitHub PR #5](https://github.com/Ontologics-2Zo/ontologics-chat/pull/5)를 기준으로 한다. 원시 질문·응답·trace·스크린샷·DB dump·인증 파일은 Git 밖에 보관한다.

| 검사 | 결과와 범위 |
|---|---|
| 전체 서비스 | 격리 PostgreSQL/Neo4j에서 `python -m unittest discover -s tests`:413 PASS/skip0,69.950초. 실제 모델/키0. Runner16통제(메모리·PID·CPU 강제 포함) PASS |
| 실제 브라우저 | Chromium 작성자 A 읽기17검사 PASS.482/704 ID 전수 일치, 좌표 불변, 근거165 전부 강조, 카메라137프레임/135개 서로 다른 viewBox, 검색·취소·reduced-motion·탭·모바일·테마 확인. 외부 요청/JS 오류/업무 쓰기/새 모델 호출0 |
| 화면 모듈 | `node --test tests/*.mjs`:154 PASS. graph13 포함. 실제 v4 snapshot482노드/704관계, 고유 좌표482/도형 겹침0, 핵심9단계+BOM 일괄1단계 |
| 관측·모델·Agent | 격리 PG에서 관련67 PASS 및 실행중 취소1 PASS. 실제 모델0 |
| 납기 결론 | `python -m unittest tests.test_query_answers`:23 PASS. 미확인 수량·알려진0·현재 등록/미등록 분기 |
| 수정 후 조회 계약 | `dev/evaluate_service.py`:24고유/24회 PASS, warmup0, 원래24사례에 미확인 결론 assertion 추가. 읽기 전용/모델0 |
| Langfuse Fake 실제 저장 | Public observations API 재조회:6관측, root/segment/model parent 정합, 질문/최종답변과 root·trace IO 일치, 실제7bool feature/capability 일치, usage NULL/FAKE_NOT_BILLABLE 유지. exported6/failed0/invalid0. 비용0 |

전체 회귀 근거 SHA-256 `2041e6f3270e3731615fa2166ecb1b6bc19ffc5437e8e53d91bb6e59671a6f74`, 코드 전후 SHA `312d241637bdadaf9b4c397ecc8c2697890c5b821fd19fcb15428ecaf951e5c9`. 첫 harness 실행의 DSN 형식/동적 localhost 방화벽 제한을 수정한 뒤 동일 서비스 코드로 재실행했다. 이 준비 오류를 서비스 실패 또는 성공 건수로 합치지 않는다.

관측 근거 SHA-256: Fake 저장 `b7c1aa7fba07a9ccc284d141ba274feaddb4d380f237d4f240fc3b830db249b4`, 기존 빈 저장 확인 `19aacf64fb30cc516060c102acfea60fb80947d7352cf43177a5f8c9585b5bf9`. 수정 조회 계약 SHA `84085a6b632a507646e6db9220c4a1648cec127bdaed81f91dc4d1aae74226a0`.

## 보존·실행 원칙

원래 앱은 원장v4/482노드704관계/31테이블과 활성 원문74행을 유지한다. 별도 BOM 최초 시연은 원장v1/18노드11관계/27테이블/BOM0이며 Agent/key 없이 유지한다. 최초 시연을 원래 DB 초기화로 구현하지 않았다. 검증은 별도 복제 앱과 별도 회귀 DB/Neo4j에서 수행한다. 원본155파일, 제공 참조11파일, 보호 화면7파일, 기존 사용자 파일9개를 hash 대조한다.

이번 검토 직전 canonical 예산은 live33회·확정 US$0.01343·기존 미확정 예약 US$0.016414·잔여 US$9.970156이었다. 이전 평가 문서의30회 수치는 당시 값이며 이후 사용자 호출을 삭제하지 않는다. 검증 후 main 앱 재시작은 기존 runtime과 `.env`를 읽고 로컬 관측 설정만 별도 비공개 launcher/override로 추가한다. DB import/reset, 원래 `.env` 덮어쓰기, 기존 예약 해제는 하지 않는다.

## 남은 수용

독립 사람 gold·전체 CQ20/110·B0~B5·규모/실무 효과와 실제 원답 품질 인수는 미완료다. 이전 정성 기준선의 핵심 결과 우선/다음 행동 구체화 개선점은 남는다. 이번 미확인 결론 수정과 회귀 PASS를 전체 품질 재평가로 부풀리지 않는다.
