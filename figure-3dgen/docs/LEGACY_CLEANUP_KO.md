# 레거시 정리 기록

인계 ZIP(`figure-remodel-claude-20260928.zip`)의 `current-code/`와 그 안의 2026-09-27 handoff ZIP을 모두 읽은 뒤, 새 `figure-3dgen/`에는 **검증된 알고리즘만 다시 작성해 이식**하고 나머지는 가져오지 않았다. 원래 회사 저장소(`RebuilderAI/vringon-beta-module`)의 파일은 이 세션에서 접근하지 않았으며 삭제하지 않았다(그 저장소 정리는 소유자 승인 후 별도로).

## 가져오지 않은 것 (레거시로 판단)

| 레거시 | 이유 |
|---|---|
| `zbrush-integration/*` (queue_core, tracked_step, host_probe, lifecycle_compat, setup/start.ps1, MCP bridge) | 사용자 우선순위가 Blender. 특정 PC 경로·세션에 묶임. 데모 경로와 무관 |
| `figure-remodel/` Flask·정적 초안 (server.py, app.js, index.html, pipeline.py) | 미검증 초안이라고 원작성자가 명시. React/FastAPI로 대체 |
| `semantic-remodel/*` (037 전용 병합, 012 전용 인서트, review_images.py 등) | 특정 샘플 하드코딩. 일반 분할 판정 모델(`SplitJudge` + Astra)로 대체 |
| `experiments/gen.py`, `experiments/index.ts` 플레이그라운드 결합, `/gen` 경로 | 목표 경로는 `/3dgen`. 독립 앱으로 분리 |
| `reference_key.json` (2291a 한 표본의 키 단면) | 단일 표본으로 전 제품을 대표할 수 없음(인계 계획서 지적). S3 라이브러리로 대체 |
| `figure_gen/pipeline.py`의 경계 cap·harmonic 공유 패치·QuadriFlow만 하는 Editable | GLB 표면을 닫기만 하는 방식 → "GLB를 정답으로 쓰는" 실수의 원인. 복셀 기반 새 솔리드 + 사진 실루엣 루프로 대체 |
| 모든 파트를 Blender 한 프로세스에 넣는 방식, 20분 전체 제한 | 파트별 프로세스로 대체 |
| 스크립트 `scripts/figure_*.py` 일회성 probe | 재현 가능한 `run_synthetic.py`, `s3_reference.py`와 pytest로 대체 |

## 다시 작성해 이식한 것 (재사용 가치 확인)

| 원 위치 | 새 위치 | 변경 |
|---|---|---|
| `geometry.audit`, `boundary_loops` | `app/geometry/audit.py` | 셸 수·면적 추가 |
| `keyed.construct_key` (마스터 단면 핀 + 간극 커터 + 보스 봉투) | `app/geometry/joints.py: construct` | **버그 수정**: 벽 검사가 항상 통과하던 문제(보스가 무제한 성장) → 보스 ≤ 봉투 10%, 핀 노출 금지 |
| `keyed.disjoint_partition` | `joints.resolve_overlaps` | 0.1mm 접촉 간극, 미세 조각 허용 한도 |
| `insertion.check_translation` | `app/geometry/insertion.py: sweep` | 서브어셈블리 단위로 이동 (조립된 다른 부품과의 가짜 충돌 제거) |
| `blender_worker.metrics`, BVH 자기교차 스크린 | `app/blender/worker.py` | JSON 명령 해석기(임의 코드 실행 없음) |
| `native.execute` 감독 | `app/blender/runner.py` | 동일 원칙(시간 예산, 결과 없으면 실패) |
| `quality.py`의 "빈 목록은 PASS 아님", 해시 결합 | `jobs.py`, 각 단계 `gates()` | 게이트를 `contracts/criteria.json`에서 읽음 |
| `fig3dgen/astra.py` Responses API 호출 형태 | `app/astra.py` | 이미지 생성·JSON 스키마·캐시·provenance |
| PDF_ANALYSIS.md, ASTRA_GUIDE.md의 판정 원칙 | `docs/PDF_CRITERIA_KO.md`, `CLAUDE.md` | 113쪽 재확인 후 재정리 |

## 과거 결과에 대한 사실 (재현 없이 인용하지 말 것)

- 2026-09-27 기록: 입력 5종 111 파트 전부 열림, 012 재생성 폐합 9/16, 4종 암수 통합 0 → 당시 파이프라인은 사용자 요구 미달이었다.
- 이번 새 파이프라인은 **합성 피규어**에서만 전 단계 실행됐다. 실제 5종은 원본 사진이 없어 아직 실행하지 않았다.
