---
title: "rule-worker Auto Scaling — 서로 다른 지표를 쓴 scale-out/scale-in이 충돌해서 desired count가 출렁인 문제"
date: 2026-07-07
draft: true
categories: ["troubleshooting"]
tags: ["aws", "ecs", "fargate", "auto-scaling", "sqs", "cloudwatch", "step-scaling"]
---

## 개요

이중 큐(event-queue/ai-queue) 아키텍처로 전환한 뒤, rule-worker(ECS 서비스 `moodotclone-main`)의 Auto Scaling이 부하테스트 중 desired count를 예측 불가능하게 오르내리는 문제를 발견하고 원인을 찾아 고친 기록. 최종 원인은 단순한 "정책 충돌"이 아니라 **scale-out과 scale-in이 서로 다른 CloudWatch 지표를 독립적으로 평가하고 있었던 것**이었고, 그 과정에서 기각한 대안들과 판단 근거도 같이 정리.

(주: 이 글은 나중에 devlog/troubleshooting으로 재정리할 걸 염두에 두고 최대한 상세하게 남겨둔 초안. 실제 게시 시 분량 조절 필요.)

---

## 증상

7/2 점진적 부하 테스트(gradual, 10→30→50→100VU) 중 ECS 서비스 이벤트 로그를 보니 `moodotclone-main`의 desired count가 이렇게 튀었다.

```
15:07 → 4
15:08 → 7
15:10 → 9 (중간에 6으로 내려갔다가 다시 9로)
15:11 → 9 (7로 내려가려다 무효화)
15:12 → 9
15:13 → 9 (7로 내려갔다가 다시 9)
```

패턴: **15:08, 15:10, 15:11, 15:12, 15:13 — 5분 연속으로 scale-out과 scale-in 알람이 매분 동시에 ALARM 상태**였다. 부하가 15:11경 사실상 끝났는데도(SQS Sent 급감) 15:17까지 새 task가 계속 뜨는 등, 알람 진동의 잔여 효과로 보이는 현상도 관찰됨.

---

## 원인 분석

### 1차 조사 — 정책이 몇 개인지부터 확인

ECS 콘솔 → `moodotclone-main` → Auto Scaling → Scaling policies에서 확인해보니 **정책이 4개**였다.

| 정책명 | 지표 | 조건 | 액션 | 휴지기간 |
|---|---|---|---|---|
| `rule-worker-scale-out` | Visible | > 102 | +3 | 60s |
| `rule-worker-scale-in` | Visible | < 50 | -1 | 120s |
| `...event-queue-rule-worker-scale-out-message-sent` | **Sent** | > 1000 | +3 | 60s |
| `...event-queue-rule-worker-scale-in` | Visible | < 50 | -1 | 120s |

즉 scale-out 2개(Visible 기준 / Sent 기준), scale-in 2개(둘 다 Visible 기준, 완전 중복)가 붙어 있었다. scale-in 중복은 하나 삭제.

### 2차 조사 — Visible 기반 scale-out을 지웠다가 다시 살림

처음엔 "scale-out 2개가 서로 충돌하는 것"이라 생각해서 Visible 기반 scale-out(`rule-worker-scale-out`)을 지웠다. 그런데 CloudWatch 데이터를 다시 보니:

**event-queue Visible 값 (7/2, 14:56~15:15, 20분 전체)**

거의 전부 0, 15:03에 딱 한 번 1, 15:07에 딱 한 번 67(피크). **테스트 내내 102를 한 번도 안 넘었다.** ECS 서비스 이벤트 로그에도 desired count 변화는 전부 `scale-out-sent`(Sent 기준)와 `scale-in`으로만 기록돼 있고, Visible 기반 scale-out이 원인으로 잡힌 적이 없었다.

→ **Visible 기반 scale-out은 이 사건과 무관했다.** 삭제는 잘못된 조치였고, 나중에 재생성해서 복구.

### 진짜 원인 — scale-in이 사실상 "상시 대기 상태"였다

event-queue **NotVisible**(처리 중인 메시지 수)도 확인해보니 최고값이 6 — 역시 항상 낮음.

**왜 항상 낮으냐**: rule-worker는 rule 판단만 하고 끝나는 가벼운 작업이라 처리 속도가 매우 빠르다. 그래서 유입량(Sent)이 아무리 튀어도 자기 큐(event-queue)에는 거의 안 쌓인다 — Visible이 "부하가 없다"가 아니라 "rule-worker가 원래 빠르다"를 나타내는 지표였던 것.

