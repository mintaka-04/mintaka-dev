# vibe_githubpage

Hugo + PaperMod 기반 개발 블로그. GitHub Pages로 배포.
URL: https://mintaka-04.github.io/mintaka-dev/

## 디렉토리 구조

```
content/
  drafts/          # 초안 보관 폴더 (검토 전)
  devlog/          # 개발 일지 (게시됨)
  troubleshooting/ # 트러블슈팅 (게시됨)
  til/             # Today I Learned (게시됨)
docs/
  roadmap.md       # 프로젝트 방향성 문서 (블로그 미게시, 내부 참고용)
scripts/
  generate_post.py # 커밋 기반 초안 자동 생성 스크립트
```

## 글 작성 워크플로우

1. `content/drafts/`에 초안 생성 (`draft: true`)
2. 작성자가 로컬에서 검토 및 수정
3. 검토 완료 후 해당 카테고리 폴더로 이동 + `draft: false`로 변경
4. `main` 브랜치에 push → GitHub Actions가 Hugo 빌드 후 GitHub Pages 배포

**중요 — Hugo draft 필드 의미:**
- `draft: true` → 게시 안 됨 (초안)
- `draft: false` → 게시됨

새 초안은 반드시 `draft: true`로 생성할 것.

## 초안 파일명 규칙

```
YYYY-MM-DD-{slug}-draft.md
```

예: `2026-05-03-cloudwatch-monitoring-devlog-draft.md`

## Frontmatter 형식

```yaml
---
title: "제목"
date: YYYY-MM-DD
draft: true
categories: ["devlog"]   # devlog | troubleshooting | til 중 하나
tags: ["tag1", "tag2"]
---
```

## 카테고리별 글 성격

- **devlog** — 오늘 한 작업, 의사결정 과정, 구현 내용
- **troubleshooting** — 문제 상황 / 원인 분석 / 해결 방법 구조
- **til** — 오늘 할 일 / 한 일 / 배운 것 구조

## generate_post.py 사용법

```bash
# 오늘 커밋 기반으로 devlog + troubleshooting 초안 생성
python scripts/generate_post.py

# 특정 날짜
python scripts/generate_post.py --date 2026-05-03

# TIL 초안 생성 (할 일 목록 직접 입력)
python scripts/generate_post.py --til "할 일1" "할 일2"

# 다른 레포 대상
python scripts/generate_post.py --repo moodot
```

스크립트는 Claude API(claude-sonnet-4-6)를 사용하며 `GH_TOKEN`, `ANTHROPIC_API_KEY` 환경변수가 필요하다.

## 배포

`main` 브랜치 push 시 `.github/workflows/deploy.yml`이 자동 실행됨.
`develop` 브랜치에서 작업 후 main으로 머지하는 방식.
