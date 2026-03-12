"""Loki LogQL query client for collecting errors from Grafana Loki."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import requests

from lambdas.shared.config import Config
from lambdas.shared.models import ErrorEntry

logger = logging.getLogger(__name__)

# Pattern to detect stack trace start in message
_STACK_PATTERN = re.compile(
    r'(\b\w+(?:\.\w+)*(?:Exception|Error|Throwable)\b.*?)(?:\n\tat |\n\s+at )',
    re.DOTALL,
)


def query_loki_errors(
    loki_url: str,
    queries: dict[str, str],
    services: list[str],
    target_name: str,
    hours: int | None = None,
) -> list[ErrorEntry]:
    """Query Loki for error logs using LogQL."""
    all_errors: list[ErrorEntry] = []
    hours = hours or Config.LOG_QUERY_HOURS
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    start_ns = str(int(start.timestamp() * 1e9))
    end_ns = str(int(end.timestamp() * 1e9))

    for source_type, logql in queries.items():
        try:
            errors = _execute_query(
                loki_url, logql, start_ns, end_ns, services, target_name, source_type
            )
            logger.info("Loki [%s/%s]: collected %d errors", target_name, source_type, len(errors))
            all_errors.extend(errors)
        except Exception as e:
            logger.error("Loki query failed [%s/%s]: %s", target_name, source_type, e)

    return all_errors[:Config.MAX_ERRORS_PER_RUN]


def _execute_query(
    loki_url: str,
    logql: str,
    start_ns: str,
    end_ns: str,
    services: list[str],
    target_name: str,
    source_type: str,
) -> list[ErrorEntry]:
    """Execute a single LogQL query and parse results."""
    url = f"{loki_url.rstrip('/')}/loki/api/v1/query_range"
    params = {
        "query": logql,
        "start": start_ns,
        "end": end_ns,
        "limit": Config.MAX_ERRORS_PER_RUN,
        "direction": "backward",
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "success":
        logger.error("Loki query returned status: %s", data.get("status"))
        return []

    results = data.get("data", {}).get("result", [])
    errors: list[ErrorEntry] = []

    for stream in results:
        labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            entry = _parse_loki_entry(line, labels, services, target_name, source_type)
            if entry:
                errors.append(entry)

    return errors


def _split_message_and_stack(message: str) -> tuple[str, str]:
    """Split a message that may contain an inline stack trace.

    Returns (clean_message, stack_trace).
    """
    if not message:
        return "", ""

    # Check for stack trace markers
    stack_markers = ["\n\tat ", "\n\t\tat ", "\nCaused by:", "\n    at "]
    split_idx = -1
    for marker in stack_markers:
        idx = message.find(marker)
        if idx != -1 and (split_idx == -1 or idx < split_idx):
            split_idx = idx

    if split_idx > 0:
        clean_msg = message[:split_idx].strip()
        stack = message[split_idx:].strip()
        return clean_msg, stack

    # Truncate long messages that have SQL or other inline content
    if len(message) > 300:
        # Try to find a natural break point
        for sep in ["] [", "; SQL [", "; nested exception", "]; constraint"]:
            idx = message.find(sep)
            if 50 < idx < 500:
                return message[:idx].strip(), ""

    return message, ""


def _to_kst(timestamp: str) -> str:
    """Convert UTC timestamp to KST (UTC+9) for display."""
    if not timestamp:
        return ""
    try:
        # Try common formats
        for fmt in [
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f+0000",
        ]:
            try:
                dt = datetime.strptime(timestamp, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                kst = dt.astimezone(timezone(timedelta(hours=9)))
                return kst.strftime("%Y-%m-%d %H:%M:%S KST")
            except ValueError:
                continue
    except Exception:
        pass
    return timestamp


def _parse_loki_entry(
    line: str,
    labels: dict,
    services: list[str],
    target_name: str,
    source_type: str,
) -> ErrorEntry | None:
    """Parse a Loki log line into an ErrorEntry."""
    app_label = labels.get("app", "")
    service = _match_service(app_label, services) or app_label

    if source_type == "frontend":
        return _parse_frontend_entry(line, labels, target_name)

    # Backend: try JSON structured log
    try:
        log_data = json.loads(line)

        raw_message = log_data.get("message", "")
        # Try multiple field names for stack trace
        stack_trace = (
            log_data.get("stack_trace")
            or log_data.get("exception")
            or log_data.get("stackTrace")
            or log_data.get("throwable")
            or ""
        )

        # If no explicit stack trace field, try to extract from message
        if not stack_trace:
            raw_message, stack_trace = _split_message_and_stack(raw_message)
        else:
            # Still clean up the message
            raw_message, _ = _split_message_and_stack(raw_message)

        # Convert timestamp to KST
        timestamp = _to_kst(log_data.get("@timestamp", ""))

        return ErrorEntry(
            timestamp=timestamp,
            service=service or log_data.get("service", "unknown"),
            environment=target_name,
            level=log_data.get("level", labels.get("level", "ERROR")),
            message=raw_message[:500],
            stack_trace=stack_trace[:4000],
            logger=log_data.get("logger_name", ""),
            trace_id=log_data.get("traceId", ""),
            source=f"loki/{source_type}",
        )
    except (json.JSONDecodeError, TypeError):
        msg, stack = _split_message_and_stack(line)
        return ErrorEntry(
            timestamp="",
            service=service or "unknown",
            environment=target_name,
            level=labels.get("level", "ERROR"),
            message=msg[:500],
            stack_trace=stack[:4000],
            source=f"loki/{source_type}",
        )


def _parse_frontend_entry(
    line: str,
    labels: dict,
    target_name: str,
) -> ErrorEntry | None:
    """Parse a Faro frontend error log entry."""
    app_name = labels.get("app_name", labels.get("app", "frontend"))

    try:
        log_data = json.loads(line)
        message = log_data.get("message", log_data.get("value", ""))
        stack_trace = log_data.get("stacktrace", log_data.get("stack_trace", ""))

        if not message:
            return None

        timestamp = _to_kst(
            log_data.get("timestamp", log_data.get("@timestamp", ""))
        )

        return ErrorEntry(
            timestamp=timestamp,
            service=app_name,
            environment=target_name,
            level="ERROR",
            message=message[:500],
            stack_trace=(stack_trace if isinstance(stack_trace, str) else json.dumps(stack_trace))[:4000],
            logger=log_data.get("type", "faro"),
            trace_id=log_data.get("traceId", log_data.get("trace_id", "")),
            source="loki/frontend",
        )
    except (json.JSONDecodeError, TypeError):
        return ErrorEntry(
            timestamp="",
            service=app_name,
            environment=target_name,
            level="ERROR",
            message=line[:500],
            source="loki/frontend",
        )


def _match_service(app_label: str, services: list[str]) -> str:
    """Match app label to known service name."""
    for svc in services:
        if svc in app_label:
            return svc
    return ""
