# 공유 MK-S2 보관 원본·시연 대상 표시 · 2026-11-09

DEC-H34의 제한된 화면/조회 구현이다. 공유 MK-S2의 760px 드로어, 파일 선택 영역과 보관 원본 버튼, 4개 요약 카드, 2열 검증 체크, 필터와 결과 표 구성을 서비스에 맞췄다. 기본 표를 선택 품목 중심으로 바꾸라는 최신 요청을 반영해, 원형의 전체 행 기본 노출 대신 접힌 전체 원문을 제공한다. 전체1차·제조 기술검수 완료는 아니다.

## 구현 경계

- `web/mks2/bom.js`, `dashboard.css`: 서버 목록과 기존 문서의 source ID/hash에서 보관 원본 바로 사용 버튼을 구성한다. 명시 클릭만 접수 요청을 만들고 실제 응답 대기 중 진행 바를 표시한다. 성공하면 추출·검증 카드와 시연 대상 행부터 보여주며 품목 연결은 같은 페이지에 유지한다.
- 원본 전체 행은 삭제하지 않고 접힌 상세에 보존한다. 시연 대상5행의 추출/연결 표, 전체 원문74행, 현재 연결 건수, 미확인 판단 건수를 구분한다. 승인 화면에도 전체 원문과 미확인 확인 항목을 유지한다. 저장은 필터된 목록이 아닌 전체 행을 보낸다.
- `service/bom_focus.py`, `bom.py`, `bom_contracts.py`, `main.py`: `POST /api/mks2/bom/review-focus` / `bom.review_focus`는 같은 회사의 `document.read`와 정확 target 버전/hash를 검사한다. 현재 연결 행을 우선하고, 없으면 같은 source hash/configuration에서 연결 이력이 있는 최근 POSTED 문서와 원문 전체를 대조한다. 반환값은 표시할 row ID와 근거 참조이며 판단/수량/승인을 복사하지 않는다. 더 최근 문서의 미확인 상태도 그대로 유지한다.
- 새 초안의 표시 대상5와 현재 연결0을 분리한다. 다른 원본·구성이나 불일치한 원문에서 임의로 대상5를 만들지 않는다. 비교·승인·반영, 전체 온톨로지·원장 계산 계약은 유지한다. 신규 migration과 그래프 레이아웃 변경은 없다.
- ODS·CSV 표 등록만 현재 경로다. PDF의 텍스트 줄 추출은 BOM 열 인식/OCR 완료가 아니며 TXT·새 파일 업로드는 미지원으로 표시한다. 실제 PDF/TXT 신규 지원을 검증한 기록이 아니다.

세부 규약은 [BOM 검토 규약](BOM_REVIEW_CONTRACT.md)을 따른다. 요구 `REG-01~04 / CM01 / SD-002~005 / PNL-02~03 / UX-H03~04` → T09/T12 → 위 구현과 `tests/test_bom_focus.py`, `test_bom_api.py`, `test_bom_ui.mjs` → 아래 증거로 연결한다. 부모 Task는 IN_PROGRESS다.

## 실행 증거

환경은 Mac arm64, Python venv, 기존 Docker PostgreSQL/Neo4j/제한 Runner, Node22, 캐시된 Playwright CLI/Chrome이다. 대상은 `9acc51b20df5e2c55d9963195a19d090ea713a9a` 위 이번 작업 트리이며 아래 최종 파일 hash로 식별한다.

| 검증 ID | 실행 | 결과 |
|---|---|---|
| BOM-FOCUS-SERVER | `.venv/bin/python <private-isolated-suite> bom-review-focus-v3 'test_bom*.py'` | 67 PASS. 같은 원본/회사/구성·정확 버전, 현재 판단 우선·이전 대상 표시만, 새 무매핑 문서 불변, 원문 불일치·중복/누락 거부, 10업무테이블 불변, 기존 BOM/Runner 회귀. 일회용 DB 사용 |
| BOM-FOCUS-NODE | `node --test tests/*.mjs` | 60 PASS. source ID/hash 선택,74행 중5행 표시와69행 보존, 오래된 응답 거부, 현재/임시 판단과 참조 분리, 정확 소수 비교, 기존 UI 계약 |
| BOM-FOCUS-PENDING | `playwright-cli -s=bom-reference run-code --filename <private-synthetic-check>` | 22 PASS. 실제 API 전부 차단. 원본 버튼·실제 대기 수명·오류/같은 키 재시도·성공/늦은 응답,5행 표시·74행 상세·명시 저장 payload74행과69행 미확인 보존 |
| BOM-FOCUS-BROWSER | `playwright-cli -s=bom-reference run-code --filename <private-read-check>` | 26 PASS. 실제 보관 원본 버튼·지원 형식 안내·4카드/6체크·기존5행/새초안5행 표시·현재연결0·접힌74행·명시펼침·독립승인·1536/390px. 업무 쓰기 차단 및 시도0 |
| BOM-FOCUS-PRESERVE | 독립 전후 보존 대사 | VERIFIED. 인증 세션·감사 각1행 증가를 분리하고 나머지29테이블 count/hash 동일. 원본·설정·원장/그래프·모델 예산 불변 |

