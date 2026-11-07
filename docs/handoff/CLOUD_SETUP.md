# Codex Cloud 개발 환경 준비

대상은 공개 저장소 `Ontologics-2Zo/ontologics-chat`이다. 2026-11-08 조회에서 저장소 크기 0, 브랜치 목록 비어 있음이 확인됐다. 이는 공개 배치 전 확인 기록이다. 후속 요청으로 공개 문서를 배치하며 환경·권한·비공개 전송은 변경하지 않는다. 실행 전 최신 상태를 다시 확인한다.

현재 저장된 `ontologics-chat` 환경에는 `Ontologics-2Zo/-ontologics-chat` 연결이 표시됐다. 목표 저장소와 다르므로 [전달 안내](PRIVATE_INPUT_DELIVERY.md)의 연결 확인부터 진행한다. 서비스 구현 전에는 [준비 전용 요청문](PREPARATION_ONLY_REQUEST.md)을 사용한다.

## 준비 순서

1. 공개용 인계 문서만 검토해 초기 커밋으로 반영한다. 원본 묶음은 포함하지 않는다.
2. Codex Cloud에서 해당 저장소를 선택해 환경을 준비하고, 설치·실행 점검 결과를 확인한 뒤 환경을 Publish한다.
3. 비공개 입력은 별도 접근 통제로 공급하고 hash를 확인한다. 공개 저장소 주소나 PR 첨부로 전달하지 않는다.
4. [첫 개발 요청문](FIRST_CLOUD_TASK.md)으로 T00~T01을 실행한다. 개발 골격이 만들어진 뒤 실제 manifest/lock에 맞춘 설치·시작 절차를 검증하고 환경 설정을 갱신한다.

현재 Cloud는 재사용 환경에서 작업별 공간을 만들며 Install script와 Start skill로 준비·기동 절차를 기록할 수 있다. 환경 게시와 서비스 배포는 구분한다. 구체 UI는 [공식 Cloud 안내](https://learn.chatgpt.com/docs/environments/cloud-environments)를 따른다. 확인일: 2026-11-08.

## 프로젝트에서 준비할 항목

| 항목 | 시작 설정·검증 | 현재 상태 |
|---|---|---|
| Git 입력 | 인계 문서의 승인된 커밋 SHA, 원격 최신 상태 확인 | 공개 배치 대상. 실제 반영은 GitHub 커밋으로 확인 |
| 개발 런타임 | T01에서 실제 Python/Node/패키지 관리자·lock 확정. 공식 구조에 필요한 버전을 확인 | NOT_CONFIGURED |
| API·DB | FastAPI/Host, PostgreSQL, migration, 실제 read/write·재시작 점검 | NOT_IMPLEMENTED |
| 그래프·검색 | RDF/SHACL/SPARQL, Neo4j, 검색 의존성을 각 Task에서 고정 | NOT_CONFIGURED |
| LangGraph·관측 | 호환 버전 고정, 로컬 Langfuse 연결·redaction·Tree/Timeline 확인 | NOT_CONFIGURED |
| Sandbox | Broker/Runner의 실제 격리 실행 가능성 확인. 불가하면 해당 Task를 막고 가능한 작업 분리 | NOT_VERIFIED |
| 모델 | 처음에는 Fake. 실제 모델·전송 허용 데이터·예산이 정해진 후 opt-in | LIVE_LLM_NOT_AUTHORIZED |
| 원본 | Git 밖 `PRIVATE_INPUT_ROOT`, 읽기 권한·hash·보관 주기 | LOCAL_PACKAGE_READY / CLOUD_NOT_PROVISIONED |
| 평가 저장 | Git/Docker 밖 `PRIVATE_EVAL_ROOT`, 비식별 공개 결과와 상세 private trace 분리 | NOT_CONFIGURED |

`PRIVATE_INPUT_ROOT`와 `PRIVATE_EVAL_ROOT`는 이 프로젝트의 제안된 설정 이름이다. Codex 플랫폼이 자동 공급하는 환경변수가 아니며, 경로에 자료가 자동 생성되지도 않는다. 구체 설치 명령은 T01에서 실제 파일이 생긴 뒤 기록한다. 이 인계본에는 실행하지 않은 설치 스크립트를 포함하지 않는다.

## 비공개 입력의 공급

- 원본 묶음은 저장소 밖에 보관한다. 인증된 비공개 저장 공간에서 읽거나 사용자가 허용한 환경에 별도 제공한다. 공급 방식과 자격증명은 아직 미정이다.
- 개발 환경에서 원본을 읽도록 허용하는 것과 외부 LLM·공개 웹으로 재전송하는 것은 별도다. 모델 기본값은 Fake이며 실제 호출에는 데이터 범위·모델·예산을 확인한다.
- 환경 접근자는 원본에 접근할 수 있으므로 원본이 들어간 환경의 공유 대상을 제한한다. 사용자 개인 파일이나 로그인 상태가 자동으로 Cloud로 이동한다고 가정하지 않는다.
- 원문 파일 이름·로컬 경로·원시 trace가 공개 테스트 로그·스크린샷·PR artifact에 남지 않도록 한다. 공개 UI 캡처에는 별도로 검토한 fixture를 사용한다.
- 자격증명은 환경 설정으로 공급한다. 환경 변수와 네트워크 secret의 차이 및 허용 목적지 설정은 [공식 환경 안내](https://learn.chatgpt.com/docs/environments/cloud-environments)를 확인한다. 키 값을 Git이나 문서에 쓰지 않는다.

## 환경 준비의 통과 기준

- 실제 실행 위치·커밋·runtime 버전·lock을 기록한다.
- T01에서 health와 테스트 DB 왕복 저장·재시작을 확인한다. 자료 원문이나 credential을 출력하지 않는다.
- 이후 Task에 필요한 서비스가 제공되는지 각각 확인한다. Cloud에서 Docker 명령이 보인다는 사실만으로 Runner 격리 시험을 통과시키지 않는다.
- 비공개 자료 미공급은 해당 원본 검증을 BLOCKED로 남긴다. Fake·공개 bootstrap과 실제 원본 테스트를 혼동하지 않는다.
- 환경 설정/게시, 공개 문서 커밋, 서비스 구현/배포를 각각 기록한다. 이번에는 공개 문서 배치만 수행하고 환경 변경·서비스 구현/배포는 하지 않는다.

루트 `AGENTS.md`는 새 저장소의 개발 기준을 전달한다. 원본 자료 안의 과거 AGENTS 파일을 활성 경로로 복사하지 않는다. 파일 탐색 규칙은 [공식 AGENTS.md 안내](https://learn.chatgpt.com/docs/agent-configuration/agents-md)를 따른다.
