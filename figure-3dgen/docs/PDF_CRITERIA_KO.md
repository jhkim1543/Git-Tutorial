# DPAI 확장판 교육 PDF — Editable / Moldable 구조와 검증 기준

원문: Dream Plastic 「AX DESIGN PLAN & METHOD REV.1」(DPAI 확장판 교육, 2025-12-30), 113쪽. 페이지는 PDF 1부터. 이 세션에서 113쪽 전체 텍스트를 추출해 읽었다(그림만 있는 쪽 7·13·22·26·35·39·40·45·67–69는 텍스트 없음). 기계용 계약은 `contracts/criteria.json`이며 코드가 그 파일을 읽는다.

## 1. 두 3D 상태의 정의 (p.18, 52–54, 59, 64)

| | Editable 3D | Moldable 3D |
|---|---|---|
| 정의 (p.18) | 디자인 형상을 바꾸기 쉽게 최적화된 3D 드로잉 데이터. 파트가 **디자인 논리**로 분리. 다시 수정·응용될 것을 전제 | 실제 성형·제작이 가능하도록 최적화된 설계 데이터. 수정 자유도보다 **제조 가능성** 우선. "이대로 만들어도 되는 구조" |
| 나누는 방식 | **분리(object segmentation)**: 편집 효율을 위한 데이터상 나눔. 다시 합치거나 더 나눌 수 있음 | **분할(part separation)**: 취출·탈형·언더컷·파팅라인을 고려한 물리적 나눔. "이렇게 안 나누면 사출 불가" |
| 도구 (p.14, 96–100) | ZBrush (SubTool/Polygroup, 하이폴리 조형) | Freeform (분할·중공·배출구·두께/부피 시뮬레이션, STL 출력) |
| 관계 (p.52, 64) | Editable ⊃ Moldable: Editable이 되면 사람이 단계마다 개입해 Moldable을 완성. **설계 책임자의 판단**이 적용돼야 Moldable 완성 | |

→ 데모 반영: Editable을 먼저 만들고 승인 해시를 고정 → Moldable은 그 스냅샷에서만 파생(`derived_from_editable`). 분리(split 단계)와 분할(joints/moldable 단계)을 다른 단계로 둔다.

## 2. Editable 기준 (p.57–63, 80–86, 108–109)

| 기준 | PDF | 데모 게이트 |
|---|---|---|
| 편집 부위별 Polygroup / SubTool 정리, Blender에서는 Select Linked 단위 | p.57, 58, 83 | `single_shell_per_part`, `semantic_split_approved` |
| 파츠 구분 합리성 (조형 개념적·Moldable 지향적 분리 계획) | p.109 A-2 | 분할 판정 모델 + Astra + 사람 승인 |
| 폴리곤 무결성: 뒤엉킨 삼각면·무질서 토폴로지·통짜 메쉬 금지, 뒤틀림·찢어짐 정리 | p.86, 109 A-6 | `all_parts_closed`, `self_intersection_screen` |
| 토폴로지 안정화 (Subdivision·Slicing에 맞는 면 배열) | p.61, 109 A-5 | `quad_dominant_topology` (쿼드 ≥ 90%, QuadriFlow) |
| Polycount: 평균 7–12M 사각면, 최대 30M (예시·예산) / p.109 "1M(1천만)" 표기 모호 | p.84, 109 | `polycount_budget` 보고만 (면 수 부풀리기 금지) |
| 부분 고해상도(PAD·눈썹) | p.85 | 향후: 부위별 해상도 배분 |
| Export 스케일·Subdivision 초기화, inch↔mm | p.109 A-1 | `scale_and_axis` (mm, Y-up/Z-up 변환, 높이 오차 ≤3%) |
| Basemesh·비례·라이선스 고증 (사진/컨셉 대비 형상) | p.62, 63, 108 | `photo_silhouette` (승인 사진 다시점 IoU ≥ 0.85) |
| 머리/부위 겹침·관통 | p.108 A-2 | Moldable 단계 겹침 해소 + 간섭 0 |
| ZBrush에서 바로 재편집 가능 | p.12, 96–99 | `zbrush_sculpt_roundtrip` = NOT_VERIFIED (사람) |

**주의 (p.97–99)**: 피규어 원형은 고해상도·Non-manifold인 경우가 많고 "Moldable은 Manifold & Uniform 검증이 아니다". 그래서 폐합은 **이 데모 사용자의 출하 조건**(모든 파트 닫힘)이며, 폐합만으로 Editable 품질을 증명하지 않는다 — 실루엣·분할 승인·토폴로지 게이트를 함께 본다.

