# 검증 기록 (2026-09-28, 개발 컨테이너)

환경: Linux, Python 3.11, Blender 4.2.0 (`bpy` 모듈, 헤드리스), Node 22 / Vite 6. `OPENAI_API_KEY` 없음, AWS 자격증명 거부, `figure.rebuilder.ai` egress 403, Docker 데몬 없음.

## 자동 테스트

`cd backend && python -m pytest -q` → **18 passed** (약 94초)

| 파일 | 내용 |
|---|---|
| `test_geometry.py` (9) | 감사, 경계 지그재그(깨끗 1.13 / 잡음 3.8–5.5), 깨끗한 재분할(→1.24), 판정 모델 사전·재학습, 비주얼 헐이 초과분만 제거·IoU 상승, 암수 생성·삽입 PASS/측면 이동 FAIL(음성시험), 공간 부족 시 BLOCKED(보스 과성장 금지), 얇은 판 두께·샤프 FAIL, 교차 구멍 언더컷 검출 |
| `test_reference.py` (4) | 정답 분석기: 수/암·원형/키·간극 0.15·벽 1.2 계측, 라이브러리 READY → 플래너가 라이브러리 값 사용, 매니페스트 검증(누락 검출), 동봉 매니페스트 50개 고유 |
| `test_blender_worker.py` (2) | 실제 Blender: 구멍 난 머리 → solid, 열린 시트 → 2.0mm shell, 둘 다 닫힌 쿼드·OBJ 왕복 PASS; 미지원 명령(`exec_python`) 거부 |
| `test_api_e2e.py` (3) | HTTP 전 과정: 열린 GLB 수용, Astra 키 없음 → 409/BLOCKED(가짜 결과 없음), 승인 전 단계 진입 409, 분할 적용 8→4 파트, Editable 게이트, Moldable 게이트, ZIP, 경로 탈출 404 |

## 합성 피규어 전 단계 (`scripts/run_synthetic.py`)

**합성 시험체**다(몸통 원기둥, 머리 구, 눈, 망토 판). AI GLB는 일부러 망가뜨림: 8개 열린 파트, 머리 2조각·망토 3조각·눈 2조각(잡음 경계), 머리 폭 12% 부풀림. 승인 다시점은 참값 렌더.

| 단계 | 결과 | 시간 |
|---|---|---|
| intake | 8 파트, 닫힘 0, 열린 에지 1,470 (수용) | 0.1s |
| split | 경계 4개 → 판정 모델 병합 4 (p 0.86–0.98), Astra `BLOCKED`(키 없음) → Blender join → 4 의미 파트 | 5.2s |
| editable | 4/4 닫힘(body·head·eye solid, cape 2.0mm shell), 전부 QuadriFlow 쿼드, 자기교차 0, OBJ 왕복 PASS. 사진 실루엣 IoU 정면 0.93 → 0.95 → 0.97, 2회차 보정은 자기교차로 **거부되고 직전본 유지** | 55s |
| joints | 눈 → 머리 병합(PAD 대상), 접점 2개 → 망토(수, D-키) → 몸통(암), 몸통(수, 넥 플러그) → 머리(암). 개념도 `BLOCKED`(키 없음) | 3.8s |
| moldable | 차단 게이트 전부 PASS (파생, 폐합, 접점 커버리지, 암수 쌍, 간섭 0, 서브어셈블리 삽입, 최소벽, 언더컷, 샤프포인트, OBJ 왕복). 보고 게이트: 두께 균일·구배·S3 근거·공정 승인 `NOT_VERIFIED`, 개념도 `BLOCKED` | 16s |

개발 중 이 루프가 잡아낸 실제 결함(수정됨): 머리 조각 사이 미세 틈 때문에 속 빈 쉘로 재구성되던 문제, 카빙 후 스무딩 접힘(자기교차), 가져온 키 생성기의 무제한 보스 성장, 조립된 다른 부품과의 가짜 삽입 충돌, 접촉면 간극 0으로 인한 미세 간섭, 부피보존 스무딩 106초 병목.

## UI

uvicorn이 `/3dgen/` UI와 `/3dgen/api/*`를 서빙. 헤드리스 Chromium(1440px)에서 6단계 화면·3D 뷰어·게이트 표·실루엣 diff 확인, 콘솔 오류는 favicon 404뿐, 390px 모바일 가로 넘침 0px. `npm run build` 통과(tsc strict).

## 실행하지 못한 것 (BLOCKED / NOT_VERIFIED)

| 항목 | 이유 |
|---|---|
| Astra 실호출 3종 | 이 환경에 `OPENAI_API_KEY` 없음 → 서버의 기존 키로 실행 필요 |
| S3 50개 형상 분석 | AWS 자격증명 거부, .cly는 FreeForm export 필요 → 0/50 |
| 실제 GLB 5종 | 파일이 사용자 PC에 있고 **원본 사진이 없음** |
| figure.rebuilder.ai 배포 | egress 403, 서버 권한 없음 |
| Docker 이미지 빌드 | Docker 데몬 없음 |
| ZBrush 조형 왕복, 금형·공정 승인 | 사람 증거 필요 |
