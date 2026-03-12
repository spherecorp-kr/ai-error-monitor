"""OpenAI Responses API client for error analysis."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from openai import OpenAI

from lambdas.shared.config import Config
from lambdas.shared.models import ErrorEntry, ErrorAnalysis

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.txt").read_text()


def _get_client() -> OpenAI:
    return OpenAI(api_key=Config.OPENAI_API_KEY)


def classify_error(error: ErrorEntry) -> dict:
    """Classify an error using GPT-5 Nano (fast, cheap)."""
    client = _get_client()
    system_prompt = _load_prompt("classify")

    user_content = f"""Service: {error.service}
Level: {error.level}
Message: {error.message}
Stack Trace:
{error.stack_trace[:2000]}
Logger: {error.logger}"""

    response = client.responses.create(
        model=Config.CLASSIFY_MODEL,
        instructions=system_prompt,
        input=user_content,
        reasoning={"effort": "low"},
    )

    return _parse_json_response(response.output_text, {
        "category": "Unknown",
        "severity": "medium",
        "is_actionable": False,
        "summary": error.message[:100],
    }, "classification")


def analyze_error(
    error: ErrorEntry,
    classification: dict,
    github_owner: str,
    github_repo: str,
    branch: str = "main",
) -> dict:
    """Deep analysis using Codex-Mini with GitHub code access."""
    client = _get_client()
    system_prompt = _load_prompt("analyze")

    stack_section = error.stack_trace[:4000] if error.stack_trace else "(스택트레이스 없음 - search_code 도구를 사용하여 에러 메시지에 언급된 클래스/메서드를 검색하세요)"

    user_content = f"""Error Classification: {json.dumps(classification)}

Service: {error.service}
Message: {error.message}
Stack Trace:
{stack_section}

Repository: {github_owner}/{github_repo} (branch: {branch})

Use get_file_content or search_code tools to fetch relevant source files, then analyze the root cause in Korean."""

    # Define tools for GitHub code access
    tools = [
        {
            "type": "function",
            "name": "get_file_content",
            "description": "Fetch a file's content from the GitHub repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root (e.g., 'src/main/java/com/example/Service.java')",
                    }
                },
                "required": ["path"],
            },
        },
        {
            "type": "function",
            "name": "search_code",
            "description": "Search for code in the repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (class name, method name, etc.)",
                    }
                },
                "required": ["query"],
            },
        },
    ]

    # Use Responses API with tool loop
    response = client.responses.create(
        model=Config.ANALYZE_MODEL,
        instructions=system_prompt,
        input=user_content,
        tools=tools,
        reasoning={"effort": "medium"},
    )

    # Handle tool calls (max 5 iterations)
    for _ in range(5):
        if not _has_tool_calls(response):
            break

        tool_results = []
        for item in response.output:
            if item.type == "function_call":
                result = _execute_tool(
                    item.name, json.loads(item.arguments), github_owner, github_repo, branch
                )
                tool_results.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": result,
                })

        if tool_results:
            response = client.responses.create(
                model=Config.ANALYZE_MODEL,
                instructions=system_prompt,
                input=tool_results,
                tools=tools,
                previous_response_id=response.id,
                reasoning={"effort": "medium"},
            )

    return _parse_json_response(response.output_text, {
        "root_cause": "분석 실패",
        "affected_files": [],
        "suggested_fix": "",
        "confidence": 0.0,
    }, "analysis")


def _parse_json_response(text: str | None, fallback: dict, label: str) -> dict:
    """Parse JSON from model output, handling markdown fences and embedded JSON."""
    if not text:
        logger.error("Empty %s response", label)
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting JSON from markdown code fences
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Try finding first { ... } block
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    logger.error("Failed to parse %s response: %s", label, text[:200])
    return fallback


def _has_tool_calls(response) -> bool:
    return any(item.type == "function_call" for item in response.output)


def _execute_tool(name: str, args: dict, owner: str, repo: str, branch: str) -> str:
    """Execute a tool call against GitHub API."""
    import requests
    from lambdas.shared.github_auth import get_installation_token

    token = get_installation_token() if Config.GITHUB_APP_ID else Config.GITHUB_TOKEN
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3.raw",
    }

    if name == "get_file_content":
        path = args["path"]
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.text[:8000]  # Limit content size
        return f"File not found: {path} (HTTP {resp.status_code})"

    elif name == "search_code":
        query = args["query"]
        url = f"https://api.github.com/search/code?q={query}+repo:{owner}/{repo}"
        headers["Accept"] = "application/vnd.github.v3+json"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])[:5]
            return json.dumps([{"path": i["path"], "name": i["name"]} for i in items])
        return f"Search failed (HTTP {resp.status_code})"

    return f"Unknown tool: {name}"