그 결과 scale-in 조건("Visible < 50")은 **거의 항상 참**이었다. 반면 scale-out 조건("Sent > 1000")은 실제 유입 부하에 따라 오르내리는 값. **서로 무관한 두 지표가 각각 독립적으로 평가되다 보니, 우연히 같은 평가 주기에 둘 다 ALARM 상태가 되는 순간이 반복적으로 발생**했고 그게 desired count 출렁임의 실제 원인이었다.

### ECS 이벤트 로그에서 발견한 흥미로운 디테일

15:08 이벤트 로그에 이런 문구가 있었다:

> desired → 3 요청했으나 "found it was later changed to 7" (scale-in, 무효화)

이건 AWS Application Auto Scaling이 scale-in을 실제로 적용하려는 시점에 desired count가 이미(다른 정책에 의해) 바뀌어 있는 걸 감지하고 스스로 적용을 취소한 것 — 일종의 동시성 보호 장치였다. 근데 이 보호가 **15:08, 15:12에는 걸렸지만 15:10, 15:13에는 안 걸려서** scale-in이 실제로 적용돼버렸다. 이 "가끔 막히고 가끔 안 막히는" 비일관성이 desired count가 예측 불가능하게 튄 이유.

(참고: 이 로그의 두 메시지가 화면에 표시되는 순서와 실제 API 호출 완료 순서가 다를 수 있다는 것도 확인 — ECS 콘솔 이벤트 탭의 표시 순서가 실제 인과관계와 반드시 일치하지 않을 수 있음. 정확한 순서를 보려면 CloudTrail에서 초 단위 타임스탬프를 봐야 함.)

---

## 기각한 대안들

### 대안 1: Target Tracking으로 전환 (backlog-per-task 커스텀 메트릭)

`ApproximateNumberOfMessagesVisible ÷ 실행 중 task 수`를 커스텀 메트릭으로 만들어 Target Tracking 하나로 scale-out/in을 통합 관리하는 방법. AWS가 목표값 하나로 자동 관리하므로 서로 다른 정책이 충돌할 구조 자체가 없어짐.

**기각 이유**: 반응 속도. 이 서비스는 이미 CPU 기반 Target Tracking을 6/30~7/1에 써봤다가 "5분 지연 + scale-in 충돌 문제"로 폐기한 전력이 있음. Target Tracking의 보수적인 평가 주기가 Step Scaling(1~2분 기대)보다 느릴 가능성이 높음.

### 대안 2: scale-in을 CPU 기반으로

**기각 이유**: 구조적으로 Visible과 똑같은 함정. CPUUtilization은 task당 평균값인데, scale-out이 먼저 성공해서 task 수가 늘면 같은 일이 더 많은 task에 나뉘어 **평균 CPU가 자동으로 뚝 떨어진다.** 실측 데이터로 확인:

| 시각 | event Sent | main CPU | main Tasks |
|---|---|---|---|
| 15:09 | 1131 | 57.04% | 6 |
| **15:10** | **1978** (여전히 높음) | **28.05%** (이미 낮음) | 7 |

Sent 기준으로는 "아직 부하 심함"인데 CPU 기준으로는 "널널함"이 동시에 참이 되는 상황 — Visible/Sent 충돌과 완전히 같은 패턴이 재현될 것으로 판단. 게다가 이 서비스에 CPU 기반 스케일링을 썼다가 "scale-in 충돌 문제"로 이미 한 번 폐기한 전례도 있어(구체적 로그는 안 남아있지만) 정황상 이번 판단과 일치.

---

## 해결

**scale-out과 scale-in을 같은 지표(Sent)로 통일하고, 두 임계값 사이에 dead band를 둠.**

- scale-out: Sent > 1000, +3, 휴지기간 60s (기존 유지)
- scale-in: Sent **< 300**, -1, 휴지기간 120s (Visible → Sent로 교체)
- Visible 기반 scale-out(>102, +3, 60s)은 원상 복구해서 유지 — 이건 애초에 이번 충돌의 원인이 아니었으므로 안전망으로 남겨둠

**왜 이렇게 하면 해결되냐**: 같은 값이 동시에 ">1000"이면서 "<300"일 수는 없다. 두 알람이 논리적으로 절대 동시에 ALARM 상태가 될 수 없는 구조가 되므로, 우연한 충돌 가능성이 원천 차단됨. 추가로 Sent는 완만하게 오르내리는 값이라 300~1000 사이를 지나가는 구간이 자연스러운 "무알람 버퍼"로 작동해 급격한 flapping도 방지됨.

**300이라는 값에 대한 정직한 평가**: 이건 검증된 값이 아니라 1차 추정값이다. 근거로 삼은 "idle 시점 Sent=25"는 부하테스트 시작 직전의 딱 한 개 데이터포인트라 근거가 약함. 실제 평소(비-테스트) 트래픽 기준 baseline을 재는 게 이상적이지만, 마감(7/15) 압박으로 일단 이 값으로 진행하고 다음 테스트 결과 보고 조정하기로 함.

