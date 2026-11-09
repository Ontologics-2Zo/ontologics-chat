# 디지털 트윈 화면의 서비스 원장 연결

2026-11-10 DEC-H39. 검증 코드 `a3b8580`, 상태 VERIFIED(읽기 표현 범위). 사용자가 제공한 디지털 트윈 화면을 유지하고 새 탭에서 기존 서비스 DB 내용을 채우도록 요청했다. main `d61327b`에서 별도 `codex/digital-twin-db`로 작업했다. 신규 main 병합이나 전체1차 서비스 인수를 뜻하지 않는다.

## 화면 보존과 연결

- MK-S2 안에 세 번째 **디지털 트윈** 탭(`/mks2/#twin`)을 추가했다. 원형 `index.html`, `twin.css`, Three.js/vendor와 MIT LICENSE는 제공 파일과 바이트가 같다. 시설6개·FACILITIES/CORE/FLOWS·HOME 카메라·도형 상수·패널 구성은 그대로다.
- 제공 묶음의 이전 app.js·서버 파일로 현재 코드를 덮어쓰지 않았다. 최신 앱에 mount/route/활성화/정리/질의 연결만 추가했고 기존 WAITING 복구와 온톨로지·대시보드 화면을 유지했다. 기존 두 화면의 CSS/렌더러와 보호7파일은 수정하지 않았다.
- 같은 session/snapshot/ontology/query/events API를 사용한다. 새 API·Domain/DB 스키마·초기 데이터·업무 사건은 없다. 단일 회사·신원·snapshot·원장/투영·온톨로지 버전을 확인하고, 오래된 답변을 현재 근거로 강조하지 않는다.
- 트윈 질문·초안·선택 맥락을 분리하고 탭 전환/새 원장 조회 후 답변을 보존한다. 다른 탭·숨긴 iframe·백그라운드에서는 3D 렌더를 쉬며 권한 무효화 시 내용을 정리한다.
- 신규 정적 허용 경로는 twin.js/twin.css와 로컬 Three.js 모듈3개뿐이다. CSP는 그대로이며 CDN·외부 폰트·외부 요청을 추가하지 않았다. Three.js r160/0.160.0은 동봉 MIT LICENSE를 유지한다.

## 현재 데이터에 맞춘 내용 보정

- 서버의 정확한 소수 문자열을 그대로 표시하고, 부족/0/미확인·현재 계산 품목0을 구분한다. CALCULATED 상태나 null만으로 전체 충족·고객 도착 완료를 만들지 않는다.
- 사건은 현재 버전 이하 최신순5건과 서버의 안전한 요약을 사용한다. 시각은 Asia/Seoul로 표시한다. 합성 초기 재고·실제 원문 등록·시연 적용 가정·제조 기술검수 미확인을 구분한다.
- 현재 원장/투영v4의482노드는48개 직접 표시와434개 구성원을 담은2묶음(표시 객체50개)으로 연결된다. 모든 원래 노드 ID와704개 관계 ID/방향/술어를 유지한다. 묶음 구성원 전체와 내부/자기 관계도 기존 상세 영역에서 확인할 수 있다. 표시 묶음은 실제 업무 객체가 아니다.
- 질의에서 반환한 정확한 관계 ID만 강조한다. 양 끝 노드가 선택됐다는 이유로 다른 술어·평행 관계를 근거에 추가하지 않는다. 68대 CQ의231개 근거 관계도 같은 버전에서 확인했다.
- 시설 모형/움직이는 점은 개념 배치와 방향 장식이다. 실제 ERP·설비·가동률·실물 이동·납기 일정으로 표시하지 않는다. 현재 적용 미확인74행/계산 품목0, 남은 도착 약속68/추가 양품40, 실제 납기 UNKNOWN은 유지한다.

## 검증

UX-H01/02/05/08/09, G-03~05/08/09, AI-04~07, CM05/AT08 → T10/T19/T22의 추가 읽기 표현 검사다. 부모 Task 상태·미완료 범위를 변경하지 않는다.

