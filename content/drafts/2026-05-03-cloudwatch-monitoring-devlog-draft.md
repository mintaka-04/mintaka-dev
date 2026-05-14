---
title: "EC2 워커 모니터링 — CloudWatch 로그 스트리밍 + CPU 알람 설정"
date: 2026-05-03
draft: true
categories: ["devlog"]
tags: ["ec2", "cloudwatch", "monitoring", "aws"]
---

## 배경

EC2 위에서 `moodot-worker` (Python AI 에이전트 워커)가 systemd 서비스로 돌아가고 있다.
지금까지는 배포 파이프라인(GitHub Actions + SSM)만 갖춰져 있었고, 워커가 죽거나 에러가 나도 직접 EC2에 접속해서 `journalctl`을 뒤지기 전엔 알 방법이 없었다.

최소한의 모니터링을 붙이기로 결정.

## 무엇을 모니터링할 것인가

모니터링의 층위를 나눠보면:

| 층위 | 내용 | 도구 |
|---|---|---|
| 인프라 메트릭 | CPU, 메모리, 디스크 | CloudWatch (Agent) |
| 로그 | 워커 실행 로그, 에러 | CloudWatch Logs |
| 알림 | 이상 감지 → 통보 | CloudWatch Alarm + SNS |

## 왜 CloudWatch인가

- 이미 AWS 환경 위에 있음 (EC2, IAM, SSM 세팅 완료)
- 추가 서비스 없이 IAM 정책 하나 + Agent 설치로 커버
- Grafana/Prometheus는 이 규모에서 과함

## 지금 당장 할 것 (최소 구성)

- [x] CloudWatch Logs 스트리밍 — 워커 로그 수집
- [x] CPU / 메모리 / 디스크 메트릭 수집
- [ ] CPU 사용률 Alarm — 나중에

나머지(메모리/디스크 메트릭, processed=false 적체 감지 등)는 실제 운영하면서 필요할 때 추가.

## 구현

### 1. EC2 IAM 역할에 정책 추가

IAM → 역할 → EC2에 붙어있는 역할 → 권한 추가 → `CloudWatchAgentServerPolicy` 연결

CloudWatch Agent가 EC2에서 AWS로 로그/메트릭을 전송하려면 이 권한이 필요하다.

### 2. CloudWatch Agent 설치 (SSM Run Command)

SSM Run Command는 EC2에 SSH 없이 명령어를 원격 실행하는 방법. CD 파이프라인에서 이미 쓰고 있는 방식이다.

AWS 패키지 설치는 `AWS-RunShellScript` 대신 `AWS-ConfigureAWSPackage`를 써야 한다. (트러블슈팅 참고)

- 문서: `AWS-ConfigureAWSPackage`
- Action: `Install`
- Name: `AmazonCloudWatchAgent`
- Version: 비워두기 (최신)

### 3. 로그 파일 설정

**왜 전용 로그 파일이 필요한가**

처음엔 `/var/log/syslog`에서 "moodot" 키워드로 필터링하는 방법을 검토했으나 두 가지 문제가 있다:
- moodot 단어가 없는 로그 라인은 누락될 수 있음
- 다른 프로그램 로그에 "moodot"이 포함되면 같이 수집됨

moodot-worker 로그만 정확히 수집하려면 전용 파일이 필요하다.

**systemd 서비스 파일 수정**

Python 코드 변경 없이 systemd 서비스 설정에서 출력을 전용 파일로 리다이렉트한다:

```ini
[Service]
StandardOutput=append:/var/log/moodot-clone/moodot-worker.log
StandardError=append:/var/log/moodot-clone/moodot-worker.log
```

**logrotate 설정**

로그 파일이 무한정 쌓이지 않도록 logrotate로 관리:

```
# /etc/logrotate.d/moodot-worker
/var/log/moodot-clone/moodot-worker.log {
    size 50M       # 50MB 초과 시 즉시 로테이션
    daily          # 매일 로테이션
    rotate 7       # 7일치 보관
    compress       # 오래된 파일 gzip 압축
    missingok
    copytruncate   # 파일 복사 후 원본 비움 (서비스 재시작 불필요)
}
```

`copytruncate`를 쓰는 이유: 파일을 이동시키지 않고 내용만 비우기 때문에 systemd가 파일 디스크립터를 유지한 채로 계속 쓸 수 있다.

**알려진 한계:**
- copytruncate 복사↔비움 사이 window에서 쓰인 로그는 유실될 수 있음 (window가 매우 짧아 허용 가능한 수준)
- 로테이션 직후 CloudWatch Agent가 파일을 처음부터 다시 읽어 중복 로그가 올라갈 수 있음
- Agent가 꺼진 사이에 로테이션이 발생하면 일부 로그 누락 가능

**CloudWatch Agent 설정 파일**

```json
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/moodot-clone/moodot-worker.log",
            "log_group_name": "/moodot/worker",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
```

**CloudWatch Logs 보존 기간 설정**

CloudWatch Logs 기본값은 영구 보존이라 비용이 쌓인다. 로그 그룹 생성 후 보존 기간을 설정해야 한다.

CloudWatch → 로그 그룹 → `/moodot/worker` → 보존 기간 설정

### 4. 메트릭 수집 설정

CloudWatch Agent 설정 파일에 `metrics` 섹션 추가:

```json
{
  "metrics": {
    "metrics_collected": {
      "cpu": {
        "measurement": ["cpu_usage_idle", "cpu_usage_user", "cpu_usage_system"]
      },
      "mem": {
        "measurement": ["mem_used_percent"]
      },
      "disk": {
        "measurement": ["disk_used_percent"],
        "resources": ["/"]
      }
    }
  }
}
```

`metrics_collection_interval` 생략 시 기본값 60초 적용.

수집된 지표는 CloudWatch → 지표 → **CWAgent** 네임스페이스에서 확인 가능.
- CPU: `host` 차원
- 메모리/디스크: `host`, `device`, `path`, `fstype` 차원

### 5. CPU Alarm 설정

나중에 진행 예정. CloudWatch → 경보 → 경보 생성 → CPUUtilization 80% 이상 → SNS 이메일 알림.

## 결과

- CloudWatch Logs: `/moodot/worker` 로그 그룹에 워커 로그 정상 수집 확인
- CWAgent 네임스페이스에서 CPU / 메모리 / 디스크 메트릭 정상 수집 확인
- 로그 보존 기간 1개월 설정 완료
- logrotate 7일 보존 설정 완료
