# figure.rebuilder.ai/3dgen 배포

## 구조

```
브라우저 ── https://figure.rebuilder.ai/3dgen/ ──▶ nginx (TLS) ──▶ figure-3dgen 컨테이너 :8030
                                                                  ├─ /3dgen/        React 빌드 (frontend/dist)
                                                                  ├─ /3dgen/api/*   FastAPI
                                                                  └─ Blender(bpy 4.2) 파트별 서브프로세스
```

- 프런트는 `base: "/3dgen/"`로 빌드되고 API를 **같은 출처의 상대 경로** `/3dgen/api`로 부른다(절대 호스트·키 없음).
- 앱이 `/3dgen` 접두어를 그대로 받으므로 프록시는 접두어를 **유지**한다(`proxy_pass http://127.0.0.1:8030;` 뒤 슬래시 없음).
- `OPENAI_API_KEY`는 호스트의 기존 비밀 저장소 값을 **재사용**해 런타임 환경변수로 주입한다. 이미지·저장소·로그에 넣지 않는다.
- 작업 데이터는 `/data` 볼륨(업로드 사진·GLB·결과). 사진은 고객 자료이므로 보존 기간·접근 권한을 정한다.

## 절차

```bash
# 서버에서 (figure-3dgen/ 폴더)
docker compose -f deploy/docker-compose.yml build
OPENAI_API_KEY=<기존 키> docker compose -f deploy/docker-compose.yml up -d
curl -s http://127.0.0.1:8030/3dgen/api/health     # {"ok":true,"blender":true,"astra":true,...}
# nginx: deploy/nginx-3dgen.conf 블록을 figure.rebuilder.ai server{}에 추가 → nginx -t && systemctl reload nginx
curl -s https://figure.rebuilder.ai/3dgen/api/health
```

CloudFront/S3 정적 호스팅을 쓰는 경우: `/3dgen/*` 동작을 이 오리진으로 보내고 `/3dgen/api/*`는 캐시 끔, 업로드 크기(≥160MB)·타임아웃(≥300s)을 늘린다. 2026-09-27 기록상 `figure.rebuilder.ai/gen`은 CloudFront/S3 403이었다 — 현재 오리진 구성 소유자를 먼저 확인한다.

## 스모크 체크리스트

1. `/3dgen/api/health` 200, `blender: true`, `astra: true`
2. `/3dgen/` UI 로드, 콘솔 오류 없음, 모바일 가로 넘침 없음
3. 사진+GLB 업로드 → intake done → Astra 다시점 생성 → 승인 → 분할 분석 → Editable → 암수 → Moldable
4. ZIP 다운로드(BLEND/OBJ/STL/GLB/report)
5. HTTP 200만으로 CAD 품질 완료를 주장하지 않는다 — 게이트 표를 함께 보고

## 이 세션의 상태 (정직한 기록)

- 로컬: uvicorn이 `/3dgen/`(UI)·`/3dgen/api/health`를 200으로 서빙, 헤드리스 Chromium에서 6단계 화면 확인.
- **도메인 미배포**: 이 클라우드 세션의 egress 정책이 `figure.rebuilder.ai:443`을 403으로 차단했고, 서버 접근 권한도 없다.
- **Docker 이미지 빌드 미검증**: 컨테이너에 Docker 데몬이 없었다. 동일 런타임(Python 3.11 + bpy 4.2)에서 API·Blender 경로는 검증됨.
