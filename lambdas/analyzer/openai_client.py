"""OpenAI Responses API client for error analysis."""
from __future__ import annotations

import json
import logging
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

    try:
        return json.loads(response.output_text)
    except (json.JSONDecodeError, AttributeError) as e:
        logger.error("Failed to parse classification response: %s", e)
        return {
            "category": "Unknown",
            "severity": "medium",
            "is_actionable": False,
            "summary": error.message[:100],
        }


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

    user_content = f"""Error Classification: {json.dumps(classification)}

Service: {error.service}
Message: {error.message}
Stack Trace:
{error.stack_trace[:4000]}

Repository: {github_owner}/{github_repo} (branch: {branch})

Please use the get_file_content tool to fetch relevant source files from the stack trace, then analyze the root cause."""

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

    try:
        return json.loads(response.output_text)
    except (json.JSONDecodeError, AttributeError) as e:
        logger.error("Failed to parse analysis response: %s", e)
        return {
            "root_cause": "Analysis failed",
            "affected_files": [],
            "suggested_fix": "",
            "confidence": 0.0,
        }


def _has_tool_calls(response) -> bool:
    return any(item.type == "function_call" for item in response.output)


def _execute_tool(name: str, args: dict, owner: str, repo: str, branch: str) -> str:
    """Execute a tool call against GitHub API."""
    import requests

    headers = {
        "Authorization": f"Bearer {Config.GITHUB_TOKEN}",
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
