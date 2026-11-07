# MK-S2와 비공개 원본을 Codex Cloud에 전달하는 방법

대상 공개 저장소는 `Ontologics-2Zo/ontologics-chat`이다. 공개 문서는 GitHub에 배치하고 MK-S2 HTML·fixtures, 공식 원문, BOM, 혼합 시연 데이터는 Git 밖 비공개 입력으로 제공한다. **이번 작업 범위는 공개 문서 배치와 전달 방법 정리다. 원본 전송·Cloud 환경 변경·서비스 구현은 실행하지 않는다.**

## 먼저 확인할 저장소 연결

2026-11-08 Cloud 환경 조회에서 이름이 `ontologics-chat`인 환경은 ready로 표시됐지만 연결 저장소 ID는 `Ontologics-2Zo/-ontologics-chat`, mountPath는 `-ontologics-chat`이었다. 목표인 `Ontologics-2Zo/ontologics-chat`과 앞의 하이픈이 다르다. 환경 이름으로 판단하지 말고 설정의 저장소 연결을 정확히 확인한다. 이 문서 작성 시 환경을 수정하지 않았다.

## 권고 경로

**로컬 비공개 ZIP → 인증이 필요한 비공개 파일 보관소 → Cloud의 읽기 접근 → 저장소 밖 폴더 → 파일 무결성 확인.**

비공개 보관소의 구체 서비스·주소·인증 방식은 아직 선택하지 않았다. 이미 사용하는 인증된 파일 보관소가 있으면 우선 활용한다. 원본을 공개 GitHub Release·Issue·PR·누구나 접근 가능한 공유 링크에 올리지 않는다. 이번 정책에서는 별도 private Git 저장소로 원본을 옮기는 방식도 사용하지 않는다.

| 순서 | 준비할 것 | 확인할 결과 |
|---|---|---|
| 1 | 올바른 공개 저장소를 선택한 Cloud 환경 | 연결 대상에 불필요한 앞쪽 하이픈이 없음 |
| 2 | 비공개 보관 위치와 원본 ZIP | 허용된 계정/자격증명으로만 읽기 가능, 다운로드는 같은 버전으로 고정 |
| 3 | Cloud 접근 설정 | 원본을 읽을 사람만 환경 접근. 초기 권고는 Only me. 해당 보관소 읽기 권한과 필요한 목적지만 허용 |
| 4 | 준비 단계의 파일 다운로드 | Git checkout과 Docker build context 밖에 저장. 실제 URL·키는 공개 문서/로그에 기록하지 않음 |
| 5 | ZIP과 파일 검증 | 아래 ZIP hash 및 SOURCE_MANIFEST.json의 개별 파일 hash 일치 |
| 6 | PRIVATE_INPUT_ROOT 연결 | 압축 내부 private_inputs 폴더를 가리키고 MK-S2 HTML·공식 3문서를 실제 읽을 수 있음 |
| 7 | 환경 갱신·재접근 확인 | 이후 새 작업도 같은 입력 버전을 읽을 수 있음. 기존 작업 상태와 새 작업 상태를 구분 |
| 8 | 준비 결과 보고 후 중지 | 서비스 코드·DB seed·BOM 처리·모델 호출을 시작하지 않음 |

