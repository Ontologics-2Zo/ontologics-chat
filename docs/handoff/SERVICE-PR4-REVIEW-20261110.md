# PR #4 최종 검토와 병합 기준

2026-11-10 DEC-H38. 사용자가 PR #4를 확인하고 문제가 없으면 main에 병합하도록 승인했다. 검토 대상은 base `848fe79`에서 head `c02122c`까지의 전체94파일과 후속 Agent 대기 선택 복구 수정 `24ff01f`다. 상태 VERIFIED(병합 전 검토 범위). 최종 head와 실제 merge SHA·병합 후 Mac 확인 결과는 [PR #4](https://github.com/Ontologics-2Zo/ontologics-chat/pull/4)에 기록한다. 이 문서 자체는 병합 성공을 미리 선언하지 않는다.

## 검토와 수정

- Model Gateway/영속 Agent/권한·정확 버전/복구/검색/Product/관측과 UI/BOM/그래프를 독립 검토했다. 구매·입하·IQC의 승인·명시 반영·멱등성·원장/outbox 경계도 확인했다. 서버 쪽 새 병합 차단 사항은 발견하지 않았다.
- 화면 P2: 과거 WAITING 답변의 업무 선택 버튼을 완료 또는 원장 갱신 후 다시 누르면 낡은 실행의 resume이 이어져 새 질문도 실패했다. 서버는 버전 불일치를 차단하므로 원장 변경은 없었지만 화면 복구가 필요했다. 현재 대기 실행·버전·조회 기준에만 선택을 허용하고 소비·무효화 시 과거 버튼을 비활성화한다. 과거 질문과 답변은 보존한다.
- 원형 보존 화면7파일은 `b3ee599`와 바이트가 동일하다. 실제 BOM·개인 평가·trace·키·DB·비공개 원본을 공개 변경에 포함하지 않았다. 새 파일과 diff의 자격증명/로컬 비공개 경로 검사도 수행했다.

## 실행 증거

| ID | 대상·환경·명령 | 결과 |
|---|---|---|
| PR4-PYTHON-ALL | `c02122c` backend, Mac의 새 일회용 PostgreSQL DB와 전용 Neo4j 컨테이너/볼륨, `python -m unittest discover -s tests -q` |381 PASS / 0 SKIP,71.385초 |
| PR4-RUNNER | 같은 로컬 이미지의 제한 Runner, 메모리/PID/CPU 실제 강제 포함 |16 PASS |
| PR4-UI-ALL | `24ff01f`, `node --test tests/*.mjs` |106 PASS / 0 SKIP, 로그 보관 실행150.519459ms |
| PR4-UI-RECOVERY | 신규9건, 실제 app.js를 합성 DOM/fetch로 실행 |완료·과거 버튼·원장 갱신·409/408·새 질문·두번째 WAITING·재시도·잘못된 snapshot PASS |
| PR4-PRESERVE | 서버 회귀 전후 기존 서비스 DB/그래프/원본/설정/예산 대조 |31테이블 및 아래 보존 대상 동일 |

전체 회귀 실행에는 기존 서비스 Neo4j 연결을 넘기지 않았다. 실키를 제거하고 Fake 기본·외부 소켓 차단 상태로 실행했으며 실제 모델 요청0이다. 소유한 테스트 DB·컨테이너·볼륨만 정리했다. UI 복구 수정은 app.js/테스트만 바꾸므로 backend가 동일한381건 결과를 재사용했다. 수정의 독립 재검토도 추가 차단 사항0이며 `git diff --check` 통과다. GitHub Actions와 commit status는 이 검토 시점에 구성된 실행이 없었으므로 GitHub CI 통과로 보고하지 않는다.

앞선 실제 브라우저 검증은 변경되지 않은 코드에 재사용한다. [그래프/CQ](SERVICE-GRAPH-25D-20261110.md)의36 assertions와 reduced-motion1, [자재 충족률](SERVICE-MATERIAL-CARD-20261110.md)의 실제12/별도 합성9, [BOM](SERVICE-BOM-FOCUS-20261109.md) 및 [원장 사건](SERVICE-LEDGER-FEED-20261109.md)의 대상 코드·범위를 구분한다.

| 비공개 증거 | SHA-256 |
|---|---|
| 병합 전 보존 기준 | `aecaa63124f988a621c182240f83948e1ddd59baa56564843bdd8e364421da5e` |
| 격리 전체 회귀·정리·보존 결과 | `907358dc01386ef7d24b6eb8e7cd07a8e0cc67ea79696d5591bc21bd8a8ef34d` |
| Python 전체 회귀 로그 | `6aa1a7b78e71f737567907e96a9e4fa3ba307f82de86d03421b7980a0e7a3710` |
| UI106 전체 회귀 로그 | `19c2be95af3286a26b03b7091d6db77dcb5e2f950321a61d5111121067018adf` |
| 전체 회귀 후 보존 대조 | `8f32b5d9d27f4a6ce0958ea419acd1949fa0f5c12034ae3da521038cbff24a7f` |

## 병합·실행과 데이터 경계

정확한 검증 head를 지정해 병합하고 기존 작업 트리의 사용자 파일을 보존한 채 main을 fast-forward한다. 재시작 전 pending 업무/실행과 기존 migration checksum을 확인하며 기존 앱 프로세스만 같은 runtime·환경·예산으로 재시작한다. 초기 이관·reset·새 migration SQL·원장 사건 생성은 수행하지 않는다. 재시작 후 작성자 A의 읽기 화면과 `/health` 및 보존 대사를 별도로 확인한다.

병합 전 보존 기준은 원장/투영v4, Neo4j 전체482노드/704관계, BOM31문서/4원본, 원본155개, 보존 화면7개와 전체 온톨로지52클래스/74관계/79shape다. 현재 활성 BOM은74행 모두 적용 미확인·계산 품목0이다. 과거 선택5품목의 조건부 적용 문서는 보존하며 현재 BOM으로 복사하지 않는다. 남은 고객 도착68과 추가 양품 목표40을 구분하고 실제 납기 가능성은 UNKNOWN이다.

이번 검토 시작의 같은 US$10 예산은 누적16요청, known US$0.00557, held US$0.016414이며 사용 이력/미확정 예약을 보존한다. 추가 실제 모델 호출은 이 검토에 필요하지 않다. 새 인증 세션의 감사 기록과 업무 원장 변경은 구분한다.

부모 Task 상태와 남은1차 수용은 그대로다. PR 병합은 전체 업무 Agent·전체 CQ20·제조 기술검수·실제 납기 확정·외부 공급사 연계·운영 배포 완료가 아니다.
