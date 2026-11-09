# BOM 등록 전 별도 시작 시연

2026-11-10 DEC-H40. 검증 대상 코드 `ee10ea9`, 상태 VERIFIED(별도 INITIAL 시작·등록 진입 범위). 사용자는 남은 고객 도착 약속에 대한 질문을 BOM 등록부터 시작하는 시점으로 다시 시연하고, 기존 MK-S2 화면 연결을 확인하도록 요청했다. 기존 PR #5의 `codex/digital-twin-db`에서 진행했다. 전체1차 서비스 인수나 main 병합을 뜻하지 않는다.

## 두 환경의 구분

| 구분 | 기존 작업 환경 | 새 시작 시연 |
|---|---|---|
| 주소 | `http://127.0.0.1:8870/mks2/#twin` | `http://localhost:8872/mks2/#twin` |
| 원장 | 기존 v4·BOM31문서/4원본 유지 | 별도 빈 DB에 INITIAL 최초 이관 |
| 데이터 | 기존 원본 등록·검토·승인 이력 보존 | BOM 미등록부터 시작, 과거 판단 복사 없음 |
| 저장소 | 기존 PostgreSQL·Neo4j·runtime | 별도 PostgreSQL·Neo4j·볼륨·영속 runtime |
| 모델 | 기존 live 설정과 승인 US$10 예산 유지 | Agent disabled·결정적 서버 조회, 예산 계정 복사/신설 없음 |

68은 남은 고객 도착 약속,40은 추가 양품 목표다. 이 시작 환경의 수량은 검증된 INITIAL 합성 기초 이관이며 실제 운영 실적이 아니다. BOM이 없으면 소요·부족을0으로 표시하지 않고 `BLOCKED_BOM`으로 유지한다. 원본 등록과 검토·독립 승인·원장 반영을 마쳐야 적용 판단에 따른 계산으로 이어지며, 그 뒤에도 실제 납기 가능성은 생산·조달·검사·운송 등의 실제 근거가 필요하다.

## 구현

- `service/__main__.py`: `--origin-host`와 세 DB 포트 인자를 추가했다. 기존 기본값과 실제 `127.0.0.1` bind는 유지한다. 외부/별칭 Host, 범위 밖·중복 포트를 DB 연결 전에 거부하고 기존 runtime의 정확한 loopback/포트 검사를 사용한다. 같은 호스트의 포트만 바꾸면 쿠키가 섞이므로 새 시연은 `localhost`로 분리한다.
- `service/query_answers.py`: BOM 미등록 납기 질문에서 “현재 수량 확인 · BOM 등록 후 소요·부족 계산 · 납기 판단 보류”를 표시한다. 등록된 BOM의 기존 답변은 유지한다.
- `web/mks2/query.js`, `app.js`: 서버의 `BLOCKED_BOM`·BOM ID 없음 상태에만 **BOM 원본 등록** 버튼을 표시한다. 정확한 현재 identity/company/snapshot/ledger/projection/ontology 버전을 확인한 뒤 기존 `dashboard.openBom(0)`만 실행한다. 과거 답변은 등록 진입을 차단한다.
- 기존 BOM drawer·등록8단계·검토/승인/반영 분리·디지털 트윈 CSS/시설/패널·보호7파일은 변경하지 않았다. 버튼은 자동 등록·승인·업무 쓰기를 실행하지 않는다. drawer에서 기존 GET 조회와 최초 Runner 상태 probe는 발생할 수 있다.

