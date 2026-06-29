---
title: "ECS Fargate + ALB + RDS api-server 구축하면서 헤맨 것들"
date: 2026-06-29
draft: true
categories: ["troubleshooting"]
tags: ["aws", "ecs", "fargate", "alb", "rds", "jwt", "supabase", "postgresql"]
---

## 개요

Express/TypeScript api-server를 ECS Fargate에 올리고, ALB → `api.mintaka04.xyz` 도메인으로 연결하는 작업을 하면서 삽질한 것들 기록. 각 문제마다 증상 → 원인 → 해결 순으로 정리.

---

## 1. ALB health check 계속 timeout 나는 문제

### 증상
ECS 서비스 태스크는 `RUNNING` 상태인데, ALB 대상 그룹에서 타겟이 계속 `unhealthy` (request timed out).

### 원인
ALB에 붙어있는 보안 그룹이 잘못됐다.

ALB를 만들 때 `default` 보안 그룹이 자동으로 붙었고, 나는 `moodotclone-alb-sg` (443 인바운드 허용)를 만들어뒀는데 ALB에 적용하지 않은 상태였다. 결국 ALB → 타겟 간 통신은 되지만, 외부 → ALB 80/443 트래픽이 막혀서 health check 패킷 자체가 통과 못 했던 것.

### 해결
ALB → 보안 그룹에 `moodotclone-alb-sg` 추가.

### 교훈
ALB 생성 직후 보안 그룹 목록을 반드시 확인할 것. `default` 그룹이 붙어있으면 의심.

---

## 2. "Target is in Availability Zone not enabled for the load balancer"

### 증상
ECS 서비스 배포 후 타겟이 등록되지 않거나, 등록돼도 트래픽이 흐르지 않음. ECS 콘솔 이벤트에 위 에러 메시지 등장.

### 원인
ECS 서비스 서브넷과 ALB가 커버하는 AZ가 달랐다.

ALB는 `ap-northeast-2a`, `ap-northeast-2b` 두 AZ만 커버하도록 설정했는데, ECS 서비스를 생성할 때 서브넷 4개를 전부 선택했다. `ap-northeast-2c`, `ap-northeast-2d` 서브넷에 올라간 태스크는 ALB가 라우팅할 수 없어서 타겟 등록 자체가 실패.

### 해결
ECS 서비스 → 업데이트 → 서브넷을 ALB가 커버하는 `ap-northeast-2a`, `ap-northeast-2b` 두 개만 선택.

### 교훈
ECS 서비스 서브넷 ⊆ ALB AZ 여야 한다. ALB 먼저 만들었으면 ALB가 커버하는 AZ를 먼저 확인하고 서비스 서브넷 선택할 것.

---

## 3. CD 파이프라인이 성공했는데 새 이미지가 배포 안 되는 문제

### 증상
GitHub Actions 파이프라인은 `success`인데, CloudWatch 로그에 찍히는 버전이 그대로. ECR에는 새 이미지 있음.

### 원인
파이프라인이 `aws ecs update-service --force-new-deployment`만 하고, task definition 새 revision을 등록하지 않았다.

`--force-new-deployment`는 현재 task definition의 이미지를 다시 pull하는 것이지, 이미지 태그를 업데이트하는 게 아니다. 기존 task definition이 `:latest`가 아닌 특정 SHA를 가리키고 있으면 새 push와 관계없이 구 이미지로 계속 뜬다.

### 해결
파이프라인에서 현재 task definition을 읽어서 이미지만 새 SHA로 바꾼 뒤, `register-task-definition`으로 새 revision을 등록하고 `update-service`를 한다.

