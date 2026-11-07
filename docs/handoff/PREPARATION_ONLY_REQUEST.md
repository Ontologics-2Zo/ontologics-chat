# Cloud 환경과 원본 접근만 준비하는 요청문

아래 내용은 비공개 보관 위치·읽기 인증을 정한 뒤 사용한다. 이 문서 작성은 Cloud 작업 실행이나 서비스 개발을 시작하지 않는다.

---

`Ontologics-2Zo/ontologics-chat`의 문서와 비공개 기준 자료를 읽을 수 있도록 환경만 준비해 주세요. 서비스 프론트·백엔드·DB schema·seed·Agent 구현은 시작하지 마세요.

1. 연결 저장소가 정확히 `Ontologics-2Zo/ontologics-chat`인지 확인하세요. 이름이 비슷한 `Ontologics-2Zo/-ontologics-chat`을 대상으로 진행하지 마세요.
2. 공개 저장소의 AGENTS.md와 docs/handoff/PRIVATE_INPUT_DELIVERY.md를 읽으세요. 현재 요청의 준비 범위를 우선하고 FIRST_CLOUD_TASK.md의 구현 요청을 자동 실행하지 마세요.
3. 사용자가 지정한 비공개 보관소·읽기 인증으로 승인된 원본 ZIP만 받으세요. 보관 위치나 인증이 없으면 필요한 입력을 알려주고 원본 전송은 기다리세요. URL·키·원문을 공개 Git/로그에 쓰지 마세요.
4. 원본을 Git과 Docker build context 밖에 두고 ZIP SHA-256 및 개별 SOURCE_MANIFEST.json을 대조하세요. PRIVATE_INPUT_ROOT가 정확한 private_inputs 폴더를 가리키게 하세요.
5. MK-S2 HTML·규약, 공식 3문서, 데이터 manifest를 읽을 수 있는지 확인하세요. 원본 분석 스크립트·BOM 파서·초기화 도구를 실행하지 마세요.
6. 설치나 환경 변경이 필요하면 이 준비 요청에 필요한 항목만 다루세요. 준비 상태를 사용할 접근 대상과 새 작업의 원본 접근 재현 여부를 확인하세요.
7. 저장소·자료 버전·검증 성공/실패·미준비 항목을 보고하고 중지하세요. 실제 LLM 호출, 데이터 평가 실행, 공급사 송신, 배포, 서비스 구현은 수행하지 마세요.
