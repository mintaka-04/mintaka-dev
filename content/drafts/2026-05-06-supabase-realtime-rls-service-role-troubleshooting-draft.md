---
title: "Supabase Realtime postgres_changes가 service_role 구독자에게 전달 안 되는 문제"
date: 2026-05-06
draft: true
categories: ["troubleshooting"]
tags: ["supabase", "realtime", "rls", "service_role", "postgresql"]
---

## 문제 상황

EC2 Python 워커가 `intervention_feedback` 테이블의 INSERT 이벤트를 구독하고 있었는데, 프론트엔드에서 실제로 row가 삽입돼도 워커의 콜백이 전혀 호출되지 않았다.

- Realtime publication에 테이블 추가 ✅
- 구독 코드 정상 실행 ✅
- 워커 재시작 후에도 동일 증상 ✅
- 에러 로그 없음

## 원인 분석

`memories` 테이블(정상 동작)과 `intervention_feedback` 테이블(미동작)의 RLS 정책을 비교했다.

```sql
SELECT policyname, cmd, roles, qual
FROM pg_policies
WHERE tablename IN ('memories', 'intervention_feedback')
ORDER BY tablename, cmd;
```

| 테이블 | cmd | roles | qual |
|--------|-----|-------|------|
| memories | SELECT | `{public}` | `auth.uid() = user_id` |
| intervention_feedback | SELECT | `{authenticated}` | `auth.uid()::text = user_id` |

Supabase에서 역할 구조:

| 역할 | 설명 |
|------|------|
| `anon` | 비로그인 사용자 |
| `authenticated` | 로그인한 일반 사용자 |
| `service_role` | 서버 사이드 (EC2 워커 등) |

- `TO public` → 모든 역할에 적용 (`service_role` 포함)
- `TO authenticated` → 일반 로그인 사용자에게만 적용 (`service_role` 미포함)

Supabase Realtime은 `postgres_changes` 이벤트를 전달하기 전에 구독자의 역할로 SELECT 가능 여부를 확인한다. EC2 워커는 `service_role` JWT로 연결하는데, `intervention_feedback`에는 `service_role`에게 적용되는 SELECT 정책이 없어서 이벤트가 드랍됐다.

`memories`가 정상 동작한 이유는 SELECT 정책이 `TO public`이라 `service_role`도 적용 대상에 포함돼 있었기 때문.

## 해결

SELECT 정책의 대상을 `authenticated`에서 `public`으로 변경.

```sql
DROP POLICY "Users can select own feedback" ON intervention_feedback;

CREATE POLICY "Users can select own feedback"
ON intervention_feedback
FOR SELECT
TO public
USING (auth.uid()::text = user_id);
```

`USING` 조건은 그대로 유지되므로 일반 사용자는 여전히 자신의 데이터만 조회 가능하다. `service_role`은 원래 RLS를 우회하므로 보안상 문제없다.

## 교훈

Supabase Realtime `postgres_changes`를 서버 사이드(service_role)에서 구독할 때, 해당 테이블의 SELECT RLS 정책이 `service_role`을 포함하는 역할(`public` 또는 `service_role`)로 설정돼 있어야 이벤트가 전달된다. `TO authenticated`만 있으면 서버 구독자는 이벤트를 받지 못한다.
