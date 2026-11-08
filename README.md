# ontologics-chat 개발 인계

MK-S2의 화면·핵심 동선을 유지하면서 제조 업무를 실제 서버 원장·온톨로지·Agent에 연결하기 위한 개발 기준이다. 공식 설계 v1.7과 시연 데이터 v1.3은 서로 다른 버전 축이다.

**현재 코드: MK-S2의 내용·구성을 재현한 읽기 전용 화면 미리보기. 실제 서비스 원장·승인·T-Box/A-Box 투영·모델 연결은 이 코드에서 구현 완료되지 않았다.** 이전 미커밋 서비스가 없는 새 환경에서 사용자의 최신 화면 구현 요청에 따라 작성했다. 이전 서비스 복원본으로 취급하지 않는다.

## 화면 실행 — Cloud / Apple Silicon Mac

Python 3.10 이상과 최신 브라우저를 사용한다. 이 화면에는 외부 Python/npm 의존성, 설치 과정, DB migration, Docker가 없다. 의존성 lock과 migration은 해당 없음이며 전체 서비스 패키지를 뜻하지 않는다.

비공개 입력은 저장소 밖에 둔다. `PRIVATE_INPUT_ROOT`는 `mk-s2/fixtures`가 들어 있는 기존 입력 루트를 가리켜야 한다. 키는 화면 실행에 필요하지 않다.

```bash
# Mac 예시: 본인의 실제 비공개 입력 위치로 지정한다.
export PRIVATE_INPUT_ROOT="$HOME/Private/ontologics/private_inputs"
python3 dev/preview_server.py --reference-preview --port 8765
```

Mac에서 위 명령을 실행한 경우 **그 Mac의** 브라우저로 `http://127.0.0.1:8765/`를 연다. Cloud에서는 기존 `PRIVATE_INPUT_ROOT`를 그대로 사용한다. Cloud 내부의 같은 localhost 주소가 사용자 Mac에서 열리는 주소는 아니다. 현재 환경의 외부 접근용 미리보기 주소는 확인되지 않았다. Mac 실기 실행은 **NOT_RUN**이다.

화면 확인 동선:

1. 온톨로지·질의 → 노드 선택 → 상세의 관계 칩. 추천 질문은 입력란만 채우며 **보내기** 후 시연 근거를 재생한다.
2. 운영 대시보드 → KPI/자재·회차 그래프/표 → BOM 원본 등록 → 보관 원본 미리보기. 각 단계는 읽기 전용이며 실제 파일 처리·승인·반영을 실행하지 않는다.
3. 대시보드 추천 질문 → 실행 → BOM 미등록에 따른 판단 보류 → **등록 후 견적 화면 예시**. 별도 시연 버전임을 패널에 고정 표시한다. 구매·입하·수입검사 쓰기는 미연결이다.

시연 수치·답변은 원본 fixture를 실행 시 읽는다. 브라우저 화면의 데이터나 정해진 시연 답변을 실제 원장·Agent 실행으로 인정하지 않는다. 원본의 `null개` 표시는 `소요 계산 보류`로 보완했다. 새로고침·초기화는 화면 선택만 되돌리며 원장을 삭제하지 않는다.

## 변경 범위 검증

```bash
python3 -m unittest discover -s tests -p 'test_preview_server.py' -v
node --check web/app.js
node --check web/dashboard.js
node --check web/graph.js
```

선택적 브라우저 검사는 Node 22 이상과 별도로 실행한 Chromium의 loopback CDP 포트가 필요하다. 격리된 브라우저 프로필을 사용한다. 캡처에 비공개 시연 내용이 포함될 수 있으므로 산출물 경로는 Git 밖이어야 한다.

```bash
node dev/check_ui.mjs --cdp=http://127.0.0.1:9233 --url=http://127.0.0.1:8765/ --artifacts=/tmp/mks2-private-ui-check
```

이 검사는 화면·읽기 전용 접근·근거 재생 취소·버전 불일치 처리에 한정한다. 실제 API/DB/권한/승인/SHACL/모델/관측 인수는 별도다. 원본 HTML·fixture·비밀 설정·DB·평가 원문은 실행 ZIP이나 공개 Git에 포함하지 않는다.

## 개발 인계

- 시작: [개발 인계 안내](docs/handoff/START_HERE.md)
- 작업 순서: [구현 작업표](docs/handoff/IMPLEMENTATION_TASKS.md)
- 화면 연결: [화면별 수용 기준](docs/handoff/SCREEN_ACCEPTANCE_MAP.md)
- 구조: [아키텍처 기준](docs/handoff/ARCHITECTURE_BASELINE.md)
- 원본 전달: [비공개 자료 전달 방법](docs/handoff/PRIVATE_INPUT_DELIVERY.md), [준비만 진행하는 요청문](docs/handoff/PREPARATION_ONLY_REQUEST.md)
- 이후 개발 승인 시 실행: [Cloud 준비 절차](docs/handoff/CLOUD_SETUP.md), [첫 개발 요청문](docs/handoff/FIRST_CLOUD_TASK.md)
- 원본: [자료 인덱스](docs/handoff/SOURCE_INDEX.md), [데이터 반입 계약](docs/handoff/DATA_IMPORT_CONTRACT.md)

저장소는 공개 유지한다. 새로 작성한 화면 코드·일반 실행 도구·인계 요약만 배치한다. 원본 HTML/스크립트를 통째로 복사하지 않았으며, 공식 원문·공유 목업·실제 BOM·원본 파생 시연 데이터는 별도의 비공개 입력으로 공급한다. 공개 배포 허용이 확인되지 않은 원본은 공개 산출물에 포함하지 않는다.

첫 구현은 T00 기준 확인과 T01 실행 골격부터 진행한다. 원본이 아직 제공되지 않은 환경에서는 자료 점검을 완료로 처리하지 않는다. 없는 원본을 가짜 파일로 대체해 화면·BOM 검증을 통과시키지 않는다.
