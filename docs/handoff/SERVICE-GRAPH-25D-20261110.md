# MK-S2 2.5D 그래프와 고객 도착 CQ

2026-11-10, DEC-H36. 검증 코드 `451ec959209dcbb7686cb9921ad5cd61a0070037`, 브랜치 `codex/phase1-material-flow`, PR #4. 상태는 **VERIFIED · 표현/읽기 slice**이며 T06/T10/T11/T19 부모는 IN_PROGRESS다.

## 요청과 구현

기존 DB·노드·관계를 보존하면서 그래프를 2.5D로 정리하고, 공유 MK-S2의 고객 도착 질문에 대한 결론·수량·근거·조건 흐름을 실제 서버 응답에 연결했다. 원형의 fixture 답변·미래 사건·비공개 HTML은 복사하지 않았다. 화면 변경은 `web/mks2/`에 한정한다.

- `graph-layout.js`는 고객 약속, 생산·완제품, 출하·도착, 설계·BOM, 자재·재고, 조달·검사 영역을 얕은 층으로 배치한다. 라벨은 정면을 유지하고 많은 행은 격자에 놓는다. 업무 영역은 표시 분류이며 새 관계나 일정 추론이 아니다.
- 질문 근거의 핵심 객체와 원문 행/BOM 행 객체를 나누어 배치한다. 현재 핵심17개, 행 근거148개(원문74 + BOM 행74), 나머지 원장317개를 모두 유지한다. `핵심 흐름`, `전체 근거`, `전체 보기`로 이동하며 모든482노드/704관계의 ID·방향·술어·근거는 그대로다.
- 관계마다 독립 SVG 경로와 방향 화살표를 갖는다. 평행·역방향·자기 관계를 합치지 않는다. 행 근거의 선은 얇게 표시하고 hover/키보드 선택으로 관계를 확인한다. 표시 라벨을 줄여도 전체 ID·라벨은 제목/상세에서 유지한다.
- 카메라는 질문 근거의 전체 배치를 한 번 맞추고 핵심 관계만 짧게 재생한다. 마지막에는 명시된 전체165노드/231관계를 강조한다. 매 관계마다 확대하지 않는다. 보간 시간 하한·양수/유한 viewBox, 실제 화면 비율, resize, 취소와 reduced-motion을 보완했다.
- 납기 답변은 서버 결론 → 잔여 고객 도착 약속/추가 양품 목표 → 정확한 근거 관계 → 현재 수량/계획 투입/BOM·재고/미확인 조건 순서다. 관계4개와 객체6개를 먼저 보이고 나머지는 펼친다. 원장·그래프 버전/회사/신원 일치 검사를 유지한다.
- 새 모듈만 정적 allowlist에 추가했다. 질의 인식·도메인 계산·DB 스키마는 변경하지 않았다.

## 현재 수량과 출처

사용자가 요청한 자연어 문장은 기존 엔진에서 delivery/fulfillment/production으로 이미 인식된다. 원장 v4 기준 남은 고객 도착 약속68, 추가 양품 목표40, 수율94%의 계획 투입43을 각각 구분한다. 이 초기 수량·일정은 합성 초기 원장이고, 실제 BOM 원문 등록 사실과 다르다. 실제 납기 가능성은 UNKNOWN, 응답은 PARTIAL이며 ETA·확률을 만들지 않는다.

착수 시 활성 BOM은 전체74행 미확인·현재 적용0이었다. 과거5품목 조건부 적용/69행 미확인 결과를 현재 활성 문서의 판단으로 재사용하지 않는다. 이전 문서·원본은 그대로 보존한다. 이 UI 수정은 원문 기술검수·재고 확정·부족 해소·고객 도착 완료가 아니다.

## 요구와 검증