CloudWatch 알람은 삭제 후 재생성이 아니라 **기존 알람의 지표/임계값만 편집** — Application Auto Scaling이 알람을 ARN으로 참조하므로, 알람 설정만 바꾸면 ECS 쪽 Step Scaling 정책은 안 건드려도 됨.

---

## 검증

**변수 통제**: ai-worker(moodotclone-worker)는 의도적으로 꺼두고(desired count 0), rule-worker 스케일링만 격리해서 재테스트. ai-worker 꺼진 동안 fallback-worker가 pending 레코드를 계속 재발견해 중복이 쌓이는 걸 막기 위해 DB에서 `pending`/`processing` 상태를 주기적으로 `filtered`로 정리하는 가드 실행(2분 간격 수동 반복).

**결과** (2026-07-07, k6 gradual test, 10:04 시작):

- **task 증감이 깔끔함** — scale-out만 순차적으로 반영되고, 관찰 구간 내 scale-in과의 충돌 없음
- **반응 속도 저하 없음** — Sent가 1000을 넘은 시점(10:11~12)부터 첫 scale-out 액션(10:15)까지 약 3~4분. 7/2의 크로싱→액션 반응시간(3분)과 동일한 수준
- **Vercel 응답 정상**: `avg=844.34ms`(임계 2140ms 대비 여유), `p(95)=1.23s`(임계 2860ms 대비 여유), **에러율 0%**(29,740건 전부 성공)

### 부수적으로 발견한 것 — rule-worker CPU 5분간 90~100% 포화

task가 늘어나는 도중에도(desired 4→7→9) CPU가 90~100%를 계속 찍는 구간이 있었다(10:16~10:20). 처음엔 "회귀 아닌가" 싶었는데, 7/2 데이터를 다시 보니 **똑같은 패턴이 그때도 있었다**(15:04~15:08, 73~99.97%) — 이번에 새로 생긴 문제가 아니라 원래 있던 특성이 이번엔 충돌 없이 깔끔하게 보였을 뿐.

**고치지 않기로 한 이유**: event-queue는 이 구간에도 안 밀렸고(Visible 낮음) Vercel 응답도 정상이었다 — 즉 사용자에게 보이는 영향이 없다. rule-worker는 API 요청 경로에 안 끼는 비동기 워커라 CPU가 빡빡해도 요청이 느려지거나 실패하지 않는 구조. "실제 문제가 생긴 뒤에 대응한다"는 프로젝트 원칙에 따라, 지금은 이걸 고칠 버그가 아니라 **~100VU 부근의 한계 구간 특성**으로만 기록해두고, 이후 ai-worker 포함 풀 파이프라인 테스트(스파이크/소크)에서 악화되는지만 계속 지켜보기로 함.

---

## 교훈

1. **scale-out/scale-in에 서로 다른 지표를 쓰면, 그 둘이 얼마나 무관한지와 무관하게 우연한 동시 충돌이 항상 가능하다.** 같은 지표 + dead band로 통일하면 이 가능성을 논리적으로 원천 차단할 수 있다.
2. **지표를 고를 때 "이 값이 실제로 부하 수준을 구분해주는가"를 반드시 확인할 것.** Visible은 rule-worker가 너무 빨라서 부하와 무관하게 항상 낮았다 — 지표 자체가 무의미했던 것이지 임계값 튜닝으로 고칠 문제가 아니었다.
3. **평균 기반 지표(CPU 등)는 scale-out 성공 자체가 그 지표를 떨어뜨릴 수 있다.** scale-in 판단에 쓸 때는 이 자기참조적 효과를 반드시 고려해야 한다.
4. **"회귀처럼 보이는 현상"은 이전 데이터와 직접 비교해서 원래 있던 특성인지 새로 생긴 문제인지 구분할 것.** 이번 CPU 포화도 비교 없이 봤으면 "고쳐야 할 새 버그"로 오인했을 뻔했다.
5. **발견한 모든 이상 현상을 다 고치려 들지 말 것.** 실제 사용자 영향(응답시간, 에러율) 기준으로 우선순위를 가리고, 영향 없는 건 "한계 구간의 특성"으로 기록만 해두는 것도 정당한 엔지니어링 판단이다.
6. **변수를 하나씩 격리해서 검증할 것.** ai-worker를 꺼두고 rule-worker 스케일링만 먼저 검증한 덕분에, 문제 원인을 더 명확하게 좁힐 수 있었다.
