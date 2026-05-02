---
title: "GitHub Actions CD SSH 타임아웃 → SSM Session Manager 전환"
date: 2026-05-02
draft: true
categories: ["devlog"]
tags: ["github-actions", "ec2", "ssh", "ssm", "cd"]
---

## 문제 상황

GitHub Actions CD 파이프라인 실행 시 아래 오류 발생:

```
2026/05/02 06:13:19 dial tcp ***:22: i/o timeout
Error: Process completed with exit code 1.
```

## 원인

EC2 보안 그룹에서 SSH 포트(22)를 내 IP만 허용하도록 설정했는데, GitHub Actions 서버 IP는 다른 IP라서 접속이 차단됨.

- `dial tcp ***:22` — EC2 포트 22(SSH)에 연결 시도
- `i/o timeout` — 응답 없이 시간 초과 = 요청 자체가 막힌 것

SSH 키 오류나 서버 다운이면 다른 에러가 나오지만, timeout은 보안 그룹에서 차단된 경우

## 해결 방법 검토

**방법 1: 포트 22를 0.0.0.0/0으로 열기**
- 간단하지만 전 세계에서 SSH 접속 시도 가능 → 보안 취약

**방법 2: SSM Session Manager로 전환** ← 선택
- SSH 포트 자체를 안 씀
- GitHub Actions → AWS API(HTTPS 443) → SSM → EC2 내부
- 포트 22 완전히 닫아도 됨 → 보안 강함
- 단, IAM 설정 필요

## SSM이란

**AWS Systems Manager(SSM)** — AWS가 제공하는 서버 관리 서비스. 그 안에 여러 기능 중 하나가 **Session Manager**

```
AWS Systems Manager (SSM)
  ├── Session Manager  ← 포트 없이 EC2 접속
  ├── Parameter Store  ← 환경변수 관리
  ├── Patch Manager    ← 서버 패치 자동화
  └── ...
```

**SSH vs SSM 연결 방식 차이**
```
SSH 방식:
나/GitHub Actions → (포트 22) → EC2   # 외부에서 EC2로 직접 연결

SSM 방식:
EC2 안 SSM Agent → AWS 서버 (먼저 연결해둠)
나/GitHub Actions → AWS API → SSM Agent → EC2   # AWS 내부 통해서 연결
```

- SSM Agent가 EC2 안에서 계속 실행되면서 AWS 서버와 연결 유지 (Python 워커가 Supabase 구독하고 있는 것과 비슷한 개념)
- EC2가 능동적으로 AWS에 연결해두는 구조라 외부에 포트를 열 필요 없음 → 보안 강함

## SSM 전환 작업

### 1. EC2용 IAM 역할 생성 (이미 완료)
- 역할: `AmazonSSMManagedInstanceCore` 정책 부착
- 용도: EC2 → AWS SSM 서버 연결 (SSM Agent가 사용)
- EC2 → 작업 → 보안 → IAM 역할 수정에서 EC2에 부착

### 2. GitHub Actions용 IAM 역할 생성
- 역할 이름: `moodot-github-actions-role`
- 신뢰할 수 있는 엔티티 유형: **사용자 지정 신뢰 정책**
- GitHub Actions는 AWS 외부 서비스라 기본 옵션에 없어서 직접 작성

**신뢰 정책 JSON:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::계정ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:mintaka-04/moodot_clone:ref:refs/heads/develop"
        }
      }
    }
  ]
}
```

**신뢰 정책 설명**
- `Federated` — 외부 신원 제공자를 신뢰한다. 뒤의 값이 "그 신원 제공자가 누구인지"
- `oidc-provider/token.actions.githubusercontent.com` — 내 AWS 계정에 등록된 GitHub OIDC 제공자
- `AssumeRoleWithWebIdentity` — 외부(Web)에서 발급한 신원(Identity)으로 역할(Role)을 맡는(Assume) 행위
- `sub` 조건 — mintaka-04/moodot_clone 레포의 develop 브랜치에서만 허용
- `aud` 조건 — "이 토큰이 누구를 위해 발급된 건지" (sts.amazonaws.com = AWS용 토큰만 허용, 다른 서비스에 토큰 오용 방지)

**전체 흐름:**
```
GitHub Actions가 토큰 발급 (나 mintaka-04/moodot_clone의 GitHub Actions야)
  → AWS가 확인 (GitHub OIDC 제공자가 보증한 거 맞네)
  → 역할 허용
  → SSM으로 EC2에 명령어 전송
```

**역할이 두 개인 이유:**
- EC2용 역할: EC2 → AWS SSM 연결 (SSM Agent가 사용)
- GitHub Actions용 역할: GitHub Actions → AWS API 호출 (SSM 명령어 전송)
```
GitHub Actions → (GitHub Actions용 역할로 인증) → AWS API → SSM → EC2 (EC2용 역할로 연결 유지)
```

### 3. GitHub OIDC 제공자 등록

**IAM → ID 제공업체 → 공급자 추가**
- 공급자 유형: OpenID Connect
- 공급자 URL: `https://token.actions.githubusercontent.com`
- 대상: `sts.amazonaws.com`

### 4. GitHub Actions용 역할에 SSM 권한 추가

**IAM → 역할 → `moodot-github-actions-role` → 권한 추가 → `AmazonSSMFullAccess`**

- 신뢰 정책: "GitHub Actions가 이 역할을 쓸 수 있다" (문 열어주는 것)
- AmazonSSMFullAccess: "이 역할로 SSM 명령어를 보낼 수 있다" (문 열고 들어와서 할 수 있는 것)

### 5. GitHub Secrets 등록

- `AWS_ROLE_ARN` — GitHub Actions용 IAM 역할 ARN (`moodot-github-actions-role`)
- `EC2_INSTANCE_ID` — EC2 인스턴스 ID (`i-xxxxxxxxxxxxxxxxx`)

### 6. cd.yml SSM 방식으로 수정

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      id-token: write    # GitHub Actions가 AWS 신원 증명 토큰 발급받을 수 있게 허용
      contents: read     # 레포 코드 읽기 허용

    steps:
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
          aws-region: ap-northeast-2

      - name: Deploy to EC2 via SSM
        run: |
          aws ssm send-command \
            --instance-ids "${{ secrets.EC2_INSTANCE_ID }}" \
            --document-name "AWS-RunShellScript" \
            --parameters 'commands=[...]' \
            --output text
```

steps가 2개인 이유:
- SSH는 `appleboy/ssh-action` 하나가 접속+실행을 한 번에 했지만
- SSM은 AWS API를 거쳐야 해서 AWS 인증 단계가 추가됨

**주의: cd.yml 수정 후 반드시 커밋/푸시해야 적용됨** (안 하면 구버전으로 실행됨)

