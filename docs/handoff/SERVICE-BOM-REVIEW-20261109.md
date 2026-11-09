# BOM 선택형 검토·원문 비교 · 2026-11-09

DEC-H33에 따른 T09/T12의 제한된 구현 기록이다. 공유 MK-S2의 결과 표 중심 동선을 유지하면서 완료 단계 표시, 선택형 검토, 서버의 읽기 전용 비교를 연결했다. [화면·비교 규약](BOM_REVIEW_CONTRACT.md)을 적용한다. 이전 DEC-H32의 실행 증거와 미구현 상태는 역사적 기록으로 보존하며, 자동 비교에 대한 최신 상태는 이 문서를 따른다.

## 구현

- `web/mks2/bom.js`, `dashboard.css`: 실제 문서 상태 기반 완료/검토 필요 색상과 현재 위치를 분리한다. 기본 연결 표에서 행을 선택해야 검토 패널이 열린다. 적용·품목·수량·단위를 선택하고, 예외 입력과 의견은 접힌 상세로 제공한다. 원문 전체와 미해결 행을 보존한다.
- `web/mks2/product.js`: 기존 조회 후보를 같은 패널의 선택지에 제공한다. 자동 선택이나 자동 저장은 하지 않는다.
- `service/bom_compare.py`, `bom.py`, `bom_contracts.py`, `main.py`: `POST /api/mks2/bom/compare` / `bom.compare`는 `document.read` 권한과 양쪽 정확 버전/hash를 확인한다. 두 문서를 같은 SELECT로 읽고 원문 대응·차이를 계산한다. 파싱·업무 쓰기·모델 호출은 없다. DB migration도 없다.
- 비교 기준은 명시 선택이다. 기준 없음·동일 원본·변경·모호한 대응을 구분하고, 제조 동등성과 실제 개정 계보를 확정하지 않는다. 이전 검토/승인은 자동 이월하지 않는다.

## 실행 증거

요구 `REG-01~04 / CM01 / SD-002~005 / PNL-02~03 / UX-H03~04` → T09/T12 → 위 구현 및 `tests/test_bom_compare.py`, `test_bom_api.py`, `test_bom_ui.mjs` → 아래 검증으로 연결한다. 부모 Task와 전체 1차 서비스는 IN_PROGRESS다.

환경: Mac arm64, 기존 PostgreSQL/Neo4j/제한 Runner, Python venv, Node 22, 캐시된 Playwright CLI/Chrome. 대상은 `c94b6be7b847d7cfe18952b574037af0e894e2f2` 위 이번 작업 트리다. 아래 파일 hash와 실행 증거로 대상을 식별한다.

| 검증 ID | 실행 | 결과 |
|---|---|---|
| BOM-REVIEW-SERVER | `.venv/bin/python <private-isolated-suite> bom-compare-v1 'test_bom*.py'` | 56 PASS / 0 SKIP. 합성 원문 비교, 동일/없는 기준, 중복·분할·병합·미확인, 권한·양쪽 stale/hash, 다른 회사 거부, 업무 테이블 불변·파서 호출0. 기존 BOM/Runner 회귀 포함. 별도 일회용 DB 사용 |
| BOM-REVIEW-NODE | `node --test tests/*.mjs` | 47 PASS / 0 SKIP. 상태 기반 단계, 선택 명시성, 근거 생성, 정확 소수, 오래된 비교 응답 무시, 오류·HTML escape, 기존 UI 계약 |
| BOM-REVIEW-BROWSER | `playwright-cli -s=bom-ax run-code --filename <private-read-check>` | 29 PASS. 실제 저장 문서 조회·전체 원문74행, 완료/확인 필요 색상, 기준 없음·다른 원본·동일 원본 비교, 선택4개·기본 텍스트 입력 숨김·소수 보존·자기승인 차단·1536/390px. 업무 쓰기 요청 차단 및 시도0 |
| BOM-REVIEW-PRESERVE | 독립 전후 DB·원본·runtime 대사 | VERIFIED. 업무·모델 등29테이블 count/hash 동일, 인증2테이블은 로그인1회 기록만 추가. 원본·설정·보존 화면·그래프 동일 |

