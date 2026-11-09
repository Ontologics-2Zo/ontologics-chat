# 로컬 실행과 Codex Cloud 환경 안내

> 최신 인계(2026-11-10): DEC-H38은 PR #4 최종 검토·검증 후 main 병합과 기존 설정의 Mac 앱 확인 범위다. [검토 기록](SERVICE-PR4-REVIEW-20261110.md)과 [GitHub PR #4](https://github.com/Ontologics-2Zo/ontologics-chat/pull/4)의 실제 병합 결과를 먼저 확인한다. 병합 완료 후 main으로 이어가며 `.env`·DB runtime·예산 이력을 보존한다. 아래 이전 실행 수치는 당시 증거다.


2026-11-09 갱신. 초기 원장·실제 BOM·자연어 CQ는 PR #1~#3으로 `main`에 병합됐다. PR #2 기준은 `2e6f24c`, PR #3 기준은 `848fe79`이며 CQ의 병합·Mac main 재시작 증거는 [CQ 실행 기록](SERVICE-CQ-NL-20261108.md)에 보존한다. Agent의 선행 280 Python·12 Node/Fake 관측 증거를 보존하며, 최신 연결 수정의 286 Python 검사와 실제 모델1회·원장 조회를 확인했다. T04~T07 부모는 모두 IN_PROGRESS이며 T00~T23 전체 집계는 IN_PROGRESS 14 / NOT_STARTED 10 / VERIFIED 0이다. 과거 초기화·환경 Publish 절차를 현재 재시작에 적용하지 않는다.

Linux Cloud의 초기 조회 검증은 [INITIAL 실행 기록](SERVICE-INITIAL-20261108.md), 과거 Cloud BOM 차단은 [BOM 실행 기록](SERVICE-BOM-20261108.md)을 따른다. 후속 Mac arm64에서는 실제 Runner 강제 검사와 원본 74행·선택 5품목 조건부 검토/승인/반영을 확인했다. 최신 UI·보존·재시작 검증 결과는 [Mac BOM 실행 기록](SERVICE-BOM-MAC-20261108.md)을 따른다. 구매·Agent·모델·전체 CQ 완료를 의미하지 않는다.

## 1. Mac에서 준비할 것

