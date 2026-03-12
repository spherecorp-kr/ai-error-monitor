"""Loki LogQL query client for collecting errors from Grafana Loki."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import requests

from lambdas.shared.config import Config
from lambdas.shared.models import ErrorEntry

logger = logging.getLogger(__name__)


def query_loki_errors(
    loki_url: str,
    queries: dict[str, str],
    services: list[str],
    target_name: str,
    hours: int | None = None,
) -> list[ErrorEntry]:
    """Query Loki for error logs using LogQL.

    Args:
        loki_url: Loki base URL (e.g., http://loki.monitoring.svc.cluster.local:3100)
        queries: Dict of source_type -> LogQL query (e.g., {"backend": '{...}', "frontend": '{...}'})
        services: List of known service names for extraction
        target_name: Target environment name
        hours: Hours to look back (defaults to Config.LOG_QUERY_HOURS)
    """
    all_errors: list[ErrorEntry] = []
    hours = hours or Config.LOG_QUERY_HOURS
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    # Loki uses nanosecond timestamps
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


def _parse_loki_entry(
    line: str,
    labels: dict,
    services: list[str],
    target_name: str,
    source_type: str,
) -> ErrorEntry | None:
    """Parse a Loki log line into an ErrorEntry."""
    # Determine service from labels or content
    app_label = labels.get("app", "")
    service = _match_service(app_label, services) or app_label

    if source_type == "frontend":
        return _parse_frontend_entry(line, labels, target_name)

    # Backend: try JSON structured log
    try:
        log_data = json.loads(line)
        return ErrorEntry(
            timestamp=log_data.get("@timestamp", ""),
            service=service or log_data.get("service", "unknown"),
            environment=target_name,
            level=log_data.get("level", labels.get("level", "ERROR")),
            message=log_data.get("message", ""),
            stack_trace=log_data.get("stack_trace", log_data.get("exception", "")),
            logger=log_data.get("logger_name", ""),
            trace_id=log_data.get("traceId", ""),
            source=f"loki/{source_type}",
        )
    except (json.JSONDecodeError, TypeError):
        # Plain text
        return ErrorEntry(
            timestamp="",
            service=service or "unknown",
            environment=target_name,
            level=labels.get("level", "ERROR"),
            message=line[:500],
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
        # Faro error format
        message = log_data.get("message", log_data.get("value", ""))
        stack_trace = log_data.get("stacktrace", log_data.get("stack_trace", ""))

        if not message:
            return None

        return ErrorEntry(
            timestamp=log_data.get("timestamp", log_data.get("@timestamp", "")),
            service=app_name,
            environment=target_name,
            level="ERROR",
            message=message,
            stack_trace=stack_trace if isinstance(stack_trace, str) else json.dumps(stack_trace),
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