```yaml
- name: Deploy to ECS
  env:
    ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
  run: |
    TASK_DEF=$(aws ecs describe-task-definition --task-definition moodotclone-api-server --no-cli-pager)
    NEW_TASK_DEF=$(echo $TASK_DEF | python3 -c "
    import json, sys
    td = json.load(sys.stdin)['taskDefinition']
    for c in td['containerDefinitions']:
        if c['name'] == 'api-server':
            c['image'] = '$ECR_REGISTRY/moodotclone/api-server:${{ github.sha }}'
    keys = ['family','taskRoleArn','executionRoleArn','networkMode','containerDefinitions',
            'volumes','placementConstraints','requiresCompatibilities','cpu','memory','ephemeralStorage']
    print(json.dumps({k: td[k] for k in keys if k in td}))
    ")
    aws ecs register-task-definition --cli-input-json "$NEW_TASK_DEF" --no-cli-pager
    aws ecs update-service \
      --cluster moodotclone \
      --service moodotclone-api-server \
      --task-definition moodotclone-api-server \
      --force-new-deployment \
      --no-cli-pager
```

### 교훈
`force-new-deployment`는 "이미지 교체"가 아니라 "재시작"이다. 새 이미지를 배포하려면 새 task definition revision을 반드시 등록해야 한다.

---

## 4. API 경로에 슬래시가 두 개 (`//api/memories`)

### 증상
CloudWatch 로그에 요청 경로가 `//api/memories`, `//api/auth/merge`로 찍힘. Express 라우터에서 404 반환.

### 원인
Vercel 환경변수 `API_SERVER_URL`에 trailing slash가 붙어있었다.

```
API_SERVER_URL = "https://api.mintaka04.xyz/"  ← 마지막 /
```

api-client.ts에서 URL을 이렇게 조합했다.

```typescript
fetch(`${API_BASE}${path}`, ...)  // path = "/api/memories"
// 결과: "https://api.mintaka04.xyz//api/memories"
```

Express는 `//api/memories`를 `/api/memories`와 다른 경로로 처리해서 404.

### 해결
Vercel 환경변수에서 trailing slash 제거. 또는 api-client.ts에서 `API_BASE.replace(/\/$/, "")` 처리.

### 교훈
URL 조합 시 trailing slash 여부를 신경쓸 것. Vercel 환경변수 값은 복사할 때 실수로 `/`가 붙기 쉽다.

---

## 5. Supabase JWT가 RS256인데 HS256으로 검증하려 한 문제

### 증상
CloudWatch에 `[auth] jwt.verify failed: JsonWebTokenError: invalid algorithm` 로그 반복. 클라이언트에서 401 "유효하지 않은 토큰" 반환.

### 원인
Supabase 신규 프로젝트는 JWT를 **RS256** (비대칭 알고리즘)으로 서명한다. 그런데 api-server에서는 `jwt.verify(token, jwtSecret)`로 검증했는데, `jwtSecret`이 plain string이면 jsonwebtoken은 HMAC (HS256) 계열만 허용한다. RS256 토큰을 string secret으로 검증하려 하면 "invalid algorithm" 에러.

Supabase 대시보드의 "JWT Secret"은 anon key/service key 생성용이지, 실제 토큰 서명 검증에 쓰는 키가 아니다.

### 해결
Supabase의 JWKS 엔드포인트에서 공개키를 가져와서 검증. 새 패키지 없이 Node.js 내장 `crypto`와 `https`만 사용.

```typescript
import jwt from "jsonwebtoken"
import https from "https"
import { createPublicKey } from "crypto"

let cachedPublicKey: string | null = null

async function getPublicKey(): Promise<string> {
  if (cachedPublicKey) return cachedPublicKey
  const supabaseUrl = process.env.SUPABASE_URL!
  return new Promise((resolve, reject) => {
    https.get(`${supabaseUrl}/auth/v1/.well-known/jwks.json`, (res) => {
      let data = ""
      res.on("data", (chunk) => (data += chunk))
      res.on("end", () => {
        try {
          const { keys } = JSON.parse(data)
          const key = createPublicKey({ key: keys[0], format: "jwk" })
          cachedPublicKey = key.export({ type: "spki", format: "pem" }) as string
          resolve(cachedPublicKey)
        } catch (e) { reject(e) }
      })
    }).on("error", reject)
  })
}

// jwt.verify(token, await getPublicKey())
```

