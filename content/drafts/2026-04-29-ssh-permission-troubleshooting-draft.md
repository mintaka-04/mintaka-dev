---
title: "WSL에서 SSH 키 파일 권한 오류 (WARNING: UNPROTECTED PRIVATE KEY FILE)"
date: 2026-04-29
draft: false
categories: ["troubleshooting"]
tags: ["ssh", "wsl", "ec2", "aws", "permissions"]
---

## 문제 상황

WSL2(Ubuntu)에서 EC2에 SSH 접속 시도 시 아래 오류 발생:

```
WARNING: UNPROTECTED PRIVATE KEY FILE!
Permissions for 'keyfile.pem' are too open.
```

## 원인

AWS가 키 파일을 생성해서 Windows에 다운로드해줬는데, Windows 파일 권한이 그대로 붙어있음.
Windows는 파일 권한 개념이 Linux랑 달라서 "모두 읽기 가능" 상태로 저장됨.
SSH는 보안상 키 파일이 **나만 읽을 수 있는 상태**여야 접속을 허용하기 때문에 거부함.

## 해결 방법

WSL에서 키 파일 권한을 변경:

```bash
chmod 400 /mnt/c/Users/이름/Downloads/키파일.pem
```

`chmod 400` = 나만 읽기 가능으로 권한 변경. 이후 정상 접속됨.

## 추가 문제

`chmod 400` 해도 여전히 오류 발생 (0777 → 0555로 바뀌었지만 여전히 too open):

```
Permissions 0555 for 'keyfile.pem' are too open.
```

## 원인

`/mnt/c/` 는 Windows 파일 시스템이라 WSL에서 `chmod`가 제대로 적용되지 않음.

## 최종 해결 방법

키 파일을 WSL 파일 시스템으로 복사 후 권한 변경:

```bash
cp /mnt/c/Users/이름/Downloads/키파일.pem ~/keys/키파일.pem
chmod 400 ~/keys/키파일.pem
ssh -i ~/keys/키파일.pem ubuntu@서버IP
```

## 참고

- Windows 경로 `C:\Users\이름\Downloads\` → WSL에서는 `/mnt/c/Users/이름/Downloads/`
- `/mnt/c/` 경로는 Windows 파일 시스템이라 Linux 권한 명령어가 제대로 동작하지 않음
- WSL 홈 디렉토리(`~/`)로 복사하면 정상 동작
- 현재 위치로 복사할 때는 `cp 파일경로 .`
