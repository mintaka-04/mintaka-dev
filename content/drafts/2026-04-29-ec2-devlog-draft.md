---
title: "Python 워커 EC2 배포 세팅"
date: 2026-05-02
draft: true
categories: ["devlog"]
tags: ["ec2", "python", "aws", "deploy"]
---

## 개요

- ec2 = 가상 서버 서비스
- ec2 인스턴스 = (
    AMI(Amazon Machine IMAGE) : 서버 이미지 - 운영체제, 초기 설정, 기본 설치 소프트웨어 정보 포함
    , Instance type : vcpu 개수, 메모리 크기, 네트워크 성능
    , Network : vpc, subnet, ip
    , sTORAGE : Amazon Elastic Block store 사용
    , Security : 허용한 것만)

## EC2를 선택한 이유

**일반적인 EC2 선택 이유**
- 운영체제 수준의 제어가 필요한 경우 (systemd로 프로세스 직접 관리 등)
- 기존 서버 환경을 그대로 이전해야 하는 경우
- 네트워크/보안 설정을 세밀하게 제어해야 하는 경우

**내 경우**
- 기존에 Render 무료 플랜으로 운영 중이었는데, 일정 시간 요청이 없으면 인스턴스가 슬립 상태로 전환됨
- Python 워커는 Supabase Realtime을 상시 구독해야 해서 슬립되면 이벤트를 놓침
- 그때마다 수동으로 깨워줘야 하는 문제가 있었음
- 24시간 상시 실행이 보장되고 비용이 저렴한 EC2 t3.micro로 이전 결정

**대안으로 고려했던 것들**
- **Fly.io** — 컨테이너 기반, 무료 티어에서도 슬립 없음, 소규모 워커에 적합
- **Railway** — Render와 비슷하지만 유료 플랜이 저렴하고 슬립 없음
- **DigitalOcean Droplet** — EC2와 거의 동일한 개념, 인터페이스가 더 단순함
- **ECS Fargate** — 컨테이너 기반 AWS 서비스, 관리 편하지만 워커 하나에는 과함

## EC2 인스턴스 설정

| 구성요소 | 선택 | 이유 |
|---|---|---|
| AMI | Ubuntu 24.04 LTS | 아래 참고 |
| Instance type | t3.micro (vCPU 2, 메모리 1GB) | 워커 하나 돌리기 충분, 프리티어 대상 |
| Network | 기본 VPC, 퍼블릭 IP 자동 할당 ON | SSH 접속 및 Supabase 연결에 필요 |
| Storage | EBS 8GB | Python 코드 + 패키지 올리는 수준이라 충분 |
| Security | SSH(22) 내 IP만 허용, 나머지 인바운드 차단 | 워커는 Supabase에 아웃바운드로 연결하므로 인바운드 불필요 |

**아키텍처 선택: 64비트 x86 (AMD64)**
- x86 vs ARM: CPU 종류가 달라서 컴파일된 바이너리가 달라짐
- Python 자체는 문제없지만, C언어로 짜여진 Python 패키지는 아키텍처별로 따로 컴파일된 파일을 제공
- 이 프로젝트의 `cryptography`(감정 텍스트 AES-256-GCM 복호화에 사용), `tiktoken`(LangChain 의존성)이 해당
- ARM용 바이너리가 없으면 소스에서 직접 컴파일 시도하다 실패하는 경우 있음 → 호환성 확실한 x86 선택

**AMI 선택 기준**
- LTS = Long Term Support, 5년간 보안 패치/버그 수정 지원 (일반 버전은 9개월)
- Ubuntu Server vs Ubuntu Pro: Pro는 기업용 보안 인증/규정 준수 기능 포함 유료 버전, 개인 프로젝트엔 Server로 충분
- 26.04 vs 24.04: 26.04는 2026년 출시 직후라 아직 불안정할 수 있음. 24.04는 1년 이상 검증된 안정 버전 → **Ubuntu Server 24.04 LTS 선택**