- Git, `uv`, Python 3.12. `uv`가 없으면 [공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를 따른다. Python은 `uv python install 3.12`로 준비할 수 있다.
- [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/)을 열고 엔진 시작을 기다린다.
- 기존 **전체 비공개 입력 묶음**을 Git 밖에 둔다. `SOURCE_MANIFEST.json`과 `v1.7/`, `mk-s2/`, `cq-v1.1/`, `full-delivery-v1.3/` 등의 기존 묶음이다. 전달·hash 기준은 [PRIVATE_INPUT_DELIVERY](PRIVATE_INPUT_DELIVERY.md)를 따른다.

결정적 조회와 Fake 실행에는 유료 모델 호출이 필요 없다. Node는 UI 검사에, Chromium은 별도 브라우저 자동 검사에 사용한다. 현재 제공된 `.env`와 기존 로컬 Langfuse 설정은 보존하며 키를 출력하거나 예시 파일로 덮어쓰지 않는다.

## 2. 코드 받기

초기 원장 서비스를 처음 받는 경우:

```bash
git clone --branch main https://github.com/Ontologics-2Zo/ontologics-chat.git
cd ontologics-chat
```

이미 받은 저장소는 먼저 실제 작업 트리와 PR #4 병합 여부를 확인한다. 미커밋·미추적 파일을 보존하며 강제 초기화하지 않는다.

```bash
git status --short
git branch --show-current
git log -1 --oneline
```

PR #4가 병합됐다면 기존 변경을 보존한 상태에서 main과 origin/main을 대조하고 fast-forward 가능한 경우에만 갱신한다. 병합 전이라면 PR의 실제 head 브랜치에서 이어간다. 과거 `codex/agent-foundation`으로 돌아가거나 기존 DB를 초기화하지 않는다. 앱 재시작은 현재 runtime·환경 파일과 영속 예산을 그대로 사용한다.

```bash
git switch codex/agent-foundation
```

이하 명령은 선택한 저장소 루트에서 실행한다.

## 3. 비공개 입력과 영속 실행 설정

기존 환경에서는 이미 확인한 `PRIVATE_INPUT_ROOT`·runtime 파일·Runner 변수를 재사용한다. 아래 경로 설정은 **처음 준비하는 환경의 예시**이며 현재 runtime을 바꾸는 지시가 아니다. Cloud 파일은 Mac에 자동 복사되지 않는다.

```bash
export PRIVATE_INPUT_ROOT="$HOME/Private/ontologics/private_inputs"
export ONTO_RUNTIME_DIR="$HOME/.local/share/ontologics"
mkdir -p -m 700 "$ONTO_RUNTIME_DIR"
test -r "$PRIVATE_INPUT_ROOT/SOURCE_MANIFEST.json"
uv sync --locked --python 3.12
.venv/bin/python dev/audit_sources.py > "$ONTO_RUNTIME_DIR/source-audit.json"
```

자료 검사 실패 시 입력 위치/묶음을 확인한다. 빈 파일·목업으로 대체하지 않는다. 검사는 원문을 변경하지 않는다.

`runtime.json`은 로컬 DB 접속 자격증명을 자동 생성해 저장한다. Git 밖 영속 폴더에 보관하고 **삭제하거나 공유하지 않는다**. `/tmp`는 재부팅·정리 시 없어질 수 있어 지속 사용 경로로 쓰지 않는다. DB는 별도 Docker 볼륨에 남으므로 runtime 파일만 잃어버리면 자격증명이 맞지 않게 된다. 기존 runtime 파일이 있으면 새로 만들지 말고 그 파일을 계속 지정한다.

Agent 설정은 `--agent-env-file .env`로 기존 파일을 명시적으로 읽는다. `.env.example`은 키가 없는 공개 예시이며 기존 `.env`를 덮어쓰는 용도가 아니다. 로더는 쉘 실행·변수 확장 없이 허용된 Agent 설정만 읽고 DB·Neo4j·비공개 입력 설정은 반입하지 않는다. DB 자격증명은 계속 `--runtime-file`이 기준이다. 공개 예제 기본값은 Fake다. 현재는 사용자 충전·재개 요청에 따라 실행 환경의 `ONTO_MODEL_MODE=live`로 연결했으며 `.env` 원본은 수정하지 않았다.

실제 모델 POST 1회 시도는 실패했고 크레딧 조회의 total credits/usage는 모두 0이었다. canonical 결과는 `outcome_unknown`이며 held reservation은 보존한다. 이 기록은 당시 보류 사유다. 이후 충전·재개 결과는 SERVICE-MODEL-20261109에 기록했다. 미확정 예약·과거 비용을 환급/초기화하지 않으며 자동 공급사/model fallback OFF를 유지한다.

## 4. PostgreSQL·Neo4j 시작

도구는 저장된 원격 Docker context를 무시하고 명시적인 로컬 Unix 소켓만 사용한다. Docker Desktop의 소켓이 사용자 폴더에만 있는 경우도 지원한다.

```bash
ONTO_DOCKER_SOCKET="/var/run/docker.sock"
if [ ! -S "$ONTO_DOCKER_SOCKET" ]; then
  ONTO_DOCKER_SOCKET="$HOME/.docker/run/docker.sock"
fi
docker --host="unix://$ONTO_DOCKER_SOCKET" info --format '{{.ServerVersion}}'
.venv/bin/python dev/service_stack.py up \
  --docker-socket "$ONTO_DOCKER_SOCKET" \
  --runtime-file "$ONTO_RUNTIME_DIR/runtime.json"
```

첫 실행은 PostgreSQL 17·Neo4j 5.26 이미지를 내려받는다. 기존 컨테이너·볼륨·자격증명은 확인 후 재사용하며 초기화하지 않는다. 두 서비스가 `true`이면 앱을 실행한다.

기본 포트는 PostgreSQL `55432`, Neo4j HTTP `57474`, Neo4j Bolt `57687`이다. 앱 실행기도 이 기본 DB 포트를 사용하므로 이 안내에서는 변경하지 않는다.

## 4-1. 실제 BOM용 제한 Runner 준비

BOM 기능이 포함된 코드에서만 수행한다. DB를 초기화하거나 비공개 입력을 Docker build context에 넣지 않는다. 빌드는 공개 `runner/` 코드로 제한되며 Runner 실행은 이미지 ID를 고정하고 네트워크·권한·입출력·시간/자원을 제한한다.

```bash
.venv/bin/python dev/build_bom_runner.py \
  --docker-socket "$ONTO_DOCKER_SOCKET" \
  --output "$ONTO_RUNTIME_DIR/bom-runner.json"
```

빌드 출력이 `runner: READY`, `enforcement: PASS`이고 저장된 metadata의 `ready`가 `true`여야 다음 설정과 실제 BOM 접수를 진행한다. `RUNNER_ENFORCEMENT_UNAVAILABLE`이면 Docker의 메모리·PID·CPU 제한이 지원되지 않은 것이므로 중단한다. 과거 Cloud에서는 이 차단으로 실제 원본을 파싱하지 않았으며, 후속 Mac에서는 통과 후 원본 74행을 등록했다. 기존 검증 이미지와 metadata를 재사용하고 호스트 무제한 파싱으로 우회하지 않는다.

```bash
export ONTO_BOM_RUNNER_IMAGE="$(.venv/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["image_id"])' "$ONTO_RUNTIME_DIR/bom-runner.json")"
export ONTO_BOM_DOCKER_SOCKET="$ONTO_DOCKER_SOCKET"
```

이 변수들을 설정한 같은 터미널에서 앱을 시작한다. 새 터미널이나 재시작 시 저장된 metadata의 같은 이미지 ID를 다시 읽는다. Runner 설정이 없거나 사용할 수 없으면 `RUNNER_UNAVAILABLE`이며 호스트 파서로 대체하지 않는다. CSV/ODS는 행 근거를 추출하고 텍스트 PDF는 텍스트 근거만 추출한다. PDF 텍스트 추출 성공을 구조화 BOM 검수 완료로 취급하지 않는다. 지원·실행 검증 범위는 최신 BOM 기록을 확인한다.

## 5. 앱 시작·접속

아래는 기존 DB를 사용하는 Fake 재시작 명령이다. 현재 승인된 live 재시작은 이미 검증한 같은 Agent 예산 환경을 유지하고 `ONTO_MODEL_MODE=live`로 바꾼다. 예산ID `mks2-agent-20261108`, 승인참조 `DEC-H29`, 해당 scope 최초 이전사용액0은 `.env.example`의 필드명을 따른다. 이 설정으로 기존 예산 행이나 사용액을 초기화하지 않는다. 저장된 runtime과 기존 입력·Runner 환경을 지정하며 `--import-initial`을 추가하지 않는다.

```bash
ONTO_MODEL_MODE=fake .venv/bin/python -m service \
  --runtime-file "$ONTO_RUNTIME_DIR/runtime.json" \
  --agent-env-file .env \
  --dev-mode --reference-preview --port 8870
```

실행한 **그 Mac**의 브라우저에서 [http://127.0.0.1:8870/](http://127.0.0.1:8870/)을 연다. 현재는 정확한 Host/Origin을 검사하므로 이 명령에서는 `localhost` 대신 `127.0.0.1`을 사용한다.

1. `현재 구현`은 보존된 참조 화면이다.
2. `MK-S2`에서 개발 계정 **작성자 A** 또는 **검토자 B**를 선택한다. `다른 회사 G`는 다른 회사이므로 초기 원장을 보여주지 않는다.
3. 원장/그래프 버전, 전체 온톨로지, 대시보드 수량, 추천 질문의 서버 근거 조회를 확인한다. 그래프의 직접 CQ는 결정적 조회를 유지하며 대시보드 업무 에이전트는 Fake 라우팅·전문 조회·진행 상태·질문 확인 재개를 사용한다. 이를 실제 모델 품질이나 승인/원장 반영으로 표시하지 않는다.
4. BOM 기능을 사용하는 경우 드로어에서 원본을 접수하고 전체 행과 검토 결정을 확인한다. 작성자와 다른 검토 계정으로 정확 버전을 검토·승인한 뒤 원장에 반영한다. 승인과 반영은 별도 단계다.
5. 반영 후 최신 원장을 조회해 소요·부족과 전체 행 coverage를 확인한다. 실제 원문, 합성 운영 잔액, 조건부 가정, 미확인 항목을 구분하며 구성 충돌·매핑·단위·재고 미확인을 임의 확정하지 않는다.

`--import-initial`은 빈 개발 원장을 처음 설치할 때만 별도로 사용하는 옵션이다. 현재 DB의 재시작·검증에는 사용하지 않는다. `--reference-preview`는 보존 탭용이다. MK-S2는 참조 fixture를 원장으로 사용하지 않으며 위 명령은 유료 모델을 호출하지 않는다. 이 실행 모드와 과거 실패한 실제 호출 1회의 누적 기록은 구분한다.

## 6. 상태 확인·종료·재시작

다른 터미널에서는 필요한 변수들을 다시 지정한 뒤:

```bash
.venv/bin/python dev/service_stack.py status \
  --docker-socket "$ONTO_DOCKER_SOCKET" \
  --runtime-file "$ONTO_RUNTIME_DIR/runtime.json"
curl --fail http://127.0.0.1:8870/health
```

앱은 실행 터미널에서 `Ctrl+C`로 종료한다. DB는 계속 실행되며 데이터를 유지한다. DB까지 잠시 중지하려면:

```bash
docker --host="unix://$ONTO_DOCKER_SOCKET" stop ontologics-pg ontologics-neo4j
```

다음 실행 때 같은 입력·runtime·Agent 설정을 지정하고 4~5단계를 반복한다. 현재 Agent 브랜치와 미커밋/로컬 커밋을 보존하고 `uv sync --locked --python 3.12`로 그 작업 트리의 의존성을 맞춘다. 현재 작업을 이어가는 도중 임의로 main을 pull하거나 브랜치를 바꾸지 않는다. 컨테이너·볼륨·runtime·checkpoint·비용 예약을 지워 재설정하지 않는다.

## 자주 막히는 지점

| 표시 | 확인할 것 |
|---|---|
| `LOCAL_DOCKER_SOCKET_UNAVAILABLE` / `LOCAL_DOCKER_UNAVAILABLE` | Docker Desktop 실행과 실제 소켓 경로 확인 |
| `RUNNER_ENFORCEMENT_UNAVAILABLE` | Docker 메모리·PID·CPU 제한 지원이 필요. 지원되는 실행 환경에서 probe를 다시 통과한 뒤 실제 원본 접수 |
| `RUNNER_UNAVAILABLE` / Runner 이미지 오류 | 명시적 로컬 Docker 소켓·빌드한 이미지 ID·Runner 환경변수 확인. 호스트 파싱으로 우회하지 않음 |
| `PRIVATE_INPUT_UNAVAILABLE` / `INITIAL_SOURCE_*` / schema 오류 | 기존 전체 원본·manifest 및 `PRIVATE_INPUT_ROOT` 확인. Agent `.env` 로더는 비공개 입력·DB 설정을 대신하지 않음 |
| `EXISTING_CONTAINER_REQUIRES_ORIGINAL_RUNTIME_FILE` / `EXISTING_VOLUME_REQUIRES_ORIGINAL_RUNTIME_FILE` | 기존 DB에 사용한 원래 runtime 파일 지정. 삭제·새 비밀번호로 덮어쓰기 금지 |
| `EXISTING_CONTAINER_*_MISMATCH` | 같은 이름의 기존 컨테이너가 다른 구성인지 확인. 자동 삭제하지 않음 |
| DB 준비 상태 `false` | 초기 시작을 기다린 뒤 같은 설정의 `status`/`up` 재실행 |
| `LOCAL_ORIGIN_REQUIRED` | `http://127.0.0.1:8870/`로 접속 |
| `LEDGER_NOT_INITIALIZED` | 초기 개발 회사 계정 및 최초 `--import-initial` 확인 |
| `PROJECTION_NOT_READY` / `PROJECTION_READ_UNAVAILABLE` | Neo4j 상태 확인. fixture나 오래된 결과로 대체하지 않음 |
| 모델 잔액 부족·실제 호출 실패 | 반복 호출을 중단하고 원인·기존 예산·미확정 attempt를 확인. reservation을 임의 해제하지 않음. 2026-11-09 성공한 현재 설정과 과거 실패를 구분 |
| Agent `WAITING` / 재개 시 버전·권한 오류 | 질문과 업무를 확인해 같은 실행을 재개. 현재 세션·소유자·원장/정책 버전을 서버가 다시 검사하며 오래된 권한·승인을 우회하지 않음 |
| 포트 사용 중 | 앱 `8870`과 DB 기본 포트 충돌 확인. 앱 포트를 바꾸면 URL도 같은 포트 사용 |

## Codex Cloud에서 실행할 때

환경에 제공된 기존 `PRIVATE_INPUT_ROOT`·runtime 파일·DB를 먼저 확인하고 유지한다. 기본 Docker 소켓은 `/var/run/docker.sock`이다. uv cache 쓰기 권한이 없으면 `UV_CACHE_DIR`를 쓰기 가능한 별도 경로로 지정한다.

Cloud 내부 `127.0.0.1`은 사용자 Mac의 주소가 아니다. 로컬 실행에 Cloud 환경 Publish는 필요 없다. 모델·관측은 현재 실행과 분리하여 DEC-H18/19의 기존 승인·예산·전송 제한을 유지한다. 비공개 원문·키·DB·캡처는 공개 Git/PR/CI artifact에 넣지 않는다.

## 과거 안내 갱신의 검증 — LOCAL-SETUP-20261108

아래는 기준 커밋 `03c640c` 당시 문서와 `dev/service_stack.py`의 로컬 소켓 선택 검증이다. 현재 Agent 코드의 검증 수나 Mac 실행 상태를 뜻하지 않는다. 선행 280 Python/12 Node·Fake 검증은 [Agent 기록](SERVICE-AGENT-20261108.md), 최신 286 Python·실제 연결은 [재연결 기록](SERVICE-MODEL-20261109.md)을 따른다.

- `.venv/bin/python -m unittest discover -s tests -p test_service_stack.py -v`: 3 PASS. 실제 Unix 소켓/심볼릭 링크, 잘못된 경로·원격 URL 차단, 원격 Docker 환경변수 배제를 확인했다.
- 기본 소켓의 기존 스택 `status`: PostgreSQL/Neo4j 모두 `true`, DB reset 없음.
- 별도 빈 시험 스택 `up --name ontologics-launcher-check --postgres-port 55532 --neo4j-http-port 57574 --neo4j-bolt-port 57787 --docker-socket /var/run/docker.sock --runtime-file <private-runtime>`: Linux에서 최초 자격증명 생성·컨테이너 생성·양쪽 DB 인증 연결 PASS. 사용자 원본을 넣지 않았고 시험 자원만 정리했다.
- Python 구문·`git diff --check` PASS. 기존 서비스 124/브라우저 27 증거는 `03c640c` 범위로 보존하며 전체 검사를 이번 실행 결과로 다시 집계하지 않는다. Mac 실기는 미실행이다.
