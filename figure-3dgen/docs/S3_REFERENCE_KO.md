# S3 정답 CAD 50개 — 위치·절차·현재 상태

## 위치

| 구분 | 경로 | 상태 |
|---|---|---|
| 주 데이터셋 | `s3://dataset-dreamplastic` (us-east-1) | 2026-09-28 사용자 PC 자격증명으로 목록 `AccessDenied`. IAM 읽기 권한 요청 필요 |
| 백업 (사용) | `s3://q10park-archive/dreamplastic-cly-20260801/raw/<SampleId>/3D/*.cly` | 사용자 PC에서 50개 다운로드·해시 확인 완료(인계 기록) |
| 목록 | `reference/s3_50_manifest.csv` | 50행, SampleId 모두 다름, 크기·SHA-256·삼각면 수·치수·FreeStyle 버전 |
| 분석표 | `reference/s3_50_analysis.csv` | 모든 행 `NOT_ANALYZED` (아래 이유) |
| 로컬 사본 | 사용자 PC `…\데모 개발\reference_data\dreamplastic_s3_50_20260928\` | git 제외 (약 2.49 GiB) |

표본은 파일 크기(메시 복잡도) 분위수로 뽑은 것이며 **제품 종류·암수 방식 라벨이 아니다**. 크기 층으로 카테고리를 추측하지 않는다.

## 이 세션에서 확인한 사실

- 이 클라우드 컨테이너의 AWS 자격증명은 두 버킷 모두 `InvalidAccessKeyId`로 거부됐다. 사용자 PC 경로(`C:\…`)도 이 컨테이너에서 읽을 수 없다. → 50개의 형상 분석은 **0/50**. 다운로드 수·무결성·분석 수를 섞어 보고하지 않는다.
- `.cly`는 FreeStyle Workspace(FWP) 독점 형식이다. 헤더에서는 버전·단위 문자열만 읽을 수 있고 **형상·암수는 읽을 수 없다** (`app/reference/cly.py`는 메타만, `format_verified=false`).

## 분석 절차 (사용자 PC / FreeForm이 있는 PC)

```bash
cd figure-3dgen/backend
# 0) (필요 시) 다운로드 — 본인 AWS 프로필 사용, 키를 출력하지 않음
python scripts/s3_reference.py download --dest "<reference_data 경로>"
# 1) 무결성 50/50
python scripts/s3_reference.py verify --dir "<reference_data 경로>"
# 2) 메타 문자열 (참고용)
python scripts/s3_reference.py scan --dir "<reference_data 경로>"
# 3) FreeForm에서 표본마다 "조립 상태 그대로" 파트별 STL/PLY export:
#    <exports>/<SampleId>/<part>.stl   (단위 mm; inch면 --unit-scale 25.4)
python scripts/s3_reference.py analyze --exports "<exports>"
# 4) 조인트 라이브러리 (10개 이상 표본에서 pin/socket 관찰 시 READY)
python scripts/s3_reference.py library
```

`analyze`가 계측하는 것 (전부 `observed`):

| 항목 | 방법 |
|---|---|
| 수/암 | 파트 A 표면 중 파트 B에 가까운 점에서 26방향 광선 → B에 둘러싸인 비율(포위도) ≥ 0.6 + 볼록도 > 0.3 (소켓 벽 오검출 제거) → A=수, B=암 |
| 접점 수 | 포위 점 군집 = 조인트 1개 (같은 파트 쌍도 여러 개 가능) |
| 삽입(철수)축 | B에 막히지 않은 광선 방향의 합 |
| 형식 | 단면 원 적합 잔차/반경 < 0.06 → `round_pin`, 아니면 `keyed_pin`, 지름 ≥12 & 길이 < 지름 → `neck_plug` |
| 간극 | 수 측면 점의 B까지 거리 중앙값 |
| 소켓 벽 | B 소켓 벽에서 재료 안쪽 광선 거리 p05 |
| 면 접촉 | 포위도 < 0.4, 간극 < 0.2의 넓은 영역 → `face_contact`(접착/맞댐) |

합성 정답으로 검증: 원형 핀(지름 4.0, 간극 0.15, 벽 1.2) → `round_pin`, 지름 3.99, 간극 0.150, 벽 1.20; D-키 → `keyed_pin`. (`tests/test_reference.py`)

결과는 `reference/analysis/<SampleId>.json`, `s3_50_analysis.csv`(AnalysisStatus `OBSERVED_AUTO` / `OBSERVED_NO_PIN_FOUND`), `contracts/joint_library.json`에 기록된다. 라이브러리가 `READY`가 되면 Moldable 플래너가 접점 지름으로 가장 가까운 관찰 사례를 찾아 형식·간극·벽을 가져오고(`parameter_source = s3_library:[…]`), 게이트 `s3_grounded_joint_parameters`가 PASS가 된다.

## 사람 검토

자동 계측 후 표본마다 3–5개 접점을 FreeForm/뷰어로 눈으로 확인해 `ReviewerNotes`에 적는다. 회전·좌우 대칭 복제는 다른 종류로 세지 않는다. S3 CAD 이미지·형상은 외부 API(Astra)로 보내지 않는다.
