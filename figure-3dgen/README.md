# Figure 3DGen — 사진 기준 Editable → Moldable

원본 **사진**과 AI 생성 **GLB**를 받아, 사진에서 만든 승인 다시점을 외형 정답으로 Blender에서 **모든 파트가 닫힌 Editable**을 새로 만들고, 그 Editable에 **암수 구조**를 넣어 DPAI 교육 PDF의 몰더블 조건으로 검증한 **Moldable**을 만드는 데모. 목표 주소 `https://figure.rebuilder.ai/3dgen`.

> Claude Code로 작업한다면 **[CLAUDE.md](CLAUDE.md)** 부터 읽는다 (GLB를 정답으로 쓰지 말 것, S3·Astra·도메인 위치).

## 흐름

| # | 단계 | 무엇을 하나 | 사람 결정 |
|---|---|---|---|
| 1 | 입력 | 사진(필수) + GLB(참고). 열린 GLB 허용, 위치 기준 용접 후 진단 | — |
| 2 | 사진 다시점 | Astra가 사진으로 정면·우·후·좌 직교 뷰 생성(또는 업로드) → 실루엣 | 뷰 승인 |
| 3 | 분할 검토 | 경계 근거 + `SplitJudge` 모델 + Astra → 병합 / 깨끗이 재분할 / 유지 → Blender 명령 | 경계별 결정·라벨 |
| 4 | Editable | Blender가 파트마다 새 닫힌 쿼드 솔리드 생성 → 사진 실루엣 비교 → 초과분 보정 루프 | Editable 승인(해시 고정) |
| 5 | 암수 계획 | 병합(눈 등) → 생산 분할 → 접점 그래프 → 접점당 암수 1쌍 + Astra 개념도 | 계획 승인 |
| 6 | Moldable | Editable 파생: 암수 Boolean, 간섭·삽입 경로, 두께·언더컷·샤프포인트·자립·표기면 | 공정 승인(사람) |

## 폴더

```
CLAUDE.md                  Claude Code 작업 가이드 (필독)
contracts/criteria.json    PDF 기반 게이트·수치 계약 (코드가 읽음)
contracts/joint_library.json  S3 정답 CAD에서 계측한 암수 라이브러리 (현재 EMPTY)
reference/                 S3 50개 매니페스트·분석표
backend/app/               FastAPI · pipeline/ · geometry/ · blender/ · reference/ · astra.py
backend/scripts/           run_synthetic.py (키 없이 전체 흐름) · s3_reference.py (정답 CAD 도구)
backend/tests/             pytest 18개 (Blender 실행·HTTP E2E 포함)
frontend/                  Vite + React + three.js, base /3dgen/
deploy/                    Dockerfile · docker-compose.yml · nginx-3dgen.conf
docs/                      개발 계획 · PDF 기준 · S3 · 레거시 정리 · 배포 · 검증 기록
```

## 실행

```bash
cd backend && pip install -r requirements.txt   # + Blender: FIG3D_BLENDER_PATH 또는 pip install bpy==4.2.0
export OPENAI_API_KEY=...                         # 서버의 기존 키 재사용 (없으면 Astra 단계는 BLOCKED)
uvicorn app.main:app --port 8030
cd ../frontend && npm ci && npm run dev           # http://127.0.0.1:5180/3dgen/
# 또는 npm run build 후 http://127.0.0.1:8030/3dgen/
python -m pytest -q                               # backend
```

## 현재 상태

- 코드: 전 단계 구현, 테스트 18/18, 합성 피규어에서 Editable·Moldable 차단 게이트 전부 PASS ([검증 기록](docs/VERIFICATION_KO.md)).
- 남은 것: S3 50개 실제 분석(FreeForm export 필요), Astra 실호출(서버 키), 실제 5종 + 원본 사진 실행, 도메인 배포 — [개발 계획](docs/DEVELOPMENT_PLAN_KO.md).
- `release_approved`는 항상 false: 공정·금형·실물 승인은 사람 단계.
