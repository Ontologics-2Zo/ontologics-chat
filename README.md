# ontologics-chat 개발 인계

MK-S2의 화면·핵심 동선을 유지하면서 제조 업무를 실제 서버 원장·온톨로지·Agent에 연결하기 위한 개발 기준이다. 공식 설계 v1.7과 시연 데이터 v1.3은 서로 다른 버전 축이다.

**현재 범위: 공개 인계 문서 배치와 원본 전달 방법 정리. 서비스 구현·원본 Cloud 전달·실제 AI 인수는 시작하지 않는다.**

- 시작: [개발 인계 안내](docs/handoff/START_HERE.md)
- 작업 순서: [구현 작업표](docs/handoff/IMPLEMENTATION_TASKS.md)
- 화면 연결: [화면별 수용 기준](docs/handoff/SCREEN_ACCEPTANCE_MAP.md)
- 구조: [아키텍처 기준](docs/handoff/ARCHITECTURE_BASELINE.md)
- 원본 전달: [비공개 자료 전달 방법](docs/handoff/PRIVATE_INPUT_DELIVERY.md), [준비만 진행하는 요청문](docs/handoff/PREPARATION_ONLY_REQUEST.md)
- 이후 개발 승인 시 실행: [Cloud 준비 절차](docs/handoff/CLOUD_SETUP.md), [첫 개발 요청문](docs/handoff/FIRST_CLOUD_TASK.md)
- 원본: [자료 인덱스](docs/handoff/SOURCE_INDEX.md), [데이터 반입 계약](docs/handoff/DATA_IMPORT_CONTRACT.md)

저장소는 공개 유지한다. 이 저장소에는 새로 작성한 인계 문서와 추적 정보만 배치한다. 공식 문서 원문·공유 목업·실제 BOM·원본 파생 시연 데이터는 별도의 비공개 입력으로 공급한다. 공개용 HTML·fixture·실행 결과를 추가하려면 원본 포함 여부와 재배포 조건을 확인한다.

첫 구현은 T00 기준 확인과 T01 실행 골격부터 진행한다. 원본이 아직 제공되지 않은 환경에서는 자료 점검을 완료로 처리하지 않는다. 없는 원본을 가짜 파일로 대체해 화면·BOM 검증을 통과시키지 않는다.