실제 브라우저의 서로 다른 문서 비교는 기존에 저장된 두 원본을 명시 선택한 읽기 경로 확인이다. 같은 제품의 정식 L→L1 개정 관계를 확인한 증거가 아니다. 같은 원본의 다른 저장 문서는 IDENTICAL_SOURCE로 확인했고 기존 원본 경고를 유지했다. 신규 원본 접수·검수 저장·승인·반영은 수행하지 않았다.

서버 실행 로그 SHA-256: `c59a574b2805d7ce512c4b8ceb0072ccbda34d1a59aea8a0a81ecda416e08565`. 브라우저 스크립트·캡처·원문 행·runtime은 Git 밖에 보관한다. 화면 통합 당시 관찰된 별도 graph.js 음수 SVG viewBox 문제를 이번 BOM 검증으로 완료 처리하지 않는다.

## 보존과 범위

기존 runtime과 설정으로 Mac 앱 프로세스만 다시 실행했다. DB 초기화·새 초기 이관·키 교체·예산 초기화는 수행하지 않았다. `/health`는 정상이다. 이번 모델 호출은 0이며 기존 US$10 예산을 유지한다. 원문 전체의 기술검수, 실제 제조·단위·동일품 기준 확정, 전체 Agent 및 1차 인수는 남아 있다. 공개 변경에는 코드·합성 테스트·비식별 기록만 포함하며 PR 제출 이후 main 병합·배포는 수행하지 않는다.

독립 전후 대사: 업무·모델·Agent/checkpoint·스키마·계정 등29테이블의 count/hash는 동일하다. 인증 세션과 인증 감사만 사용자 A 선택1회로 각각1행 증가했으며 취소·업무 변경은 없다. BOM 문서12개/보관 원본4개, 비공개 원본155개와 manifest, runtime·키·Runner·모델 설정, 보존 화면7개, 온톨로지 버전을 유지했다. 원장/투영 v2, 실제 Neo4j176노드/252관계, 모델 live5·확정비용0.00128달러·미확정예약0.010932달러가 동일하다. 분류 대사 증거 SHA-256은 `f8329913311fb52e83ba5d01aff158751215bc77e0c99f0a426a92fc984ce661`다.

최종 코드 SHA-256:

| 대상 | SHA-256 |
|---|---|
| `service/bom_compare.py` | `eefe7d981673f17c7a1b07b80849973a3a02301d1a4e043cf6086615679392f7` |
| `service/bom_contracts.py` | `8cdcc2069154f917cdc474ea8e6ec312cfa7adc80c91eb8b1ba0ebe7c7d8f73d` |
| `service/bom.py` | `a68e8bf599e7e42980b8e1e033e811e29f1209619e32934761766ec4ca4cc07b` |
| `service/main.py` | `c9d5dda6e4dc65959fe11a9f90ffd962a99448c8959f97f99b6589efb4b088b8` |
| `web/mks2/bom.js` | `15941a6aed6d4f50465736ca8df25b32a384c942511ed18af8e0d760fdec76ee` |
| `web/mks2/dashboard.css` | `31a1bec27d58b8cb3f9042a6b36eedcdcdef2d8f8c98e2f1b392be669747f6ee` |
| `web/mks2/product.js` | `738cab59825598cb006721a6a879aab2092e9ad1e32522d3843c703f79b3bb45` |
| `tests/test_bom_compare.py` | `d00e2ed678bb41cfa67812978c98f41ca50a06f913a4fd26bb6df251d740d255` |
| `tests/test_bom_api.py` | `92948aa6f1dc0d019e5b30b9545a7f35fdb60b39d15bd0f68214800c86625903` |
| `tests/test_bom_ui.mjs` | `e7e5f972d62a6d9b5f1c9afae19a73554dd42047c2e05462402bd54614b8558d` |

최종 비공개 브라우저 결과 SHA-256: `8cfdf53244992acfe834af0f311c224b5f67fd88a879d6aeef9cc9924e5f1f15`. Node 결과 SHA-256: `407079269ab59db8c87bc0f5348ecad68f95a62281951be371ad2511cbbb79e5`.
