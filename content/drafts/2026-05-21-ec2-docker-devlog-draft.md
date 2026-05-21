---
title: "EC2에 Docker로 Python AI 워커 올리기"
date: 2026-05-21
draft: true
categories: ["devlog"]
tags: ["docker", "ec2", "python", "aws"]
---

## 배경

Windows 환경에서 `pip install -r requirements.txt` 실행 시 C 컴파일러 미설치로 빌드 실패가 발생했다. Mac은 Xcode CLT에 clang이 기본 포함되어 있어 문제없지만, Windows는 별도 설치가 필요하다. OS마다 환경 세팅이 달라지는 문제를 근본적으로 해결하기 위해 Docker를 도입한다.

## Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

### Python 버전 선택: 3.12

CI(`ci.yml`)가 `python-version: "3.12"`를 사용하고 있어 맞췄다. requirements.txt 주석에 `Render(Python 3.14)와 호환 안 됨`이라고 적혀 있어서 3.14를 쓰려 했으나, CI 기준이 3.12였고 Render는 더 이상 사용하지 않으므로 3.12로 결정.

### COPY 순서

```dockerfile
COPY requirements.txt .   # 1. 의존성 파일만 먼저
RUN pip install ...       # 2. 설치 (레이어 캐시 활용)
COPY . .                  # 3. 나머지 코드 전체
```

requirements.txt를 코드보다 먼저 복사하는 이유는 Docker 레이어 캐시 때문이다. 코드만 바뀌었을 때 pip install을 다시 실행하지 않도록 분리한다.

### 멀티스테이지 빌드는 사용하지 않음

멀티스테이지 빌드의 목적은 build-essential 등 컴파일 도구를 최종 이미지에서 제거해 이미지 크기를 줄이는 것이다. 그러나 현재 requirements.txt의 패키지들(`supabase`, `langchain`, `openai`, `httpx` 등)은 Linux(amd64) 환경에서 대부분 pre-built wheel로 제공된다. 즉 컴파일 자체가 일어나지 않아 build-essential이 필요없고, 멀티스테이지를 써도 이미지 크기 차이가 거의 없다.

Windows에서 빌드 실패가 발생한 이유도 Linux wheel이 없어서가 아니라 Windows에 컴파일러가 없어서였다. Linux 환경에서는 같은 문제가 재현되지 않는다.

> 단, 향후 컴파일이 필요한 패키지가 추가되어 `pip install` 중 에러가 발생하면 그때 멀티스테이지로 전환한다.

## .dockerignore

```
venv/
.env*
__pycache__/
*.pyc
*.pyo
tests/
demo_setup.py
test.txt
README.md
```

`venv/`는 로컬 가상환경이라 컨테이너 안에 들어가면 안 된다. `.env*`는 패턴으로 막아 `.env.local`, `.env.production` 등 모든 환경변수 파일을 제외한다.

## 로컬 빌드 확인

EC2에 올리기 전에 로컬에서 이미지가 정상적으로 만들어지는지 먼저 검증했다.

```bash
cd moodot_clone/service
docker build -t moodot-worker .
```

`build-essential` 없이도 모든 패키지 정상 설치 확인. 멀티스테이지 불필요함이 재확인됐다.

## ECR 레포지토리 생성

GitHub Actions에서 이미지를 빌드해 ECR에 push하고, EC2가 pull하는 구조로 결정했다. ECR은 AWS 계정당 레지스트리가 하나이고 그 아래 레포지토리가 여러 개 존재하는 구조다.

```bash
aws ecr create-repository \
  --repository-name moodot-worker \
  --region ap-northeast-2 \
  --image-tag-mutability MUTABLE
```

`latest` 태그를 매 배포마다 덮어써야 하므로 MUTABLE로 설정했다. Private 레포지토리로 생성.

## 환경변수 처리

`.env.local`은 이미지에 포함하지 않는다. EC2에 파일이 존재하고, 컨테이너 실행 시 `--env-file`로 넘긴다.

```bash
docker run --env-file /home/ubuntu/moodot_clone/service/.env.local moodot-worker
```

`main.py`가 `load_dotenv('.env.local')`로 파일을 읽으려 하지만, `--env-file`로 넘긴 값들은 이미 환경변수로 등록되어 있어 `os.getenv()`로 정상적으로 읽힌다.

## CD 파이프라인 수정 (cd.yml)

기존 흐름: `git pull → pip install → systemctl restart`

변경된 흐름:
```
push to develop
      ↓
GitHub Actions
  ① docker build → ECR push (SHA 태그 + latest)
  ② SSM으로 EC2에 명령
        → ECR 로그인 → docker pull → docker rm -f → docker run
```

이미지 태그는 SHA + latest 두 개를 붙인다. EC2는 항상 `latest`를 pull하고, 롤백이 필요하면 SHA 태그로 특정 버전을 지정할 수 있다.

`docker rm -f moodot-worker 2>/dev/null`은 기존 컨테이너가 있으면 멈추고 삭제, 없으면 무시하고 넘어간다.

