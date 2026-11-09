# MK-S2 원장 사건 목록 · 2026-11-09~10

DEC-H35의 읽기 화면 변경이다. 공유 MK-S2 운영 대시보드 하단 원장 사건 카드를 시간/원장 버전/사건 설명의3열, 최대280px 스크롤로 맞췄다. 실제 서버 사건만 사용하고 예시·미래 사건을 생성하지 않는다. 원형의 고정 fixture 사건 내용과 개수를 복제하지 않는다.

## 구현과 계약

- `service/ledger.py`: snapshot.events와 `/api/mks2/events`의 기존4필드(event ID, ledger version, type, recorded time)를 유지하고 안전한 한글 summary, document kind, 정확 문서 reference, origin을 추가했다. 회사·사건 버전·문서 FK 참조·엄격한 typed metadata와 content hash를 검사한다. BOM의 실제 원본 출처도 확인한다. 불명확하면 근거 확인 필요와 null reference를 반환한다.
- 요약은 고정 템플릿과 opaque 문서 ID 일부/버전으로 만든다. payload 전체·원문·메모·사용자 식별정보를 응답에 추가하지 않는다. 초기 합성 이관, 실제 BOM 원문 등록, 내부 개발 업무 반영을 구분한다. 공급확인은 내부 모의 기록이며 외부 공급사 확약으로 표시하지 않는다.
- `web/mks2/dashboard.js`, `dashboard.css`: 기록 시각을 Asia/Seoul HH:mm으로 표시하고 전체 원본 시각은 접근 가능한 설명과 접힌 상세에 유지한다. 같은 행에서 문서 종류·ID·정확 버전/hash를 펼쳐 볼 수 있다. 초안·검토·승인을 확정 원장 사건으로 추가하지 않는다.
- 서버 목록의 실제 건수와 전체 건수를 표시하고 최근100건 제한을 숨기지 않는다. 조회 버전보다 미래 사건은 표시하지 않는다. 첫 조회·다른 회사/신원·같은 버전 조회는 신규 강조가 없으며, 동일 신원에서 원장 버전이 증가한 경우에만 해당 사건을 첫 렌더링에서 잠시 강조한다. 로컬 필터·수량 기준 전환은 강조를 재시작하지 않는다.
- UI 선택/상세 펼침은 추가 API·모델·쓰기 없이 동작한다. 기존 새 버전 알림과 명시 최신 원장 조회를 유지한다. 이번 변경은 전체 실시간 사건 전달·재연결 인수가 아니며 과거 답변을 다시 작성하거나 매 사건마다 LLM을 호출하지 않는다.

요구 `UX-H08 / SD-027,033 / PNL-19 / CM04,05 / OBS17-05` → T03/T06/T10의 표시·근거 slice → 위 구현 및 `tests/test_ledger_event_views.py`, `tests/test_ledger_events_ui.mjs` → 아래 실행 증거로 연결한다. 부모 Task와 전체 서비스 인수는 IN_PROGRESS다.

## 실행 증거

환경: Mac arm64, Python venv, 별도 일회용 PostgreSQL, Node22, 캐시된 Playwright CLI/Chrome, 기존 Mac 앱8870. 대상은 `7a07fbe` 위 작업 트리이며 최종 파일 hash는 아래 기록한다.