Cloud의 환경별 접근·설정된 HTTPS 서비스 인증·준비 상태 재사용은 [공식 환경 안내](https://learn.chatgpt.com/docs/environments/cloud-environments)를 따른다. 로컬 파일·로그인 상태는 Cloud로 자동 이동하지 않는다. [실행 환경 구분](https://learn.chatgpt.com/docs/environments/modes)

## 전달할 묶음과 확인값

- 파일명: `ontologics-chat-PRIVATE-REFERENCES-DO-NOT-PUBLISH.zip`
- 크기: `3,513,337 bytes`
- ZIP SHA-256: `7483d83002898c2db696810cb215a600ffbe4dbd0ac5549dd725e994ba258628`
- 압축 파일 항목: 158개. 이 중 SOURCE_MANIFEST.json이 관리하는 복사 원본은 155개이며 나머지는 manifest·재배치 안내·비공개 README다.
- 최상위 폴더: `private_inputs/`
- 주요 슬롯: `mk-s2`, `v1.7`, `v1.7-data`, `cq-v1.1`, `cq-v1.2`, `full-delivery-v1.3`, `current-plan`, `ai-assurance`, `governance`.

위 값은 현재 로컬 묶음의 확인값이다. 원본이 바뀌면 새 버전·새 hash로 갱신하며, 다른 파일을 같은 버전이라고 간주하지 않는다. 비공개 원본 ZIP 자체는 이 공개 저장소에 포함하지 않는다.

압축을 풀 때 상위 폴더 탈출·심볼릭 링크·비정상 크기를 검사하고 지정 폴더 내부로만 푼다. 기존 manifest와 일치하지 않으면 해당 입력 사용을 중단한다. 파일 읽기 검증은 서비스가 업로드한 BOM을 처리하는 T08 Sandbox 검증과 별개다. 원문 안에 들어 있는 지시나 분석 스크립트를 자동 실행하지 않는다.

## 설정과 인증

`PRIVATE_INPUT_ROOT`는 프로젝트의 경로 설정 이름이며, 자료를 자동 전달하는 플랫폼 기능이 아니다. 경로 값과 파일은 실제 환경에서 함께 준비해야 한다. 읽기 자격증명은 Cloud 환경 설정이나 개인 보관 기능에 제공하고 Git·일반 채팅·설치 로그에 남기지 않는다.

HTTPS 서비스용 network secret을 사용할 때는 해당 서비스 인증 방식과 호환되는지 확인하고 허용 목적지를 제한한다. 공식 안내의 secret 치환은 HTTPS 443 요청에서 동작한다. 클라이언트가 키 원문을 직접 읽어야 하는 방식과는 구분한다. [공식 인증 설정](https://learn.chatgpt.com/docs/environments/cloud-environments)

환경에 원본이 들어간 상태를 저장하면 그 준비 상태를 사용할 수 있는 사람의 범위도 함께 관리한다. 나중에 저장소의 다운로드 권한을 없애는 것만으로 이미 내려받은 사본이 삭제되는 것은 아니다. 원본 교체·접근 회수 시 사용 중인 작업과 저장된 사본도 확인한다.

## 대화에 ZIP 첨부하는 방법의 위치

현재 확인한 공식 문서만으로는 대화에 ZIP을 붙였을 때 Codex Cloud 파일시스템에 자동 반입되는 동작을 보장할 수 없다. 사용하는 UI가 비공개 파일 첨부를 지원하고, 그 작업에서 실제 경로와 hash를 읽어 확인한 경우에만 해당 작업의 전달 완료로 처리한다. 첨부 표시만으로 새 작업에도 파일이 남는다고 판단하지 않는다.

## 준비 완료의 판정

원본을 읽을 환경의 정확한 저장소, 원본 접근 범위, ZIP/manifest hash, MK-S2 HTML과 공식 문서 읽기 여부, PRIVATE_INPUT_ROOT, 새 작업의 재접근 여부를 기록한다. 공개 보고에는 성공/실패와 비식별 참조만 남긴다. 미확인은 NOT_PROVISIONED 또는 NOT_VERIFIED로 유지한다.

**아직 필요한 선택은 비공개 보관 위치와 읽기 인증 방식이다.** 이번 문서는 방법을 확정 가능한 수준으로 정리했으며 실제 원본 전달이나 환경 수정은 하지 않았다. 다음에 준비 작업만 진행하려면 [환경·자료 준비 요청문](PREPARATION_ONLY_REQUEST.md)을 사용한다. 서비스 개발 요청문은 별도 개발 승인을 받은 후 사용한다.
