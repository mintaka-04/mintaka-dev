---
title: "GitHub Actions CD 파이프라인이 성공으로 뜨는데 EC2에 배포가 안 되는 문제"
date: 2026-05-06
draft: true
categories: ["troubleshooting"]
tags: ["github-actions", "aws", "ssm", "ec2", "cd"]
---

## 문제 상황

GitHub Actions CD 파이프라인이 항상 성공(초록불)으로 표시되는데, EC2에 실제로 코드가 반영되지 않았다. `git log`로 확인해보면 최신 커밋이 없는 상태.

---

## 원인 1: `send-command`는 전달만 하고 끝난다

`aws ssm send-command`는 EC2에 명령을 **전달**하는 것만 하고 바로 종료된다. EC2에서 실제로 실행됐는지, 성공했는지 기다리지 않는다.

```yaml
# 기존 코드 — EC2 실행 완료를 기다리지 않음
aws ssm send-command \
  --instance-ids "${{ secrets.EC2_INSTANCE_ID }}" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=[...]' \
  --output text
```

GitHub Actions 입장에서는 SSM에 명령을 성공적으로 전달했으니 job이 성공으로 끝난다. EC2에서 `git pull`, `pip install`, `systemctl restart`가 실제로 실행됐는지는 확인하지 않는다.

### 해결

Command ID를 캡처하고, `wait command-executed`로 완료를 기다린 뒤 결과를 확인한다.

```yaml
COMMAND_ID=$(aws ssm send-command \
  ... \
  --query "Command.CommandId" \
  --output text)

aws ssm wait command-executed \
  --command-id "$COMMAND_ID" \
  --instance-id "${{ secrets.EC2_INSTANCE_ID }}" || true

STATUS=$(aws ssm get-command-invocation \
  --command-id "$COMMAND_ID" \
  --instance-id "${{ secrets.EC2_INSTANCE_ID }}" \
  --query "Status" --output text)

if [ "$STATUS" != "Success" ]; then exit 1; fi
```

`|| true`를 붙이는 이유: `wait`가 Failed 상태를 감지하면 즉시 exit해서 그 아래 output/error 조회 코드가 실행되지 않는다. `|| true`로 wait 결과와 무관하게 항상 로그를 출력하도록 처리한다.

---

## 원인 2: SSM은 root로 실행되는데 디렉토리 소유자는 ubuntu

`wait`를 추가하고 나서 실제 실패 원인이 드러났다.

```
fatal: detected dubious ownership in repository at '/home/ubuntu/moodot_clone'
To add an exception for this directory, call:
    git config --global --add safe.directory /home/ubuntu/moodot_clone
```

SSM `AWS-RunShellScript`는 기본적으로 `root`로 명령을 실행한다. 그런데 `/home/ubuntu/moodot_clone`은 `ubuntu` 유저 소유라서, root가 git을 실행하면 소유자 불일치로 거부된다.

### 해결

`sudo -u ubuntu bash -c`로 git pull, pip install을 ubuntu 유저로 실행한다. `systemctl`은 sudo 권한이 필요하므로 ubuntu 블록 밖에서 root로 실행한다.

```yaml
--parameters 'commands=["sudo -u ubuntu bash -c \"cd /home/ubuntu/moodot_clone && git pull origin develop && service/venv/bin/pip install -r service/requirements.txt -q\" && sudo systemctl restart moodot-worker && sudo systemctl status moodot-worker --no-pager"]'
```

`source service/venv/bin/activate` 방식도 SSM 환경에서 신뢰성이 떨어지므로 `service/venv/bin/pip`으로 직접 경로를 지정한다.

---

## 최종 워크플로우

```yaml
- name: Deploy to EC2 via SSM
  run: |
    COMMAND_ID=$(aws ssm send-command \
      --instance-ids "${{ secrets.EC2_INSTANCE_ID }}" \
      --document-name "AWS-RunShellScript" \
      --parameters 'commands=["sudo -u ubuntu bash -c \"cd /home/ubuntu/moodot_clone && git pull origin develop && service/venv/bin/pip install -r service/requirements.txt -q\" && sudo systemctl restart moodot-worker && sudo systemctl status moodot-worker --no-pager"]' \
      --query "Command.CommandId" \
      --output text)

    aws ssm wait command-executed \
      --command-id "$COMMAND_ID" \
      --instance-id "${{ secrets.EC2_INSTANCE_ID }}" || true

    STATUS=$(aws ssm get-command-invocation \
      --command-id "$COMMAND_ID" \
      --instance-id "${{ secrets.EC2_INSTANCE_ID }}" \
      --query "Status" --output text)

    OUTPUT=$(aws ssm get-command-invocation \
      --command-id "$COMMAND_ID" \
      --instance-id "${{ secrets.EC2_INSTANCE_ID }}" \
      --query "StandardOutputContent" --output text)

    ERROR=$(aws ssm get-command-invocation \
      --command-id "$COMMAND_ID" \
      --instance-id "${{ secrets.EC2_INSTANCE_ID }}" \
      --query "StandardErrorContent" --output text)

    echo "=== Status: $STATUS ==="
    echo "=== Output ===" && echo "$OUTPUT"
    if [ -n "$ERROR" ]; then echo "=== Error ===" && echo "$ERROR"; fi

    if [ "$STATUS" != "Success" ]; then exit 1; fi
```

## 교훈

- `aws ssm send-command`는 fire-and-forget이다. 배포 성공 여부를 보장하려면 반드시 `wait` + `get-command-invocation`으로 결과를 확인해야 한다.
- SSM은 root로 실행된다. 특정 유저 소유 디렉토리에서 작업할 때는 `sudo -u <user>`로 명시적으로 유저를 지정해야 한다.
- `wait`에 `|| true`를 붙이지 않으면 실패 시 로그 조회 코드가 실행되지 않아 원인 파악이 어렵다.
