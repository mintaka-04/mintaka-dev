---
title: "Windows 환경에서 pip install 실패 — C 컴파일러 미설치 문제"
date: 2026-05-14
draft: true
categories: ["infra"]
tags: ["python", "windows", "pip", "compiler", "docker", "환경설정"]
---

## 🧩 이슈 개요

- Windows 환경에서 Python 프로젝트 실행 시 dependency 설치 과정에서 에러 발생

## ⚠️ 문제 상황

```
Preparing metadata (pyproject.toml) ... error
error: subprocess-exited-with-error

× Preparing metadata (pyproject.toml) did not run successfully.

WARNING: Failed to activate VS environment: Could not find C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe

..\meson.build:1:0: ERROR: Unknown compiler(s): [['icl'], ['cl'], ['cc'], ['gcc'], ['clang'], ['clang-cl'], ['pgcc']]
```

`pip install -r requirements.txt` 실행 중 C 컴파일러를 찾을 수 없다는 에러가 발생했다.

## 🔍 원인 분석

일부 Python 패키지(`cryptography`, `tiktoken` 등)는 사전 빌드된 wheel 파일이 없는 경우 로컬에서 직접 소스를 컴파일한다.

이때 C/C++ 컴파일러가 필요한데, OS별 기본 탑재 여부가 다르다.

| OS | 기본 컴파일러 | 비고 |
|----|--------------|------|
| macOS | `clang` | Xcode Command Line Tools에 포함 |
| Linux | `gcc` | 대부분 기본 설치됨 |
| Windows | 없음 | MSVC 또는 MinGW 별도 설치 필요 |

Windows는 공식 컴파일러인 MSVC(`cl.exe`)를 쓰려면 Visual Studio를 따로 설치해야 하고, `gcc`/`clang`도 마찬가지로 별도 설치가 필요하다.

## 🛠 해결 방법

**빠른 시도 — 바이너리 휠 우선 사용**

```bash
pip install --prefer-binary -r requirements.txt
```

빌드 없이 사전 컴파일된 wheel을 우선 사용하도록 강제한다. wheel이 제공되는 패키지라면 이걸로 해결된다.

**근본 해결 — Visual Studio Build Tools 설치**

1. [https://visualstudio.microsoft.com/visual-cpp-build-tools/](https://visualstudio.microsoft.com/visual-cpp-build-tools/) 접속
2. **"C++ 빌드 도구"** 워크로드 체크 후 설치
3. 터미널 재시작 후 `pip install -r requirements.txt` 재시도

## 🚧 한계 / 아쉬운 점

- OS마다 별도 환경 세팅이 필요하다
- Mac에서 잘 되던 게 Windows에서 안 되는 상황이 반복된다
- EC2(Linux)에서도 Python 버전이나 시스템 패키지 차이로 같은 문제가 재현될 수 있다
- 팀원마다 환경이 달라 "내 컴퓨터에서는 돼요" 문제가 생긴다

## 💡 개선 방향

Docker를 활용한 개발 환경 통일

```
Dockerfile 하나로 OS 차이 제거
→ 로컬(Mac/Windows) 어디서든 동일한 환경
→ EC2에서도 동일한 이미지로 실행
```

Docker 개념 정리:
- **Dockerfile**: 환경 설계도
- **이미지**: 설계도로 만든 실행 가능한 패키지
- **컨테이너**: 이미지를 실제로 실행한 것

EC2 위에서 Docker를 돌리는 구조로, EC2를 없애는 게 아니라 EC2 위에 컨테이너를 띄우는 방식이다.

AWS에서 컨테이너 관련 서비스로는 ECR(이미지 저장소), ECS, Fargate 등이 있지만 지금 규모에서는 EC2 + Docker 직접 운영이 가장 단순하다.

## 📚 배운 점

로컬 환경이 아니라 **"환경 자체"를 관리해야 한다.**

코드가 같아도 실행 환경이 다르면 결과가 달라진다. 도커는 코드와 실행 환경을 함께 패키징해서 이 문제를 근본적으로 해결한다.
