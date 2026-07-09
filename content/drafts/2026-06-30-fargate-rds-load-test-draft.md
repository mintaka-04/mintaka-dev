# 2026-06-30 | Fargate + RDS 전환 후 부하 테스트 결과 분석

## 오늘 헤맨 것들

### 1. SQS에 메시지가 안 들어가는 문제
- 증상: `[SQS] 전송 성공` 로그는 찍히는데 queue에 메시지가 안 보임
- 원인: rule-worker가 event-queue를 폴링하면서 받자마자 소비하고 있었음. 없는 게 정상
- 실제 문제는 따로 있었음 → rule-worker가 Supabase DB를 보고 있어서 RDS에 저장된 memory를 못 찾음 → "memory 없음, skip"

### 2. rule-worker가 Supabase DB에 연결 중
- 원인: SSM `/moodotclone/DATABASE_URL`이 Supabase URL 그대로였음
- api-server는 RDS 마이그레이션 시 `/moodotclone/api-server/DATABASE_URL`에 별도로 저장
- rule-worker는 `/moodotclone/DATABASE_URL`을 읽는데 이 값이 갱신이 안 됐던 것
- 해결: SSM `/moodotclone/DATABASE_URL` → RDS URL로 업데이트 후 force new deployment

### 3. ai-worker DATABASE_URL 미설정으로 계속 롤백
- 증상: moodotclone-worker 업데이트 시 circuit breaker 임계값 초과로 계속 롤백
- 원인: 태스크 정의에 DATABASE_URL ValueFrom SSM ARN을 오타 입력
- 해결: ARN 정확히 재입력 후 배포 성공

### 4. /api/memories 인증 에러 (1시간마다 발생)
- 증상: 접속 중에도 `인증이 필요합니다` 에러 간헐적 발생
- 원인: `middleware.ts` 누락. Supabase SSR은 미들웨어가 매 요청마다 액세스 토큰을 갱신해야 함. 없으면 토큰 만료(기본 1시간) 후 서버 사이드 `getSession()`이 null 반환
- 해결: `middleware.ts` 추가 (`supabase.auth.getUser()` 호출로 토큰 자동 갱신)

### 5. api-server/node_modules 커밋 문제
- 원인: `.gitignore`에 `/node_modules`(루트만)만 있고 `**/node_modules` 누락
- 해결: `.gitignore`에 `**/node_modules` 추가 후 `git rm -r --cached api-server/node_modules`

---

## 오늘 변경한 것들

| 항목 | 내용 |
|------|------|
| SSM `/moodotclone/DATABASE_URL` | Supabase URL → RDS URL |
| ai-worker 태스크 정의 | `DATABASE_URL` ValueFrom SSM 추가 |
| `middleware.ts` 추가 | Supabase 세션 갱신 (토큰 만료 버그 수정) |
| SSE 코드 전체 제거 | `app/api/events/route.ts`, `api-server/src/routes/events.ts`, `components/ai/ai-insight.tsx` EventSource, `service/worker.py` notify_browser |
| `.gitignore` 수정 | `**/node_modules` 추가 |
| health check 로그 제거 | ALB가 30초마다 찍어서 CloudWatch 스팸 발생 |
| SQS 디버그 로그 정리 | `logger.error` → `logger.info`로 복원 |
| k6 thresholds 갱신 | 새 베이스라인 기준으로 업데이트 |
| RDS `memories.processed` 컬럼 DROP | `ALTER TABLE memories DROP COLUMN IF EXISTS processed` |

---

## 부하 테스트 결과

### 베이스라인 (VU 1, 10회, 1회차 cold start 제외)

| 지표 | 값 |
|------|-----|
| avg | 1.02s |
| p95 | 1.34s |
| 에러율 | 0% |

- 임계점: avg > 2.04s / p95 > 2.68s
- 한계점: avg > 4.08s / p95 > 5.36s

이전 베이스라인(6/6, avg 0.94s / p95 1.01s) 대비 p95가 0.33s 증가. Vercel → Supabase 직접 호출 → Vercel → ECS api-server → RDS 구조로 한 홉 추가된 영향.

### 점진적 부하 테스트 (최대 100VU)

**전체 결과**

| 지표 | 값 | 임계점 대비 |
|------|-----|------------|
| avg | 0.83s | 41% 수준 |
| p95 | 1.21s | 45% 수준 |
| 에러율 | 0% | - |
| 총 요청수 | 30,010 | - |