## IAM 권한 추가

| 역할 | 추가 정책 |
|------|-----------|
| GitHub Actions IAM 역할 | `AmazonEC2ContainerRegistryPowerUser` (ECR push) |
| EC2 IAM 역할 | `AmazonEC2ContainerRegistryReadOnly` (ECR pull) |

## EC2 Docker 설치

```bash
sudo apt-get update
sudo apt-get install -y docker.io
sudo usermod -aG docker ubuntu
```

`-y`는 설치 중 확인 프롬프트에 자동으로 yes 응답하는 옵션이다. `usermod` 후 재로그인하거나 `newgrp docker` 실행해야 권한이 적용된다.

## 기존 systemctl 서비스 중단

Docker 컨테이너와 systemctl 워커가 동시에 실행되면 Supabase realtime 이벤트를 두 프로세스가 중복 처리하게 된다. 기존 서비스를 완전히 비활성화한다.

```bash
sudo systemctl stop moodot-worker
sudo systemctl disable moodot-worker
```

`disable`까지 해야 EC2 재부팅 시 자동으로 다시 올라오지 않는다.

## 트러블슈팅

### ECR 레포지토리 이름 불일치

첫 CD 실행 시 `name unknown: the repository with name 'moodot-worker' does not exist` 에러가 발생했다. cd.yml에 `moodot-worker`로 하드코딩했는데 실제 ECR 레포지토리 이름은 `moodotclone/moodotclone`이었다. cd.yml의 이미지 이름을 수정해 해결했다.

### CD 트리거 경로 문제

cd.yml 수정 후 push했는데 Deploy 잡이 트리거되지 않았다. cd.yml의 경로 필터가 `service/**`인데, `.github/workflows/cd.yml` 변경은 이 범위 밖이라 감지가 안 됐다. `service/test.txt`를 수정해 CD를 강제 트리거했다.

### EC2에 AWS CLI 미설치

EC2 SSM 명령에서 `aws ecr get-login-password`가 실패하며 `pull access denied` 에러가 발생했다. Ubuntu 24.04에 AWS CLI가 기본 설치되어 있지 않았고, `apt-get install awscli`도 패키지를 찾지 못했다. `/tmp`에서 직접 다운로드해 설치했다.

```bash
cd /tmp
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
sudo apt-get install -y unzip
unzip awscliv2.zip
sudo ./aws/install
```

`curl`을 `/tmp` 외 디렉토리에서 실행하면 permission denied가 발생한다. `/tmp`는 모든 유저가 쓰기 가능한 디렉토리라 sudo 없이 파일을 생성할 수 있다.

## 배포 확인

```bash
docker ps                    # 컨테이너 실행 중인지 확인
docker logs moodot-worker    # 로그 확인
```

`✅ Realtime 구독 시작!` 로그까지 나오면 정상 동작 중이다.

## CloudWatch 로그 연동

기존엔 CloudWatch agent가 journald에서 로그를 수집했다. Docker는 `awslogs` 드라이버를 사용해 CloudWatch agent 없이 Docker가 직접 CloudWatch로 로그를 전송한다.

```bash
docker run -d \
  --name moodot-worker \
  --env-file /home/ubuntu/moodot_clone/service/.env.local \
  --restart always \
  --log-driver awslogs \
  --log-opt awslogs-region=ap-northeast-2 \
  --log-opt awslogs-group=/moodot-clone/worker \
  --log-opt awslogs-stream=worker \
  $IMAGE
```

EC2 IAM 역할에 CloudWatch Logs 권한이 있어야 한다. 권한 확인:

```bash
aws logs describe-log-groups --region ap-northeast-2
```

에러 없이 목록이 나오면 권한이 있는 것이다.

배포 후 CloudWatch 콘솔에서 `/moodot-clone/worker` 로그 그룹으로 로그가 들어오는지 확인한다.

## 디스크 정리

Docker 도입 후 디스크 사용량이 78.7%까지 올라갔다. `docker system df`로 확인하니 Docker 이미지가 1.2GB, 나머지는 Ubuntu OS, venv, apt 캐시 등이었다.

apt 캐시와 AWS CLI 설치 파일을 정리하니 7% 확보됐다.

```bash
sudo apt-get clean
rm -rf /tmp/aws /tmp/awscliv2.zip
```

Docker는 EC2에서 항상 `latest` 하나만 pull하는 구조라 이미지가 누적되지 않는다. ECR에는 SHA 태그로 버전이 쌓이지만 EC2 디스크엔 영향 없다.

## CloudWatch 메모리 지표

CloudWatch 기본(`AWS/EC2` 네임스페이스)에는 메모리 지표가 없다. CloudWatch agent가 수집한 지표는 `CWAgent` 네임스페이스에서 확인해야 한다.

- CloudWatch → Metrics → All metrics → `CWAgent` → host → `mem_used_percent`

agent 설정(`amazon-cloudwatch-agent.toml`)에 `mem`이 포함되어 있으면 별도 작업 없이 수집된다.
