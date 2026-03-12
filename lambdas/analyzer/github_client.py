"""GitHub Issues API client."""
from __future__ import annotations

import json
import logging

import requests

from lambdas.shared.config import Config
from lambdas.shared.models import ErrorEntry, ErrorAnalysis
from lambdas.shared.github_auth import get_installation_token

logger = logging.getLogger(__name__)


def _get_github_token() -> str:
    """Get GitHub token - prefer App installation token, fallback to PAT."""
    if Config.GITHUB_APP_ID and Config.GITHUB_APP_PRIVATE_KEY:
        return get_installation_token()
    return Config.GITHUB_TOKEN

SEVERITY_EMOJI = {
    "critical": "\U0001f534",  # red circle
    "high": "\U0001f7e0",      # orange circle
    "medium": "\U0001f7e1",    # yellow circle
    "low": "\U0001f7e2",       # green circle
}

SEVERITY_KR = {
    "critical": "심각",
    "high": "높음",
    "medium": "보통",
    "low": "낮음",
}

ISSUE_TEMPLATE = """## 에러 상세

| 항목 | 내용 |
|------|------|
| **서비스** | `{service}` |
| **심각도** | {severity_emoji} {severity_kr} ({severity}) |
| **분류** | {category} |
| **발생 시각** | {timestamp} |
| **핑거프린트** | `{fingerprint}` |

### 에러 메시지
```
{message}
```

{stack_trace_section}

---

## AI 원인 분석
{root_cause}

### 관련 파일
{affected_files}

### 수정 제안
{suggested_fix}

### 신뢰도: {confidence}%

---
_[AI Error Monitor](https://github.com/spherecorp-kr/ai-error-monitor) 자동 생성_
"""


def create_issue(
    error: ErrorEntry,
    analysis: ErrorAnalysis,
    github_owner: str,
    github_repo: str,
) -> str | None:
    """Create a GitHub issue for the analyzed error. Returns issue URL or None."""
    existing = _find_duplicate_issue(error, github_owner, github_repo)
    if existing:
        logger.info("Duplicate issue found: %s", existing)
        _add_comment(existing, error, github_owner, github_repo)
        return existing

    title = f"[Auto] {analysis.summary or error.message[:80]}"
    if len(title) > 120:
        title = title[:117] + "..."

    # Stack trace section - only show if available
    if error.stack_trace and error.stack_trace.strip():
        stack_trace_section = f"### 스택 트레이스\n```java\n{error.stack_trace[:3000]}\n```"
    else:
        stack_trace_section = ""

    body = ISSUE_TEMPLATE.format(
        service=error.service,
        severity_emoji=SEVERITY_EMOJI.get(analysis.severity, ""),
        severity_kr=SEVERITY_KR.get(analysis.severity, analysis.severity),
        severity=analysis.severity.upper(),
        category=analysis.category,
        timestamp=error.timestamp,
        fingerprint=error.fingerprint,
        message=error.message[:500],
        stack_trace_section=stack_trace_section,
        root_cause=analysis.root_cause or "분석 결과 없음",
        affected_files="\n".join(f"- `{f}`" for f in analysis.affected_files) or "N/A",
        suggested_fix=analysis.suggested_fix or "수정 제안 없음",
        confidence=int(analysis.confidence * 100),
    )

    labels = [
        "auto-detected",
        f"severity:{analysis.severity}",
        f"service:{error.service}",
    ]
    if analysis.category != "Unknown":
        labels.append(f"category:{analysis.category}")

    url = f"https://api.github.com/repos/{github_owner}/{github_repo}/issues"
    headers = {
        "Authorization": f"Bearer {_get_github_token()}",
        "Accept": "application/vnd.github.v3+json",
    }
    payload = {
        "title": title,
        "body": body,
        "labels": labels,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 201:
            issue_url = resp.json()["html_url"]
            logger.info("Created issue: %s", issue_url)
            return issue_url
        else:
            logger.error("Failed to create issue: HTTP %d - %s", resp.status_code, resp.text)
            if resp.status_code == 422 and "label" in resp.text.lower():
                payload["labels"] = ["auto-detected"]
                resp = requests.post(url, headers=headers, json=payload, timeout=15)
                if resp.status_code == 201:
                    return resp.json()["html_url"]
    except Exception as e:
        logger.error("Failed to create GitHub issue: %s", e)

    return None


def _find_duplicate_issue(
    error: ErrorEntry, github_owner: str, github_repo: str
) -> str | None:
    """Search for existing issue with same fingerprint."""
    url = "https://api.github.com/search/issues"
    headers = {
        "Authorization": f"Bearer {_get_github_token()}",
        "Accept": "application/vnd.github.v3+json",
    }
    query = f'repo:{github_owner}/{github_repo} is:issue is:open "{error.fingerprint}" label:auto-detected'

    try:
        resp = requests.get(url, headers=headers, params={"q": query}, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                return items[0]["html_url"]
    except Exception as e:
        logger.error("Failed to search issues: %s", e)

    return None


def _add_comment(
    issue_url: str, error: ErrorEntry, github_owner: str, github_repo: str
) -> None:
    """Add a comment to an existing issue for a recurring error."""
    issue_number = issue_url.rstrip("/").split("/")[-1]
    url = f"https://api.github.com/repos/{github_owner}/{github_repo}/issues/{issue_number}/comments"
    headers = {
        "Authorization": f"Bearer {_get_github_token()}",
        "Accept": "application/vnd.github.v3+json",
    }
    body = f"**동일 에러 재발생** `{error.timestamp}`\n\n핑거프린트: `{error.fingerprint}`\n서비스: `{error.service}`"

    try:
        requests.post(url, headers=headers, json={"body": body}, timeout=10)
    except Exception as e:
        logger.error("Failed to add comment: %s", e)