서버 실행 로그 SHA-256: `ba3c891d82e0f4ae800d4ca2abbf37979308d768ba42f62f07973e1662fbe27d`. 첫 격리 실행은 테스트 fixture에서 반영 후 snapshot 전진이 없어 실패했고, 테스트의 정상 worker 전진 호출을 보완해 새 일회용 DB로 재실행했다. 최종 v3는 최신 표시용 참조 선택 규칙의 결과다. 원래 DB를 이 테스트에 사용하지 않았다.

## 보존

최신 baseline은 원장/투영v4·BOM 문서26개·그래프482노드/704관계다. 이전 기록의 v2/v3로 되돌리지 않았다. 현재 문서의 미검수/미확인과 기존 과거 반영 문서는 그대로 보존한다.

실행 중 Agent/모델 요청과 대기 업무가 없음을 확인하고 기존 설정으로 Mac 앱 프로세스만 재시작했다. 직전/직후31테이블 count/hash, runtime·키·Runner·모델 설정이 동일하고 `/health`200/live다. 모델 live12·확정비용0.00385달러·미확정예약0.016414달러를 유지했다. 재시작 증거 SHA-256: `1a752725dc50b9edbbadeaf0e174d4faaeb3e9ec8f18a34959b745e089896357`.

이번 작업의 실제 모델 호출·원래 업무 원장 쓰기·초기 이관·reset은0이다. 비공개 원문·HTML/fixture·캡처·DB·trace는 Git 밖에 보관한다. 공개 PR에는 코드·합성 검사·비식별 기록만 포함하며 main 병합·배포는 수행하지 않는다. 2.5D/3D 그래프는 의견 요청으로 취급했고 이 변경에 포함하지 않았다.

최종 코드 SHA-256:

| 대상 | SHA-256 |
|---|---|
| `service/bom_focus.py` | `3f50b49e672509fba5815626a487a25d19d4314b152d0f6188c188a6df0684e3` |
| `service/bom.py` | `3601e188712a808bfb96d8e53a27353b9a37c8e5411355f8e3e25e8983fcf48c` |
| `service/bom_contracts.py` | `35fda866c74bcc20622447e0f957ee9a987f1bf1697770e76d528ffdccda8b27` |
| `service/main.py` | `93da9e68d7324ae0884fb910785a582a5b95f298772cececbb039d7b8327f23c` |
| `web/mks2/bom.js` | `3276803a9ba6dcab74f739a7a0827452b35d3c26ad295f8c59e394958eb0fbd7` |
| `web/mks2/dashboard.css` | `3b6a5723f494cde0f9ff83b05c34271f54ea6e77958c7e2ee352afa7f780f115` |
| `tests/test_bom_focus.py` | `13b9e96dacfb2f76c8b45ffe1265f94533c71fcc8ceef018aa2bb8732d3292ef` |
| `tests/test_bom_api.py` | `9030590e9067941e9ef8b2bc9e62eed672b5d1113eb96de77ae4c244dfe3717e` |
| `tests/test_bom_ui.mjs` | `e8eb466e3c392be09ee9f2cbb3df98db277d952e5a08e8090f8537ec1d4b8487` |

비공개 실행 결과 SHA-256:

- 실제 읽기: `4d1e0d8d33b0ee7c03039eb1c935a0ef8517646f301f1dc7025dcbec28f5b52c`
- 합성 동작: `da983b8039a90590ef7f7035a16ba3dee2737255caf695d3cd32b611b8b3410b`
- Node: `9c24020b5f5caa845be97093be1ecc64e7637d017a7e807bbca88b5261348295`

최종 독립 대사는 최초 신규 baseline과 재시작 직후 baseline 양쪽을 기준으로 수행했다. 인증 세션/감사만 각각48→49이고 나머지29테이블은 count/hash가 동일하다. BOM26문서/4원본, 원장/투영v4, Neo4j482노드/704관계, 비공개 원본155개, 보존 화면7개, runtime·키·Runner·모델 설정, 전체 온톨로지, 모델 사용량/예산을 유지했다. `/health`200. 분류 대사 SHA-256: `91caf0fe09c2bbe24714504e363f83ba5f9b761f5f33c97a86d3972d229e61e6`.
