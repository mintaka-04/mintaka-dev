# Ubuntu 24.04 LTS에서 SSM Session Manager에 인스턴스가 안 보이는 문제

## 문제

EC2 인스턴스를 생성하고 IAM 역할(`AmazonSSMManagedInstanceCore`)도 붙였는데, AWS Systems Manager → Session Manager에서 인스턴스가 목록에 나타나지 않음

## 원인

Ubuntu 24.04 LTS AMI에는 SSM Agent가 기본 설치되어 있지 않음. Amazon Linux와 달리 Ubuntu는 수동으로 설치해야 함

## 조치

인스턴스 생성 시 User data에 아래 스크립트 추가:

```bash
#!/bin/bash
snap install amazon-ssm-agent --classic
systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service
systemctl start snap.amazon-ssm-agent.amazon-ssm-agent.service
```

이후 인스턴스 시작 2~3분 뒤 Session Manager에서 정상적으로 인스턴스가 조회됨