**단계별 결과**

| 단계 | avg | p95 | 요청수 |
|------|-----|-----|--------|
| 1단계 유지 (10VU) | 0.881s | 1.465s | 1,306 |
| 2단계 유지 (30VU) | 0.837s | 1.212s | 4,038 |
| 3단계 유지 (50VU) | 0.831s | 1.202s | 6,761 |
| 4단계 유지 (100VU) | 0.823s | 1.194s | 13,594 |

VU 증가할수록 오히려 응답속도가 빨라지는 경향. Vercel 서버리스 특성상 요청이 많아질수록 함수 인스턴스가 warm 상태로 유지되어 cold start 오버헤드가 감소하기 때문.

---

## SQS + Auto Scaling 분석

### event-queue (rule-worker 입력)

| 지표 | 관찰 내용 |
|------|-----------|
| sent vs deleted | 50VU까지 완전 동기 → 100VU 구간부터 벌어짐 (200~300개 차이) |
| visible | 44분까지 최대 11개, 이후 증가하여 47분 피크, 부하 종료 후 0 |
| oldest | 최대 1분 (안정권) |
| task 변화 | 1개 유지하다 부하 종료 직전(48분)에 4개로 scale-out |

**소견**: scale-out 임계값(visible > 1440)이 이 부하 패턴에서 너무 높음. 44분에 rule-worker CPU 98% 도달했지만 task는 1개 유지. CPU 기반 Auto Scaling 정책 추가 필요. SQS 임계값 재조정도 필요.

### rule-worker Auto Scaling 이벤트

| 시간 | 이벤트 |
|------|--------|
| 13:47 | scale-out 알람 → 1 → 4 (태스크 3개 추가) |
| 13:50 | scale-in → 4 → 3 |
| 13:53 | scale-in → 3 → 2 |
| 13:56 | scale-in → 2 → 1 |

scale-in은 3분 cooldown마다 -1씩 순차 축소. 정책대로 동작 확인.

### ai-queue (ai-worker 입력)

| 지표 | 관찰 내용 |
|------|-----------|
| oldest | 최대 10.5분 (52분) → **실질적 병목** |
| visible | 38분부터 47분까지 107 → 8,030 선형 증가 |
| sent vs deleted | 36분까지 유사, 이후 sent는 VU따라 증가 / deleted는 평탄, 44분 일시적 증가(780) 후 41까지 감소 |
| task 변화 | 45~47분: 4개 / 48~50분: 7개 / 51분+: 10개 |

**소견**:
- task 4개 → visible 계속 증가, 효과 없음
- task 7개 → visible 증가 완화, 임계 상태 유지
- task 10개 → deleted 오히려 감소, notvisible 급락 → **역효과**

10개에서 역효과가 나타난 원인은 OpenAI API 동시 요청 rate limit으로 추정. 수평 확장이 외부 API 병목 구간에서는 효과 없음. ai-worker의 병목은 ECS 리소스가 아닌 LLM API.

### OpenAI Rate Limit 오류 분석 (CloudWatch Logs Insights)

부하 테스트 중 `/ecs/moodotclone-worker` 로그에서 45분부터 HTTP 429 RateLimitError 발생 확인.

| Rate Limit 유형 | 오류 수 | 의미 |
|----------------|--------:|------|
| TPM | 1,522 | 분당 토큰 사용량 초과 |
| RPM | 1,184 | 분당 요청 수 초과 |
| RPD | 1,004 | 일일 요청 수 초과 |
| **합계** | **3,710** | 전체 429 오류 수 |

**핵심 발견**: RPM뿐 아니라 TPM이 가장 많이 걸림. 동시 요청 수를 줄이더라도 프롬프트 길이(토큰 수)가 많으면 TPM 한도에 별도로 걸릴 수 있음. concurrency 제어 설계 시 RPM과 TPM을 모두 고려해야 함.

---

## 개선 필요 사항

| 항목 | 내용 |
|------|------|
| rule-worker scale-out 임계값 재조정 | visible > 1440 → 하향 조정 필요 |
| CPU 기반 Auto Scaling 추가 | CPU > 70% 시 scale-out (SQS 기준보다 빠른 반응) |
| ai-worker concurrency 제어 | RPM + TPM 동시 고려한 동시 LLM 호출 수 제한 / 지수 백오프 도입 |
| `frequency_limit.enabled` 복원 | 부하 테스트 후 True로 변경 필요 (현재 False) |