JWKS는 첫 요청에만 fetch하고 메모리에 캐싱.

### 교훈
Supabase가 HS256을 쓴다고 가정하지 말 것. 프로젝트 생성 시기에 따라 RS256이 기본일 수 있다. JWT 검증 에러가 나면 토큰 헤더의 `alg` 필드부터 확인.

---

## 6. DATABASE_URL 비밀번호 특수문자 미인코딩

### 증상
ECS 태스크가 뜨자마자 502. CloudWatch에 `TypeError: invalid URL, input: [redacted], base: 'postgres:/base'` 에러.

### 원인
RDS 비밀번호에 `@`, `!` 등 URL 예약 문자가 포함됐는데, SSM에 저장한 `DATABASE_URL`에서 URL 인코딩을 안 했다.

```
postgresql://user:p@ssword!@host:5432/db
                    ^       ^
                    이 문자들이 URL 파싱을 깨뜨림
```

pg 라이브러리가 connection string을 URL로 파싱할 때 `@`를 host 구분자로 해석해서 URL 구조가 깨진다. Node.js 20의 Ada URL 파서는 이런 경우를 "invalid URL"로 엄격하게 거부.

에러 메시지의 `base: 'postgres:/base'`는 실제 connection string의 일부가 redact/truncate된 것.

### 해결
비밀번호의 특수문자를 URL 인코딩.

| 문자 | 인코딩 |
|------|--------|
| `@`  | `%40`  |
| `!`  | `%21`  |
| `#`  | `%23`  |
| `$`  | `%24`  |
| `%`  | `%25`  |
| `^`  | `%5E`  |

```
postgresql://user:p%40ssword%21@host:5432/db
```

### 교훈
DATABASE_URL에 비밀번호 넣을 때 특수문자 있으면 반드시 URL 인코딩. 특히 `@`는 URL에서 userinfo와 host 구분자라 파싱이 완전히 깨진다.

---

## 7. PostgreSQL BIGSERIAL → JavaScript string → Python int 타입 불일치

### 증상
Python 워커 CloudWatch에 `asyncpg.exceptions.DataError: invalid input for query argument $1: '1' ('str' object cannot be interpreted as an integer)` 반복.

### 원인
3계층 타입 변환 문제.

1. PostgreSQL `memories.id` 컬럼 타입: `BIGSERIAL` (64비트 정수)
2. node-postgres (pg) 라이브러리: **BIGINT/BIGSERIAL은 JavaScript string으로 반환** (JavaScript number가 64비트 정수를 정확히 표현 못하기 때문)
3. Next.js API route에서 `id`를 SQS 메시지에 담으면 `{ "memory_id": "1" }` (문자열)
4. Python에서 `json.loads(body)['memory_id']` → `'1'` (str)
5. asyncpg에 `WHERE id = $1` 파라미터로 전달하면 타입 에러

```typescript
// Next.js route.ts
const { id: memoryId } = await apiRequest<{ id: number }>("/api/memories", {...})
// TypeScript 타입은 number지만 런타임 값은 실제로 string "1"

MessageBody: JSON.stringify({ memory_id: memoryId })
// → '{"memory_id":"1"}' (의도치 않게 문자열)
```

### 해결
두 군데 모두 수정.

```typescript
// route.ts — SQS 메시지 보낼 때 명시적으로 Number() 변환
MessageBody: JSON.stringify({ memory_id: Number(memoryId) })
```

```python
# main.py, worker.py — SQS 메시지 받는 쪽에서도 int() 변환
memory_id = int(payload['memory_id'])
```

### 교훈
node-postgres는 `BIGINT`, `BIGSERIAL`, `NUMERIC` 타입을 JavaScript string으로 반환한다. TypeScript 타입이 `number`여도 런타임에서 실제로는 string일 수 있다. 크로스 언어 경계(JSON → Python)를 넘길 때 타입을 명시적으로 변환할 것.