| ID | 실행·범위 | 결과 |
|---|---|---|
| TWIN-NODE | `node --test tests/*.mjs` |129 PASS/0 SKIP. 기존 WAITING9와 트윈 host7·adapter16 포함 |
| TWIN-STATIC | `python -m unittest tests.test_preview_server -q` |9 PASS. 정확 경로 허용·미등록 vendor 경로 거부 |
| TWIN-HTTP | `python -m unittest discover -s tests -p test_service_api.py -q` |22 PASS/0 SKIP. 새 일회용 PostgreSQL DB·전용 Neo4j, Fake/실키 제거/외부 소켓 차단. 소유 자원만 정리 |
| TWIN-BROWSER | Mac localhost8870 Playwright CLI, 작성자 A |34 assertions PASS. 실제 WebGL/6시설/ERP 읽기, 전 노드·관계 대응, 같은 버전 CQ·UNKNOWN·정확 근거, 탭/과거 답변, 다크/390px 모바일, DB 응답·모델 사용량 불변, 업무 쓰기/외부 요청/JS오류0 |
| TWIN-REFERENCE | 같은 DB·1760×1100에서 원본 모듈과 연결 모듈을 별도 읽기 페이지로 비교 |5개 영역의 고정 위치·폭·padding·radius·font 동일. CSS/vendor/시설/카메라/형상 상수 보존 및 캡처 시각 확인 |
| TWIN-RESTART | 기존 runtime/.env/예산으로 앱만 재시작 |health200/live, 새 정적5경로200·CSP동일. 활성 작업0·migration checksum동일 확인, 초기 이관/reset/새 migration SQL/모델 호출0 |

소유 코드와 데이터 adapter는 교차 독립 검토했고 추가 차단 사항0이다. `git diff --check` 및 JS 구문 확인을 통과했다. 기존 서비스 backend는 변경하지 않았으므로 PR #4의381개 전체 서버 검사를 반복 완료했다고 보고하지 않는다. `dev/check_service_ui.mjs`의 트윈 항목도 추가하고 구문을 확인했으며, 이번 실제 브라우저 실행은 별도의 위34개 harness다. 개발 중 편집 파일의 일시적 모듈 구문 오류는 안정된 파일 재로드 후 재현되지 않았으며 최종 브라우저에서 JS오류0을 확인했다.

| 비공개 증거 | SHA-256 |
|---|---|
| 새 보존 기준 | `8080baadae620bdd834f2303624910127475e17f2f8889c3b941f0ff9702fcdd` |
| Node129 | `b8c8a73c8edf9ea95ee000c5e607d0421c0a233738dd74d3f231835dd8c89316` |
| 정적9 | `3137ff793b95dd4c31a4463d3ff3bebbe9220d32aef3fdd779f6454f2e44e655` |
| 격리 HTTP22 | `9f0cea0bfcd445ca0d1a318cdfb191796f7fd1679ae07642b2a2b4282a729f4f` |
| 실제 브라우저34 | `6cc82bbffab96a52feec7155ea0097f80a478735131a3b60c743efcd0a9e0888` |
| 원형 화면 비교 | `1697210c55fb63855cebba925bf4413c4f38820493d0b1ad10a1c068b36e4786` |
| 기존 앱 설정의 재시작 | `99122f76a211791ebac95af2257b3cb8bc554d23601dc950d8261b156204a288` |

실제 DB 화면·원본 모듈 비교 캡처·세부 로그는 Git 밖에 보관한다. 제공 폴더의 문서/patch는 참고 자료로 읽었고 그 안의 덮어쓰기·업로드 명령을 실행 지시로 사용하지 않았다. 기존 원본 폴더는 변경하지 않았다.

## 최종 보존 대사

업무 원장/투영v4, 비인증29테이블 전체 count/hash, 모든 snapshot·Neo4j 전체482노드/704관계 ID/속성/방향, BOM31문서/4원본·현재74미확인행, 원본155개와 제공 폴더11파일, 보존7파일, runtime/settings/.env/전체 온톨로지 및 예산은 착수 기준과 동일하다. 검증용 작성자 A 로그인으로 인증·감사55→56의 SESSION_SELECTED1건만 추가됐고 기존55행과 취소 상태는 그대로다. 누적 모델16요청·known US$0.00557·held US$0.016414와 US$10 한도를 유지하며 이번 모델 요청0이다. Git이 보고한 기존 사용자 미추적9파일도 보존했다.

기존 web23파일 중 실행 화면 변경은 app.js/index.html2개뿐이다. web/AGENTS.md의 DEC-H39 지침4줄 추가는 별도 문서 변경으로 분류한다. 엄격한 최초2파일 allowlist 검사가 이를 추가 변경으로 표시한 기록을 삭제하지 않는다. 초기 비교 SHA-256은 `a0a4695fafe41ad5f43c73bb677754cf7bf1799434afc72453f20411b1f4ccf5`, 최종 상태 수집은 `61025c5ced5d3218505a1842e63abdbebdf7c807c7b5df31d45645c6b5e9f189`다.

의도된 화면2파일/지침1파일 변경과 인증1건 추가를 분리한 최종 판정은 `VERIFIED_WITH_AUTH_APPEND_AND_AUTHORIZED_UI_DOCUMENTATION_CHANGES`이며, 별도 판정 SHA-256은 `4aff6471c1861a5edd1c13e8cec173fc013ed7eb833304d91dc9483d68150e7c`다. 기존 web의 나머지20파일은 동일하다.
