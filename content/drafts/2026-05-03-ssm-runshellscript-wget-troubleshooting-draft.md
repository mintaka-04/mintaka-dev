---
title: "SSM Run Command에서 wget 명령어가 깨지는 문제"
date: 2026-05-03
draft: true
categories: ["troubleshooting"]
tags: ["ssm", "aws", "cloudwatch"]
---

## 문제 상황

SSM Run Command(`AWS-RunShellScript`)로 CloudWatch Agent를 설치하려고 아래 명령어를 입력했다.

```bash
wget https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb -O /tmp/cw-agent.deb
sudo dpkg -i /tmp/cw-agent.deb
```

실행 결과 실패:

```
wget: option requires an argument -- 'O'
wget: missing URL
Usage: wget [OPTION]... [URL]...
```

## 원인

SSM 콘솔 입력창에서 긴 명령어를 붙여넣을 때 줄바꿈이 깨지면서 wget이 URL과 `-O` 옵션을 제대로 인식하지 못했다.

## 해결

CloudWatch Agent처럼 AWS가 배포하는 소프트웨어는 `AWS-RunShellScript` 대신 **`AWS-ConfigureAWSPackage`** 문서를 사용한다. wget/dpkg를 직접 다루지 않아도 AWS가 내부적으로 처리해준다.

**SSM 문서 선택 기준:**

| 문서 | 용도 |
|---|---|
| `AWS-RunShellScript` | 범용 쉘 명령 실행 |
| `AWS-ConfigureAWSPackage` | AWS 패키지 설치 전용 ✅ |
| `AWS-ConfigureCloudWatch` | 구버전 awslogs 에이전트 (레거시) |

**설치 설정:**
- 문서: `AWS-ConfigureAWSPackage`
- Action: `Install`
- Name: `AmazonCloudWatchAgent`
- Version: 비워두기 (최신)