| 검증 ID | 실행 | 결과 |
|---|---|---|
| LEDGER-FEED-SERVER | `.venv/bin/python <private-isolated-suite> ledger-event-views-final 'test_ledger*.py'` | 16 PASS. 모든 지원 사건 종류·엄격한 참조·회사/버전 경계·불명확 정보·원문 노출 방지, 실제 일회용DB의 읽기 불변 |
| LEDGER-FEED-API | `.venv/bin/python <private-isolated-suite> ledger-event-api 'test_service_api.py'` | 22 PASS. 기존 HTTP 조회·권한 계약 회귀 |
| LEDGER-FEED-NODE | `node --test tests/*.mjs` | 73 PASS. 신규 원장 사건13건과 기존 수량/BOM 등 회귀 |
| LEDGER-FEED-BROWSER | `playwright-cli -s=ledger-reference run-code --filename <private-read-check>` | 20 PASS. 실제4사건/원장v4,3열·한국시각/원본시간·정확문서근거·원문비노출·snapshot/poll일치·첫조회강조0·1536/390px. 업무쓰기차단 및 시도0 |
| LEDGER-FEED-PRESERVE | 독립 전후 보존 대사 | VERIFIED. 인증 세션·감사 각1행 증가를 분리하고 기존 인증49행/비인증29테이블 count/hash 불변, 원본·설정·온톨로지·그래프·모델 예산 동일 |

서버 로그 SHA-256: `0c7d66e1c3b3b523caa81ae2cf3ae77f89b662beac0c401dd9a3c6862c3a7759`. HTTP 로그 SHA-256: `f89ad82534c81159cb3971d4212d3c22c72d2a0c95b98fcdd756f57dc55c15f9`. 실제 브라우저·원문·trace·DB·runtime 산출물은 Git 밖에 보관한다.

## 보존

진행 중 Agent/모델 요청과 대기 투영/스냅샷이 없음을 확인한 후 기존 설정으로 앱 프로세스만 재시작했다. 직전·직후31테이블 count/hash, runtime·키·Runner·모델 설정이 동일하다. 원장/투영v4·BOM26문서·모델live12/확정비용0.00385달러/미확정예약0.016414달러를 유지했고 `/health`200이다. 재시작 증거 SHA-256: `d09ca4feb352d371bdea6392514fc6a2e1d9b0cc0f0a195268e9c91c7502b963`.

DB migration·초기화·새 초기 이관·업무 사건 생성·모델 호출은0이다. 전체 온톨로지·그래프·기존 보호 화면·사용자 파일을 보존하며 원래 DB의 쓰기 시연은 수행하지 않았다. 그래프2.5D/3D는 논의 상태이고 이 변경에 포함하지 않는다. 공개 PR 제출 범위이며 main 병합·배포·외부 공급사 송신은 수행하지 않는다.

최종 대사: 이전 BOM 완료 시점과 이번 앱 재시작 직후에 대비해 인증 세션·감사만49→50이며 SESSION_SELECTED1건/세션취소0이다. 기존 인증49행과 나머지29개 테이블 count/hash가 동일하다. BOM26문서/4원본, 원장/투영v4, Neo4j482노드/704관계, 원본155개·보존화면7개·runtime/키/Runner/모델 설정·전체 온톨로지를 유지했다. 모델 추가0, `/health`200. 분류 대사 SHA-256: `700788d2eb9156b3e75905ffe8d165c226c30e25c4e864a3ecabc2745e79baf6`.

최종 코드 SHA-256:

| 대상 | SHA-256 |
|---|---|
| `service/ledger.py` | `2dd29762bcba4ae7e5764ecaf80d472e7d2a862911c24144d570c1dcd1d9f2f4` |
| `web/mks2/dashboard.js` | `5704ab8d8315a62d838f10704c2f20d9d84d3f12a6850946c479a1a13905e2e4` |
| `web/mks2/dashboard.css` | `66937c169df2c88ecf2cefa75b49090fa18ad8ac82333ecd3991513bfc2ae77d` |
| `tests/test_ledger_event_views.py` | `1b33c5f6098ef7502bed8cfcd51e32304a130fee2c27c5e35e22e1266bba701b` |
| `tests/test_ledger_events_ui.mjs` | `eb4d8eb83529c435cbf7da833ad297ef00b1a93a9534ce724d5a60395fddb013` |

최종 비공개 실행 결과 SHA-256: 브라우저 `6ab6854ead6e6afe18b5807d815ce6d5f59f8ffafa17a394a4b6bd2f89d0110b`, Node `22dab0133c1c5b8e5d121c4a1180edae1c11c4445bcaa8c55485d852d21ee0cb`.