**Amazon Linux 대신 Ubuntu를 선택한 이유**
- Amazon Linux는 AWS 전용 배포판이라 밖에서 거의 안 쓰임
- 명령어/패키지가 일반 Linux랑 미묘하게 달라서 구글링해도 답이 안 맞는 경우가 많음
- Ubuntu는 가장 널리 쓰이는 배포판이라 자료가 압도적으로 많고 Python 관련 문서도 거의 Ubuntu 기준
- 막혔을 때 찾기 쉬운 게 Ubuntu

**키 페어**
- SSH로 EC2에 접속할 때 쓰는 열쇠
- 비밀번호 대신 키 파일(.pem)로 접속하는 방식
- AWS가 자물쇠(공개키)는 EC2에 설치하고, 열쇠(개인키 .pem)는 나한테 줌
- 한 번 발급하면 다시 못 받으니까 잘 보관해야 함

**SSH란**
- 인터넷 어딘가에 있는 EC2 서버를 내 터미널에서 원격으로 조종하는 방법
- 접속하면 EC2 터미널이 내 화면에 뜨고 마치 그 서버 앞에 앉아있는 것처럼 사용 가능
- `ssh -i 키파일.pem ubuntu@서버IP주소`

**SSH 말고 접속하는 방법**
- EC2 Instance Connect: 브라우저에서 바로 터미널 열어주는 기능, 키 파일 불필요
- Session Manager: AWS Systems Manager 통해 접속, 포트 22도 안 열어도 됨
- CD 구성 시 SSH 방식이 필요해서 키 페어 생성

**암호화 방식: ED25519 선택**
- RSA: 오래된 방식, 호환성 최고지만 키 길이 길고 파일 크기 큼
- ED25519: 최신 방식, 더 짧고 빠르고 보안도 좋음. SSH-2에서 지원
- Ubuntu 24.04는 SSH-2 기본이라 ED25519 사용 가능

**키 파일 형식: OpenSSH(.pem) 선택**
- PuTTY: Windows 전용 SSH 클라이언트 프로그램, 자체 키 형식(.ppk) 사용 (레거시)
- OpenSSH: Mac/Linux 기본 내장 SSH, Windows 10/11도 기본 내장
- WSL이나 PowerShell에서도 OpenSSH 그대로 사용 가능

**네트워크 설정**
- VPC/서브넷/가용영역: 기본값 사용 (인스턴스 생성 후 VPC 변경 불가, 여러 서버 분산 배치가 필요할 때 의미있음)
- 퍼블릭 IP 자동할당: **활성화** 필수
  - 비활성화는 DB 서버처럼 외부 노출 없이 내부 네트워크로만 통신할 때 사용
  - 퍼블릭 IP가 있으면 전 세계에서 SSH 접속 시도가 들어오므로 보안 그룹에서 내 IP만 허용하는 게 중요

**GitHub Actions CD 배포 방식**
- SSH 배포: GitHub Actions가 SSH로 EC2 접속 → git pull → 워커 재시작. 설정 간단하고 직관적
- SSM Session Manager: SSH 포트 없이 AWS 내부 통해서 배포, 보안 더 좋음
- CodeDeploy: AWS 배포 전용 서비스
- 지금은 SSH 방식으로 구성, 나중에 보안 강화 시 SSM으로 전환 고려

**보안 그룹**
- SSH(포트 22) 인바운드 규칙 기본 생성됨
- 소스를 `0.0.0.0/0`(전체) 대신 내 IP로 설정 — AWS 콘솔에서 "내 IP" 선택하면 자동 입력
- 내 IP = 인터넷에서 내 컴퓨터를 식별하는 주소. 유동 IP라 장소 바뀌면 바뀔 수 있음
- 다른 노트북에서도 접속하려면 그 IP도 인바운드 규칙에 추가 (`curl -4 ifconfig.me` 로 확인)
- GitHub Actions 배포 시 Actions IP도 허용 필요 (매번 바뀌어서 추후 고려)
- IP 입력 시 반드시 CIDR 형식으로 입력해야 함 → `IP주소/32` (`/32` = 이 IP 하나만 허용)
- 같은 와이파이 쓰는 기기들은 공인 IP가 동일 (공유기 기준이라서), 다른 장소에서만 IP가 달라짐