새 runtime은 저장소 밖의 영속 비공개 디렉터리다. 별도 컨테이너2개/볼륨3개와 새 자격증명을 만들었으며 기존 자원을 삭제하거나 재설정하지 않았다. 비공개 `launch.py`는 기본 실행에 import가 없고, 최초 설치 옵션도 빈 새 DB에서만 허용한다. 기존 원본·검증된 Runner 이미지·로컬 embedding 참조는 재사용하지만 실제 모델 키·예산·비용 예약은 복사하지 않았다. 일반 실행 안내는 [CLOUD_SETUP 5-1절](CLOUD_SETUP.md#5-1-bom-등록-전-별도-영속-initial-시연)을 따른다.

## 검증과 남은 경계

UX-H01/02/05/08/09, G-03~05/08/09, AI-04~07 → T10/T19/T22의 시작 동선 추가 검증이다. 부모 Task와 전체 Agent/CQ20 인수 상태를 바꾸지 않는다.

| ID | 명령·범위 | 결과 |
|---|---|---|
| START-CLI | `.venv/bin/python -m unittest tests.test_service_startup tests.test_service_stack -q` |7 PASS. 신규4와 기존3. sandbox의 Unix socket bind 제한으로 첫 실행이 막혀 허용된 호스트 환경에서 재실행 |
| START-QUERY | `.venv/bin/python -m unittest tests.test_query_answers` |20 PASS. BOM 미등록/등록 분기, 수량 보존·소요 UNKNOWN·납기 UNKNOWN·원장 불변 |
| START-UI | `node --test --test-reporter=dot tests/*.mjs` |133 PASS. 기존129+미등록 CTA/현재 버전/오래된 답변 차단4 |
| START-REVIEW | 독립 CLI·CTA 읽기 검토와 가짜 DB 경계 검사 |차단 문제0. 잘못된 인자7개에서 runtime/DB 호출0 |
| START-RUNTIME | 별도 실제 Docker·INITIAL·제한 Runner·일반 재시작 |PASS. health200·Agent disabled·예산 계정0·모델0·원장/투영v1·18노드/11관계·BOM문서/원본0. Runner 강제검사 PASS. import 없는 재시작 전후 새DB27테이블과 설정 hash 동일 |
| START-BROWSER | Mac Playwright CLI·작성자 A·동일 MK-S2 화면·질문→원본 접수→파일 선택 |39 assertions PASS. 동일8단계/6시설/패널 폭, 보관 원본7개·선택만/추출 미실행, 기존 그래프18/11·대시보드·답변 보존, host-only cookie 분리, 업무/Agent 쓰기·외부 요청·JS오류0 |
| START-PRESERVE | 기존 DB·그래프·원본·설정·예산과 새 시연 미등록 상태 대사 |VERIFIED. 기존31테이블(인증 포함) 전체 count/hash 동일. 새 시연의 인증2건 추가 외25테이블·snapshot·그래프 불변, BOM0·예산 계정0·모델0 유지 |

Python/Node의 순수 회귀 출력은 도구 실행 기록에 보존했다. `node --check` 두 화면 파일, `git diff --check`, 문서 예제 구문/CLI 인자 검사도 통과했다. 전체381개 서버 회귀나 실제 모델 품질 평가를 이번 변경으로 반복 완료했다고 보고하지 않는다.

초기 검증 helper의 필수 헤더 누락과 공백이 있는 PID 경로 분할 오류는 각각 서버 경계/보수적 process 검사에서 중단됐고 보정 후 통과했다. 초기 브라우저 guard가 읽기 POST까지 막은 것과 UI 전환 대기·기존 답변이 남은 검사 오류도 분리했다. 최종39개 검사는 새 페이지에서 기존 읽기 POST만 허용하고 실제 업무 쓰기를 차단해 통과했다. 이 준비 오류를 제품 실패나 실제 모델 실패로 기록하지 않는다.

사용자용 새 탭에서도 작성자 A로 결정적 CQ를 실행해 **BOM 원본 등록** 버튼이 보이는 상태를 확인했다. 새 환경의 인증 기록만 추가되며 BOM 등록은 실행하지 않았다.

| 비공개 증거 | SHA-256 |
|---|---|
| 기존 환경 착수 보존 기준 | `68796c2327ae3be2c82a3cef1add3458f1bd7347b141646873ee814e68cae798` |
| CLI/스택7검사 | `39e656e1fe36060a3ebf7c7b5b1983dbba254e6d525e35576f1596445bb137a4` |
| 새 영속 환경·INITIAL·재시작 | `567f185b80650b9747f64b0a49aff75d59e3d3dee646f5816e3895fc58624030` |
| 새 DB 재시작 전후 동일 hash | `de7e3ece7d45d8b92f647940cb4b76c53c1f705a636c60fa4e62a143c876daf9` |
| 실제 브라우저39검사 | `97dffeab32123a0f238fa6d24a7c5c01966e7125df86623d66593c051f0e8e43` |
| 최종 양쪽 환경 보존 판정 | `627f299c5cadf4098e5a10f31b710f5ad2980e5c9271eaa6a1cb6e5e3a7aec76` |
| 기존 환경 최종 상태 | `e0590085fb4e1c898b7e7f898a942e0b0ea616edc70b9d116c908ec73022ed3b` |
| 새 시연 최종 상태 | `d76db9a481043e9487c4a56f9b20226f608e28fe11f9c934b5012ad1722765cd` |

최종 기존 환경은 원장/투영v4·482노드/704관계·BOM31문서/4원본과 모든 snapshot이 동일하다. 실제 원본155개·제공 참조11파일·보호7파일·CSS8파일·사용자 미추적9파일·runtime/env·전체 온톨로지와 기존 Docker 자원을 보존했다. 이번 변경의 기존 web allowlist는 `app.js`/`query.js`와 `web/AGENTS.md`뿐이다. 원래 앱은 재시작하지 않았으며 health200/live다. 기존 누적 모델16요청·known US$0.00557·held US$0.016414와 승인 US$10 한도는 동일하다.

새 시연은 health200/disabled·원장/투영v1·18노드/11관계·BOM문서/원본0·모델 attempt0·예산 계정0이다. 준비 API 확인 후 QA 로그인과 사용자 탭 로그인으로 인증 기록2건만 추가했으며 기존 인증 행은 보존했다. 독립 컨테이너2개·볼륨3개·비공개 복구 안내와 실행기는 계속 사용할 영속 자원으로 유지했다. 새 앱의 일반 재시작에 초기 이관은 없다.

실제 원본 선택·추출·등록/검토/승인/반영은 이번 시작 동선 검증에서 실행하지 않는다. 새 시연을 BOM 미등록 상태로 남겨 사용자가 원본부터 진행할 수 있게 한다. 과거 선택5품목 판단을 자동으로 붙이지 않으며, 모든 실제 원문과 미확인 항목은 기존 등록 계약을 따른다. ODS/CSV 표 등록, PDF 텍스트 추출과 BOM 표 등록 미지원, TXT/OCR 미지원을 구분한다. 공개 Git에는 원본·실제 화면 캡처·DB·runtime·세션·키·개인 trace를 넣지 않는다.
