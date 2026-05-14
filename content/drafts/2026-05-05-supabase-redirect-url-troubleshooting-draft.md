---
title: "Vercel 배포 후 Google 로그인 ERR_CONNECTION_REFUSED"
date: 2026-05-05
draft: true
categories: ["troubleshooting"]
tags: ["vercel", "supabase", "oauth", "google-login"]
---

## 문제 상황

로컬에서는 정상 동작하던 Google 로그인이 Vercel 배포 후 실패했다.
브라우저 Network 탭에서 302 리다이렉트가 이어지다가 갑자기 `net::ERR_CONNECTION_REFUSED` 에러 발생.
Vercel Runtime Log에는 아무 로그도 찍히지 않았다.

## 원인 분석

Google OAuth 플로우는 다음 순서로 동작한다.

1. 앱 → Supabase로 OAuth 요청
2. Supabase → Google 인증 페이지로 리다이렉트
3. Google 인증 완료 → Supabase로 콜백
4. Supabase → 앱의 `/auth/callback`으로 리다이렉트

문제는 4번 단계에서 발생했다. Supabase Auth 설정의 **Site URL**이 `localhost:3000`으로 되어있어서, 인증 완료 후 Vercel URL이 아닌 localhost로 리다이렉트를 시도했다. 당연히 배포 환경에서는 localhost가 없으므로 연결이 거부된 것.

코드에서 `redirectTo`는 `window.location.origin`을 사용하고 있었지만, Supabase의 Site URL 설정이 이를 오버라이드했다.

## 해결 방법

Supabase 대시보드에서 URL 설정 변경.

**Authentication → URL Configuration**

- **Site URL**: `localhost:3000` → Vercel 배포 URL로 변경
- **Redirect URLs**: Vercel URL 패턴 추가 (`https://{vercel-url}/**`)

저장 후 재시도하니 정상적으로 로그인됐다.

## 교훈

Vercel 신규 배포 후 OAuth가 안 된다면 Supabase Site URL 설정부터 확인할 것.
Runtime Log에 아무것도 안 찍힌다면 요청 자체가 앱에 도달하지 못하는 것이므로 인프라/설정 레벨 문제일 가능성이 높다.
