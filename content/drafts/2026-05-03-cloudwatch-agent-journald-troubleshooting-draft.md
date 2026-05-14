---
title: "CloudWatch Agent 설정에서 journald 타입이 지원되지 않는 문제"
date: 2026-05-03
draft: true
categories: ["troubleshooting"]
tags: ["cloudwatch", "aws", "logging", "journald"]
---

## 문제 상황

CloudWatch Agent 설정 파일에 journald 로그 수집을 설정했더니 Agent 시작 실패:

```
E! Invalid Json input schema.
Under path : /logs/logs_collected | Error : Additional property journald is not allowed
E! configuration validation first phase failed. Agent version: 1.0.
```

설정한 내용:

```json
{
  "logs": {
    "logs_collected": {
      "journald": {
        "collect_list": [...]
      }
    }
  }
}
```

## 원인

`Additional property journald is not allowed` — `logs_collected` 안에 `journald`라는 속성이 이 버전의 CloudWatch Agent JSON 스키마에 정의되어 있지 않다.

현재 CloudWatch Agent가 `logs_collected` 아래 지원하는 타입:
- `files` — 텍스트 파일 수집
- `windows_events` — Windows 이벤트 로그

`journald`는 지원하지 않는다.

## journald란

systemd가 관리하는 로그 시스템. systemd 서비스(`moodot-worker` 등)가 stdout에 출력하는 내용을 자동으로 수집해서 바이너리 형식으로 저장한다. `journalctl -u moodot-worker`로 조회 가능.

평문 텍스트 파일이 아니기 때문에 CloudWatch Agent가 직접 읽으려면 별도 지원이 필요한데, 현재 버전에서는 없다.

## 해결

systemd 서비스 파일에 `StandardOutput`을 설정해 워커 로그를 전용 텍스트 파일로 리다이렉트하고, CloudWatch Agent는 그 파일을 `files` 타입으로 수집한다.

**서비스 파일 수정:**

```ini
[Service]
StandardOutput=append:/var/log/moodot-worker.log
StandardError=append:/var/log/moodot-worker.log
```

**Agent 설정:**

```json
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/moodot-worker.log",
            "log_group_name": "/moodot/worker",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
```

## 참고

CloudWatch Agent 설정 옵션 레퍼런스:
AWS 문서 → `CloudWatch agent configuration file` 검색 → Logs section
