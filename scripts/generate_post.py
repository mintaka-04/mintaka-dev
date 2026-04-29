#!/usr/bin/env python3
"""
커밋 기반 블로그 초안 생성기

사용법:
  python scripts/generate_post.py                        # 오늘 커밋 기반
  python scripts/generate_post.py --date 2026-04-28     # 특정 날짜
  python scripts/generate_post.py --repo moodot         # 다른 레포
  python scripts/generate_post.py --til "EC2에 배포하며 배운 것들"  # TIL 초안
"""

import os
import re
import sys
import argparse
import requests
import anthropic
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

GITHUB_USER = "mintaka-04"
KST = timezone(timedelta(hours=9))

gh_token = os.environ.get("GH_TOKEN", "")
headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"} if gh_token else {}
client = anthropic.Anthropic()


def fetch_commits(repo, date_str):
    since = f"{date_str}T00:00:00+09:00"
    until = f"{date_str}T23:59:59+09:00"
    url = f"https://api.github.com/repos/{GITHUB_USER}/{repo}/commits"
    res = requests.get(url, headers=headers, params={"since": since, "until": until, "per_page": 30})
    if not res.ok:
        print(f"커밋 조회 실패: {res.status_code} {res.text}")
        return []
    return res.json()


def fetch_commit_detail(repo, sha):
    url = f"https://api.github.com/repos/{GITHUB_USER}/{repo}/commits/{sha}"
    res = requests.get(url, headers=headers)
    return res.json() if res.ok else {}


def slugify(text):
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_]+", "-", text).strip("-")[:50]


def write_draft(filename, content):
    path = f"content/drafts/{filename}"
    os.makedirs("content/drafts", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"초안 생성됨: {path}")


def generate_devlog(commits, date_str):
    if not commits:
        print("커밋 없음 — 개발일지 스킵")
        return

    commit_summary = "\n".join(
        f"- {c['commit']['message'].splitlines()[0]}" for c in commits
    )

    print(f"개발일지 초안 생성 중... ({len(commits)}개 커밋)")
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=(
            "당신은 개발자의 GitHub 커밋을 보고 한국어 개발 블로그 초안을 작성합니다. "
            "Hugo Markdown 형식으로 아래 frontmatter를 포함해서 작성하세요:\n"
            "---\n"
            "title: \"제목\"\n"
            "date: 날짜\n"
            "draft: true\n"
            "categories: [\"devlog\"]\n"
            "tags: []\n"
            "---\n"
            "내용은 오늘 작업을 자연스러운 개발 일지 형식으로 작성하세요. "
            "이 글은 초안이므로 작성자가 나중에 직접 수정할 예정입니다."
        ),
        messages=[{"role": "user", "content": f"날짜: {date_str}\n\n커밋 목록:\n{commit_summary}"}],
    )

    write_draft(f"{date_str}-devlog-draft.md", message.content[0].text)


def generate_troubleshooting(commits, repo, date_str):
    trouble_keywords = ["fix", "bug", "error", "hotfix", "revert", "수정", "오류", "버그", "해결"]

    found = [c for c in commits if any(k in c["commit"]["message"].lower() for k in trouble_keywords)]
    if not found:
        print("트러블슈팅 관련 커밋 없음 — 스킵")
        return

    for commit in found:
        msg = commit["commit"]["message"].splitlines()[0]
        sha = commit["sha"]
        detail = fetch_commit_detail(repo, sha)
        diff_summary = "\n".join(
            f"- {f['filename']} (+{f['additions']}/-{f['deletions']})"
            for f in detail.get("files", [])[:10]
        )

        print(f"트러블슈팅 초안 생성 중: {msg}")
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=(
                "당신은 개발자의 GitHub 커밋을 보고 한국어 트러블슈팅 블로그 초안을 작성합니다. "
                "Hugo Markdown 형식으로 아래 frontmatter를 포함해서 작성하세요:\n"
                "---\n"
                "title: \"제목\"\n"
                "date: 날짜\n"
                "draft: true\n"
                "categories: [\"troubleshooting\"]\n"
                "tags: []\n"
                "---\n"
                "내용은 문제 상황 / 원인 분석 / 해결 방법 구조로 작성하세요. "
                "이 글은 초안이므로 작성자가 나중에 직접 수정할 예정입니다."
            ),
            messages=[{
                "role": "user",
                "content": f"날짜: {date_str}\n커밋 메시지: {msg}\n\n변경 파일:\n{diff_summary}"
            }],
        )

        slug = slugify(msg)
        write_draft(f"{date_str}-{slug}-draft.md", message.content[0].text)


def generate_til(todos, date_str):
    todos_str = "\n".join(f"- {t}" for t in todos)
    content = (
        f"---\n"
        f"title: \"{date_str} TIL\"\n"
        f"date: {date_str}\n"
        f"draft: true\n"
        f"categories: [\"til\"]\n"
        f"tags: []\n"
        f"---\n\n"
        f"## 오늘 할 일\n{todos_str}\n\n"
        f"## 한 일\n\n\n"
        f"## 배운 것\n"
    )
    write_draft(f"{date_str}-til-draft.md", content)
    print("작성 후 '한 일'과 '배운 것' 섹션을 채워주세요.")


def main():
    parser = argparse.ArgumentParser(description="커밋 기반 블로그 초안 생성기")
    parser.add_argument("--repo", default="moodot_clone", help="대상 레포 이름")
    parser.add_argument("--date", default=datetime.now(KST).strftime("%Y-%m-%d"), help="날짜 (YYYY-MM-DD)")
    parser.add_argument("--til", nargs="+", help="오늘 할 일 목록 (예: --til 'EC2 배포' 'CD 구성')")
    args = parser.parse_args()

    if args.til:
        generate_til(args.til, args.date)
        print("\n완료! content/drafts/ 폴더에서 초안을 확인하세요.")
        return

    print(f"대상: {GITHUB_USER}/{args.repo} | 날짜: {args.date}")
    commits = fetch_commits(args.repo, args.date)
    print(f"커밋 수: {len(commits)}")

    generate_devlog(commits, args.date)
    generate_troubleshooting(commits, args.repo, args.date)
    print("\n완료! content/drafts/ 폴더에서 초안을 확인하세요.")


if __name__ == "__main__":
    main()