**스토리지**
- t3.micro 선택 시 기본 8GB EBS 자동 설정, 그대로 사용
- 추가 파일 시스템 옵션 (지금은 불필요):
  - S3: 이미지/동영상/로그 파일 저장용
  - EFS: 여러 EC2가 동시에 같은 파일에 접근해야 할 때 (공유 폴더 개념)
  - FSx: 고성능 필요한 기업용 특수 케이스

**고급 세부 정보**
- 지금은 건드릴 거 없음
- IAM 역할: SSM이나 CloudWatch 붙일 때 필요 (모니터링 설정 시 추후 설정)
- 사용자 데이터: 인스턴스 처음 시작할 때 자동 실행할 스크립트 등록 가능

## Python 환경 구성

```bash
# 패키지 업데이트
sudo apt update && sudo apt upgrade -y

# 코드 클론 (develop 브랜치)
git clone -b develop https://github.com/mintaka-04/moodot_clone.git
cd moodot_clone/service

# venv 생성 및 활성화
python3 -m venv venv
source venv/bin/activate

# 패키지 설치 (-r: requirements.txt 파일 읽어서 한꺼번에 설치)
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env.local
vim .env.local  # 실제 값 채워넣기

# 실행 확인
python3 main.py
```

## systemd 서비스 등록

systemd = Ubuntu 내장 프로세스 관리자. 여기에 등록하면:
- 부팅 시 자동 시작
- 워커가 죽으면 자동 재시작
- `systemctl status` 로 상태 확인
- `journalctl` 로 로그 확인

```bash
sudo vim /etc/systemd/system/moodot-worker.service
```

```ini
[Unit]
Description=Moodot AI Worker
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/moodot_clone/service
ExecStart=/home/ubuntu/moodot_clone/service/venv/bin/python3 main.py
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/moodot_clone/service/.env.local

[Install]
WantedBy=multi-user.target
```

- `After=network.target` — 네트워크 켜진 후 시작 (Supabase 연결 필요)
- `Restart=always` — 워커 죽으면 자동 재시작
- `RestartSec=5` — 재시작 전 5초 대기 (바로 재시작하면 같은 이유로 또 죽는 무한루프 방지)
- `EnvironmentFile` — .env.local 환경변수 파일 경로

```bash
# 등록 및 시작
sudo systemctl daemon-reload
sudo systemctl enable moodot-worker  # 부팅 시 자동 시작 등록
sudo systemctl start moodot-worker   # 지금 시작

# 상태 확인
sudo systemctl status moodot-worker  # active (running) 뜨면 성공
```

터미널 끊거나 SSH 연결 종료해도 백그라운드에서 계속 실행됨

**로그 확인 방법**
```bash
# 워커 상태 확인
sudo systemctl status moodot-worker

# 실시간 로그 스트리밍 (-f: follow, 실시간으로 계속 출력)
sudo journalctl -u moodot-worker -f

# 최근 로그 n줄만 보기
sudo journalctl -u moodot-worker -n 100
```


## CD 파이프라인 구성

### GitHub Actions용 SSH 키 생성

EC2에서 GitHub Actions 전용 SSH 키를 새로 생성:
```bash
ssh-keygen -t ed25519 -C "github-actions"
```
- 경로: 그냥 Enter (기본 `~/.ssh/id_ed25519` 에 저장)
- 비밀번호: 그냥 Enter (GitHub Actions는 자동 실행이라 비밀번호 입력할 사람이 없음)

