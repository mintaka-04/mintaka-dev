# 프로젝트 로드맵

블로그에 올리지 않는 내부 방향성 문서.

## 현재 인프라 상태 (2026-05-03)

```
Vercel          — Next.js (FE + API routes)
  └─ Supabase   — DB + Realtime
EC2             — Python AI 에이전트 워커 (moodot-worker, systemd)
  └─ 포트 8000  — 헬스서버 내장
GitHub Actions  — CI/CD (SSM으로 EC2 배포)
```

---

## 단계별 로드맵

### 1단계 — 기본 모니터링 구축 (현재)

**목표:** 문제가 생겼을 때 최소한 볼 수 있는 상태 만들기

- [ ] CloudWatch Logs — 워커 로그 스트리밍
- [ ] CloudWatch Alarm — CPU 기본 알람

---

### 2단계 — 부하 실험 및 한계 탐색

**목표:** 실제 문제를 만들어보고 Vercel 무료 한계 도달

**Vercel / Supabase 쪽**
- 동시 요청 부하 테스트 → Supabase 커넥션 풀 고갈 재현
  - Vercel 서버리스 함수는 호출마다 새 커넥션을 열기 때문에 동시 요청이 많아지면 pool 한계에 도달
- Vercel 함수 타임아웃(10초) 한계 탐색
- 대역폭/실행시간 한도 소진 → 이관 명분 확보

**Python 워커 쪽**
- 감정 데이터 대량 삽입 → LLM 동시 호출 적체 재현
  - Claude API 응답이 느려서 asyncio 큐가 쌓임
  - `processed=false` 백로그 증가 확인
- 장기 실행 중 메모리 누수 / asyncio 이벤트 루프 문제 관찰

---

### 3단계 — 애플리케이션 메트릭 (Prometheus + Grafana)

**목표:** CloudWatch로 못 잡는 애플리케이션 레벨 메트릭 가시화

CloudWatch는 시스템 메트릭(CPU, 메모리)만 잡음.
워커의 내부 동작은 커스텀 메트릭이 필요:

- LLM API 호출 1건당 응답 시간
- 파이프라인 단계별 처리 시간
- 현재 처리 대기 중인 감정 수 (큐 깊이)
- 파이프라인 단계별 에러율

**구현 방향:**
- 워커의 기존 헬스서버(포트 8000)에 `/metrics` 엔드포인트 추가
- Prometheus가 주기적으로 긁어감
- Grafana 대시보드로 시각화

---

### 4단계 — Vercel 이관

**목표:** 2단계에서 확인한 한계를 근거로 더 나은 플랫폼으로 이관

이관 후보 (미결정):
- Railway
- Fly.io
- 자체 EC2 (워커랑 같은 서버 or 별도)

---

## 메모

- 각 단계의 작업/트러블슈팅은 vibe_githubpage 블로그 포스트로 기록
- 도구를 쓰기 위한 도구 도입이 아니라, 실제 문제가 생긴 뒤에 도입하는 것이 원칙