| 검증 ID | 요구/구현 | 실행과 결과 |
|---|---|---|
| GRAPH-25D-LAYOUT | UX-H09, SD-028~030, G-01~09; `graph-layout.js`, `graph.js/css` | Node 전체90 PASS 중 배치9건:482개 좌표/입력 불변, 행 분리, 재정렬 안정성, 실제 DTO 별칭, 평행/역방향/자기 관계, 범위/보간 |
| GRAPH-25D-ANSWER | UX-H02, AI-04~07, CM05; `query.js`, `styles.css`, `app.js` | Node query16 PASS 포함. 서버 수량·미확인·출처 유지, 정확 ID/방향/predicate, 접힌 전체 근거, 취소/늦은 재생 |
| GRAPH-25D-API | 같은 버전 API/정적 경계 | `.venv/bin/python <private isolated-suite> graph-layout-static 'test_service_api.py'`: 별도 일회용 DB22 PASS, 4.541초. 순수 기존 CQ49 PASS는 의미 회귀 보조 |
| GRAPH-25D-BROWSER | UX-H02/H09, AT08/CM05의 읽기 표현 slice | Mac localhost8870 실제 작성자 A, Playwright CLI36 assertions PASS(반복 보존 대조 포함). 실제482/704 ID세트,165/231 근거,68/40/UNKNOWN,원문층,전체 온톨로지,키보드,취소,모바일,읽기 snapshot/예산 불변 |
| GRAPH-25D-MOTION | 접근성 | 별도 reduced-motion CSS 실제 계산 스타일1 PASS: 노드·관계 animation:none |
| GRAPH-25D-PRESERVE | 기존 원본·DB·예산·전체 온톨로지 | 독립 보존 대사 VERIFIED_WITH_AUTH_APPEND. 아래 보존 범위 참조 |

명령: `node --test tests/*.mjs`, `git diff --check`, 설치되어 있던 Playwright CLI의 `run-code --filename=<private browser harness>`. 새 패키지 설치나 외부 모델 호출은 없다. 브라우저는 인증 및 읽기 POST만 허용하고 업무 쓰기/Agent 실행 요청은 차단했다. 1536×1100,390×844 및 밝은/어두운 테마를 확인했다. 초기 headed 캡처의 일부 도형·글자 누락 이미지는 시각 증거로 사용하지 않고, 동일 세션을 별도 headless 렌더러에서 재검사해 완전한 표시와36개 assertion을 확인했다. 캡처·실제 데이터 상세·로그는 Git 밖에 둔다.

## 재시작과 보존

원장/투영v4, BOM31문서·4원본, 전체 현재 Neo4j482노드/704관계의 ID·속성·관계 canonical hash, 모든 snapshot/원문74행이 그대로다. 비인증29개 테이블의 count/hash가 같고 인증 세션·감사만52→53으로 검증 로그인1건 추가됐다. 기존52행 hash·취소0을 확인했다. 원본155개, 보존 화면7개, runtime/설정/`.env`/Runner/embedding과 전체 온톨로지52클래스·74관계·79shape를 유지했다.

정적 allowlist를 적용할 때 진행 중 Agent/모델/원장·투영 작업0 및 migration checksum 일치를 먼저 확인했다. 기존 실행 진입점의 적용 여부 검사만 통과했으며 새 migration SQL·초기 이관·reset 없이 앱을 재시작했다. `/health`200/live 유지. 모델14회, known US$0.00471, 미확정 예약US$0.016414와 기존 US$10 예산은 동일하다. 이번 추가 모델 호출·비용0.

| 비공개 증거 | SHA-256 |
|---|---|
| Node90 | `af29bb65ce7501a7e569a2e8ac12f7be5c37aaf79a6b186acea54d6b041d8ecd` |
| 실제 브라우저36 | `a1bad4d50b9df5364e7d9ae4f529424cbfbc7037bd6b32f01170949efcccd05c` |
| reduced-motion | `a4e1cf547802c023182d34449da2168b967c7c7b899d0360015e03b02e7e2ecd` |
| 재시작 | `e5adcc23814c280276debae89538a5983e3d2b7774750afb40583a636d5696b1` |
| 최종 보존 대사 | `92cb882d3cc7bc71cdd901af278bce63ee8ef48c8c068e06488609083c5eadde` |

## 남은 범위

기존 그래프 음수 viewBox 오류의 역사적 기록은 보존하며 이번 코드에서는 회귀/브라우저 검사를 통과했다. 자유 회전3D·권한별 전체 추적경로·온톨로지 변경영향/역사비교·10만 객체 규모·전체 AT08/Agent/CQ/1차 인수는 이번 변경으로 완료하지 않는다. PR #4 업로드 범위이며 main 병합·배포·공급사 송신·운영 원장 변경은 하지 않는다.
