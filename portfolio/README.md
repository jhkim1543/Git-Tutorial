# 김나영 Portfolio 2026

첨부된 레퍼런스 PDF의 톤앤매너(오프화이트 캔버스 · 로열블루 액센트 · 네이비 세리프 디스플레이 ·
여백이 넉넉한 화이트 카드)를 따라 다시 구성한 포트폴리오 덱입니다.

## 구성 (28p)

| 페이지 | 내용 |
|---|---|
| 01 | Cover — 직무 · 핵심 강점 |
| 02–03 | **About Me — 프로필 · 경력 · 프로젝트 · 학력 · 자격 · 기술** (2페이지) |
| 04 | Key Achievements — 한 장으로 보는 핵심 |
| 05–08 | Contents · How I Work · Strengths · Selected Work |
| 09–16 | Project 01 MOVIN — 리깅 문제 해결 |
| 17–21 | Project 02 AI:REPLY — 한국우편사업진흥원 |
| 22–24 | Project 03 Rebuilder AI — 3D 콘텐츠 / 기업용 AI 서비스 |
| 25 | Project 04 Biafedia — BIAF |
| 26 | Research — KCGS 2024 물리 기반 군중 시뮬레이션 |
| 27–28 | Review · Thank You |

전문가 페르소나 리뷰와 이력서 대조 기록은 [`REVIEW.md`](REVIEW.md)에 있습니다.

## 빌드

슬라이드는 HTML/CSS로 작성하고 Chromium으로 1920×1080 렌더링한 뒤 PPTX·PDF로 패키징합니다.
(레퍼런스 PDF도 동일하게 디자인 툴에서 내보낸 이미지 기반입니다.)

```bash
cd src
python3 build.py          # deck.html → PNG → PPTX + PDF
python3 build.py --png    # 렌더링까지만
python3 check.py          # 슬라이드 밖으로 넘친 요소 검사
```

- `src/styles.css` — 디자인 시스템(색상 토큰, 카드, 타이포, 프로젝트별 액센트)
- `src/_0*.html` — 섹션별 슬라이드 파티션
- `assets/img/` — 원본 PPT에서 추출한 실제 작업 이미지
- `dist/` — 산출물

## 수정 방법

`src/_0*.html`에서 해당 슬라이드를 고친 뒤 `python3 build.py`를 다시 실행하면 됩니다.
프로젝트별 액센트 색은 슬라이드 `<section>`의 `t-movin` / `t-reply` / `t-rebuilder` /
`t-biaf` / `t-research` 클래스로 바뀝니다.
