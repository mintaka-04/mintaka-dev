---
title: "EC2 워커 에러 알람 — CloudWatch Metric Filter + SNS 이메일 알림 설정"
date: 2026-05-14
draft: true
categories: ["devlog"]
tags: ["ec2", "cloudwatch", "alarm", "sns", "monitoring", "aws"]
---

## 배경

지난번에 CloudWatch Logs 스트리밍까지 붙여놨지만, 워커가 에러를 뱉어도 직접 콘솔에서 로그를 열기 전까지는 알 방법이 없었다.
이번엔 `moodot-clone/worker` 로그 그룹에 에러 감지 알람을 붙여서 이메일로 받을 수 있도록 설정했다.

## 구조

```
로그 텍스트 → Metric Filter (ERROR 감지) → CloudWatch Metric → Alarm → SNS → 이메일
```

CloudWatch 알람은 텍스트가 아닌 숫자(지표)에만 걸 수 있다.
Metric Filter가 로그에서 패턴을 감지할 때마다 카운터를 +1 해주는 역할이다.

## 구현

### 1. Metric Filter 생성

`CloudWatch → Log groups → moodot-clone/worker → Metric filters → Create metric filter`

**필터 패턴**

```
?ERROR ?Exception ?Traceback
```

`?` 접두사는 OR 조건이다. 셋 중 하나라도 로그 라인에 등장하면 매칭된다.
`ERROR Exception Traceback` (공백 구분, `?` 없음)으로 쓰면 AND 조건이 되어 세 단어가 동시에 있는 줄만 잡힌다.

참고로 CloudWatch 필터 패턴은 **대소문자를 구분**한다. Python 표준 `logging` 모듈은 `ERROR`, `Exception`, `Traceback`을 대문자로 출력하므로 이 패턴으로 충분하다.

**지표 설정**

| 필드 | 값 |
|------|----|
| 필터 이름 | `worker-error-filter` |
| 지표 네임스페이스 | `MoodotWorker` |
| 지표 이름 | `WorkerErrorCount` |
| 지표 값 | `1` |
| 기본값 | `0` |

- 지표 값 `1`: 패턴 매칭 시 카운터 +1
- 기본값 `0`: 매칭 없을 때 0으로 유지 (이 값이 없으면 데이터 포인트 자체가 없어서 알람이 "데이터 부족" 상태로 빠질 수 있음)
- 단위, 차원(Dimension): 지금은 비워도 무방. EC2가 한 대라 세분화가 필요 없음

### 2. CloudWatch Alarm 생성

Metric Filter 생성 완료 후 해당 필터 체크 → **Create alarm**

| 설정 | 값 |
|------|----|
| Namespace | `MoodotWorker` |
| Metric name | `WorkerErrorCount` |
| Period | 5분 |
| Statistic | Sum |
| Threshold | `>= 1` |
| Evaluation periods | 1 |
| Missing data treatment | `notBreaching` (데이터 없으면 정상으로 간주) |

5분 안에 에러 로그가 1건이라도 나오면 알람이 울리는 구조다.

### 3. SNS 이메일 알림 설정

알람 생성 중 알림 액션 → **새 SNS 주제 생성** → 이메일 입력

SNS 알림 옵션 세 가지:

| 옵션 | 언제 쓰나 |
|------|-----------|
| 기존 SNS 주제 선택 | 이미 만들어둔 주제 재사용 |
| 새 주제 생성 | 처음 만들 때 |
| 주제 ARN을 사용하여 다른 계정에 알림 | 다른 AWS 계정으로 보낼 때 |

**중요:** SNS 주제를 새로 만들면 입력한 이메일 주소로 **"AWS Notification - Subscription Confirmation"** 메일이 온다. 반드시 **Confirm subscription** 을 클릭해야 실제 알람 메일을 받을 수 있다. 이걸 빠뜨리면 알람이 울려도 메일이 오지 않는다.

## 비용

- CloudWatch Alarm: 표준 해상도 기준 **10개까지 무료**, 초과 시 $0.10/알람/월
- SNS 이메일 알림: **완전 무료**

## 동작 확인

콘솔에서 알람 상태 강제 변경은 지원하지 않는다 (CLI 전용).
AWS CLI로 테스트하려면:

```bash
aws cloudwatch set-alarm-state \
  --alarm-name "<알람 이름>" \
  --state-value ALARM \
  --state-reason "테스트" \
  --region ap-northeast-2
```

## 결과

- `moodot-clone/worker` 로그 그룹에 Metric Filter 생성 완료
- CloudWatch Alarm + SNS 이메일 알림 연결 완료
- 이메일 구독 확인(Confirm subscription) 완료
- 이제 워커에서 ERROR / Exception / Traceback 이 찍히면 5분 내에 이메일로 통보됨