## 3. Moldable 기준 — 3 핵심 이슈 + 5 실패 필터 (p.20–51, 76–79, 110)

| 필터 / 이슈 | PDF | 데모 게이트 / 검사 |
|---|---|---|
| ⓐ 언더컷 (주입·탈형 미고려 구멍·갈고리·계곡) — #0 최우선 과제 | p.20–25, 46, 79, 110 | `undercut`: 후보 방향(축 + PCA) 중 양방향 가시성 최적 방향의 언더컷 면적비 ≤ 소재 한도 |
| 구배(탈출 경사각) | p.46 | `draft`: 당김 방향과 평행한 면 비율 보고 |
| ⓑ DECO 최적화 (PAD, Masking 분할, 눈썹 PAD 간섭 → 앞머리 분리) | p.27–32 | 병합 제안: 작은 내장 디테일(눈)은 PAD 대상 → 병합 |
| ⓒ 자립 (무게중심·하중 분산, 베이스) | p.33–36 | `self_standing`: 부피 가중 무게중심이 발바닥 지지 다각형 안 |
| ⓓ 샤프포인트: 주요 최소 두께 ~1.5mm, 안정 2mm, 기둥 평균 2.3mm, 극단 첨단 1.02mm 국부 허용, 말단은 둥글게 | p.37–38 | `min_wall_thickness`(p05 ≥ 1.5), `sharp_points`(1.02mm 미만 **군집** 없음) |
| ⓔ 파손·두께 균일화 (두께 급변 → 싱크·크랙) | p.41 | `thickness_uniformity` 보고 (p95/p05) |
| ABS 구조 두께 5mm 초과 시 추가 분할 | p.42 | `abs_max_thickness` (ABS 사출만) |
| 최소 두께·접착 면적 | p.43 | 소켓 벽 봉투 검사 |
| 블리스터 핏 7–10mm, 얼굴-손 6mm | p.44, 47 | 향후 (포장 형상 필요) |
| Legal line 7–14mm (보통 10–12mm) 발바닥 평면 | p.48 | `legal_line_area` |
| Small part test | p.49 | `small_parts` (31.7mm 실린더 — 16 CFR 1501, PDF 밖 외부 기준) |
| 넥 파트 위치 → 턱 들림 | p.50 | 향후: 넥 플러그 위치 규칙 |
| 조립 구조 불량(헐거움·틈새) | p.21 | `male_female_pairs`, `assembled_interference`, `insertion_path` |
| 공정: 인젝션(엄격), ROTO(PVC, 뜯어내듯 탈형 → 언더컷 상대적 여유, 107% 왁스 목업 예시) | p.19, 77, 78 | 소재 선택 `PVC_ROTO / PVC_INJECTION / ABS_INJECTION` |
| 실물 출력·카피캐스트·금형 승인 | p.59, 64–66, 100–106 | `process_and_physical_approval` = NOT_VERIFIED (사람) |

## 4. 역할 분리와 실패 복구 (p.65–66, 103–104)

PDF는 AI가 "분할 승인·설계 승인·견적 승인"을 판단하지 않고, FAIL 시 사람이 재편집·분할 수정·설계 수정을 한다고 정한다. 데모는 이를 그대로 따른다: 모든 단계가 `review`에서 멈추고 사람 승인 후 다음 단계, 실패 파트·접점은 위치와 사유를 남기고 차단한다.

## 5. 검증 루프 (코드)

```
Split:     근거 → SplitJudge + Astra → 사람 결정 → Blender 실행 → 결정이 학습 라벨로 → 판정 모델 재적합
Editable:  Blender 재구성 → 실루엣 비교 → 초과분 카빙 → Blender 재구성 → 무결성(닫힘·셸·자기교차) → 채택 / 직전본 유지
Moldable:  조인트 생성(오프셋 6 × 반경 3 재시도) → 전 조인트 재검증 → 간섭 → 서브어셈블리 삽입 → PDF 검사 → Blender 왕복
```

## 6. 수치 사용 원칙

`contracts/criteria.json`의 `numbers_with_context`에 원문 맥락을 붙였다. 언더컷 비율 한도(ROTO 0.12, 사출 0.03)와 조인트 기본 치수(반경 간극 0.15, 끝 간극 0.4, 벽 1.2)는 **개발자 가정**이며 PDF 수치가 아니다 — S3 라이브러리 또는 금형 담당자 값으로 교체해야 한다.