생성되는 파일:
```
~/.ssh/id_ed25519      ← 개인키 (GitHub Secrets에 등록)
~/.ssh/id_ed25519.pub  ← 공개키 (EC2 authorized_keys에 등록)
```

**왜 아까 만든 .pem 키랑 별도로 만드나?**
- .pem 키: 내 컴퓨터 → EC2 접속용
- 지금 키: GitHub Actions → EC2 접속용
- 보안상 사람이 쓰는 키와 자동화 시스템이 쓰는 키를 분리하는 게 좋음. 나중에 GitHub Actions 키만 따로 폐기/교체 가능

### 공개키를 EC2에 등록

```bash
cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
```

`authorized_keys` = SSH 접속 허용 목록 파일 (폴더 아님)
- SSH 접속 시 EC2가 이 파일을 읽어서 "이 개인키에 맞는 공개키가 있나?" 확인
- 있으면 접속 허용, 없으면 거부
- AWS가 .pem 키 만들 때 자동으로 여기에 공개키를 넣어줬고, 지금은 GitHub Actions 키를 직접 추가하는 것

### 개인키를 GitHub Secrets에 등록

```bash
cat ~/.ssh/id_ed25519
```
출력된 내용 복사 후 `https://github.com/mintaka-04/moodot_clone/settings/secrets/actions` 에서 추가:
- `EC2_SSH_KEY` — 개인키 전체 (`-----BEGIN OPENSSH PRIVATE KEY-----` 부터 끝까지)
- `EC2_HOST` — EC2 퍼블릭 IP
- `EC2_USER` — `ubuntu`

### GitHub Actions CD 워크플로우

`.github/workflows/cd.yml` 생성:

```yaml
name: Deploy Worker to EC2

on:
  push:
    branches: ["develop"]
    paths:
      - "service/**"

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
      - name: Deploy to EC2
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.EC2_HOST }}
          username: ${{ secrets.EC2_USER }}
          key: ${{ secrets.EC2_SSH_KEY }}
          script: |
            cd /home/ubuntu/moodot_clone
            git pull origin develop
            source service/venv/bin/activate
            pip install -r service/requirements.txt
            sudo systemctl restart moodot-worker
            sudo systemctl status moodot-worker
```

**워크플로우 구조 설명**
- `on` — 트리거 조건 (방아쇠): develop 브랜치에 service/ 폴더 변경사항 push 시 실행
- `paths: service/**` — service 폴더 변경 없으면 배포 안 함
- `jobs` — 실행 내용 (총알)
- `runs-on: ubuntu-latest` — GitHub Actions 자체 서버 환경 (EC2 버전과 무관)
- `uses: appleboy/ssh-action@v1` — SSH 접속/실행을 대신 해주는 공개 액션 (npm 패키지 같은 개념)
- `script` — EC2 안에서 실행되는 명령어들

**실행 흐름**
```
GitHub Actions 서버 (ubuntu-latest)
  → appleboy/ssh-action이 SSH로 EC2 접속
    → EC2 안에서 script 실행
      → git pull → pip install → systemctl restart
```


**GitHub Actions 동작 방식**
- `.github/workflows/` 폴더를 GitHub이 특별하게 인식
- push 되는 순간 GitHub 서버가 트리거 조건 확인 후 자동 실행
- 로컬에서는 동작 안 함 (push 전까지 그냥 텍스트 파일)
- 로컬 테스트가 필요하면 `act` 도구 사용 가능 (설정 번거로워서 보통 그냥 push해서 확인)

**CI와 CD 실행 순서**
- 현재 CI(`ci.yml`)와 CD(`cd.yml`)는 별도 워크플로우라 동시에 실행됨
- CI 통과 후 CD 실행하려면 `needs` 설정 필요한데 별도 파일 간 연결은 복잡함
- 지금 규모에서는 동시 실행으로 유지

## 이슈 및 해결


