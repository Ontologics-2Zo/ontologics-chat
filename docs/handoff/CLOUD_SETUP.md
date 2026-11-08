# 로컬 실행과 Codex Cloud 환경 안내

2026-11-08 갱신. 이번 PR 병합 후 `main`을 기준으로 사용하는 **INITIAL 원장 조회 서비스** 실행 안내다. 작업 브랜치는 `codex/mks2-ui-tabs`이며 병합 상태는 GitHub PR에서 확인한다. 과거 저장소 초기화·T00~T01 준비·환경 Publish 절차는 현재 로컬 실행 절차가 아니다. Cloud 환경 Publish는 로컬 실행에 필요하지 않다.

Linux Cloud 검증은 [실행 기록](SERVICE-INITIAL-20261108.md)을 따른다. 아래 Mac 절차는 현재 코드에 맞춘 안내이며 **Apple Silicon Mac 실기 실행은 NOT_RUN**이다. 실제 BOM·구매·Agent·모델·전체 CQ 완료를 의미하지 않는다.

## 1. Mac에서 준비할 것

- Git, `uv`, Python 3.12. `uv`가 없으면 [공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를 따른다. Python은 `uv python install 3.12`로 준비할 수 있다.
- [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/)을 열고 엔진 시작을 기다린다.
- 기존 **전체 비공개 입력 묶음**을 Git 밖에 둔다. `SOURCE_MANIFEST.json`과 `v1.7/`, `mk-s2/`, `cq-v1.1/`, `full-delivery-v1.3/` 등의 기존 묶음이다. 전달·hash 기준은 [PRIVATE_INPUT_DELIVERY](PRIVATE_INPUT_DELIVERY.md)를 따른다.

일반 실행에는 Node, 모델 API 키, Cloud 환경 Publish가 필요 없다. Node/Chromium은 별도 브라우저 자동 검사에만 필요하다.

## 2. 코드 받기

이번 PR 병합 후에는 `main`에서 받는다. 처음 받는 경우:

```bash
git clone --branch main https://github.com/Ontologics-2Zo/ontologics-chat.git
cd ontologics-chat
```

이미 받은 저장소에서는 다음을 사용한다. 로컬 변경 때문에 중단되면 변경을 보존하고 해결하며 강제 초기화하지 않는다.

```bash
git fetch origin
git switch main
git pull --ff-only origin main
```

이하 명령은 저장소 루트에서 실행한다.

## 3. 비공개 입력과 영속 실행 설정

`PRIVATE_INPUT_ROOT`는 **실제로 보관한 비공개 폴더**로 바꾼다. 예시 경로를 지정하는 것만으로 자료가 생성되지는 않는다. Cloud 파일은 사용자 Mac에 자동 복사되지 않는다.

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

## 5. 앱 시작·접속

```bash
.venv/bin/python -m service \
  --runtime-file "$ONTO_RUNTIME_DIR/runtime.json" \
  --dev-mode --reference-preview --import-initial --port 8870
```

실행한 **그 Mac**의 브라우저에서 [http://127.0.0.1:8870/](http://127.0.0.1:8870/)을 연다. 현재는 정확한 Host/Origin을 검사하므로 이 명령에서는 `localhost` 대신 `127.0.0.1`을 사용한다.

1. `현재 구현`은 보존된 참조 화면이다.
2. `MK-S2`에서 개발 계정 **작성자 A** 또는 **검토자 B**를 선택한다. `다른 회사 G`는 다른 회사이므로 초기 원장을 보여주지 않는다.
3. 원장/그래프 버전, 전체 온톨로지, 대시보드 수량, 추천 질문의 서버 근거 조회를 확인한다.

`--import-initial`은 기초 사실을 빈 개발 원장에 반입한다. 같은 입력으로 재시작하면 기존 반입을 재사용하며, 변경 입력/기존 원장과 충돌하면 덮어쓰지 않고 중단한다. `--reference-preview`는 보존 탭용이다. MK-S2는 참조 fixture를 사용하지 않고 이 실행에는 실제 모델 호출이 없다.

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

다음 실행 때 같은 입력·runtime 파일을 지정하고 4~5단계를 반복한다. 코드 갱신은 앱을 종료한 상태에서 `git pull --ff-only origin main`과 `uv sync --locked --python 3.12`로 한다. 컨테이너·볼륨·runtime 파일을 지워 재설정하지 않는다.

## 자주 막히는 지점

| 표시 | 확인할 것 |
|---|---|
| `LOCAL_DOCKER_SOCKET_UNAVAILABLE` / `LOCAL_DOCKER_UNAVAILABLE` | Docker Desktop 실행과 실제 소켓 경로 확인 |
| `PRIVATE_INPUT_UNAVAILABLE` / `INITIAL_SOURCE_*` / schema 오류 | 전체 원본·manifest 위치 확인. `.env`는 자동 로드되지 않으므로 `export` 사용 |
| `EXISTING_CONTAINER_REQUIRES_ORIGINAL_RUNTIME_FILE` / `EXISTING_VOLUME_REQUIRES_ORIGINAL_RUNTIME_FILE` | 기존 DB에 사용한 원래 runtime 파일 지정. 삭제·새 비밀번호로 덮어쓰기 금지 |
| `EXISTING_CONTAINER_*_MISMATCH` | 같은 이름의 기존 컨테이너가 다른 구성인지 확인. 자동 삭제하지 않음 |
| DB 준비 상태 `false` | 초기 시작을 기다린 뒤 같은 설정의 `status`/`up` 재실행 |
| `LOCAL_ORIGIN_REQUIRED` | `http://127.0.0.1:8870/`로 접속 |
| `LEDGER_NOT_INITIALIZED` | 초기 개발 회사 계정 및 최초 `--import-initial` 확인 |
| `PROJECTION_NOT_READY` / `PROJECTION_READ_UNAVAILABLE` | Neo4j 상태 확인. fixture나 오래된 결과로 대체하지 않음 |
| 포트 사용 중 | 앱 `8870`과 DB 기본 포트 충돌 확인. 앱 포트를 바꾸면 URL도 같은 포트 사용 |

## Codex Cloud에서 실행할 때

환경에 제공된 기존 `PRIVATE_INPUT_ROOT`·runtime 파일·DB를 먼저 확인하고 유지한다. 기본 Docker 소켓은 `/var/run/docker.sock`이다. uv cache 쓰기 권한이 없으면 `UV_CACHE_DIR`를 쓰기 가능한 별도 경로로 지정한다.

Cloud 내부 `127.0.0.1`은 사용자 Mac의 주소가 아니다. 로컬 실행에 Cloud 환경 Publish는 필요 없다. 모델·관측은 현재 실행과 분리하여 DEC-H18/19의 기존 승인·예산·전송 제한을 유지한다. 비공개 원문·키·DB·캡처는 공개 Git/PR/CI artifact에 넣지 않는다.

## 이번 안내 갱신의 검증 — LOCAL-SETUP-20261108

기준 서비스 커밋은 `03c640c`다. 이번 변경은 문서와 `dev/service_stack.py`의 명시적 로컬 소켓 선택이며 앱·UI·원장·온톨로지 코드는 그대로다.

- `.venv/bin/python -m unittest discover -s tests -p test_service_stack.py -v`: 3 PASS. 실제 Unix 소켓/심볼릭 링크, 잘못된 경로·원격 URL 차단, 원격 Docker 환경변수 배제를 확인했다.
- 기본 소켓의 기존 스택 `status`: PostgreSQL/Neo4j 모두 `true`, DB reset 없음.
- 별도 빈 시험 스택 `up --name ontologics-launcher-check --postgres-port 55532 --neo4j-http-port 57574 --neo4j-bolt-port 57787 --docker-socket /var/run/docker.sock --runtime-file <private-runtime>`: Linux에서 최초 자격증명 생성·컨테이너 생성·양쪽 DB 인증 연결 PASS. 사용자 원본을 넣지 않았고 시험 자원만 정리했다.
- Python 구문·`git diff --check` PASS. 기존 서비스 124/브라우저 27 증거는 `03c640c` 범위로 보존하며 전체 검사를 이번 실행 결과로 다시 집계하지 않는다. Mac 실기는 미실행이다.
