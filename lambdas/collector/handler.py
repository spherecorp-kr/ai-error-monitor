"""Error Collector Lambda.

Triggered by EventBridge (daily cron).
Queries CloudWatch Logs Insights for ERROR/CRITICAL logs,
deduplicates via DynamoDB fingerprints, and sends new errors to SQS.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

import boto3
import yaml

from lambdas.shared.config import Config
from lambdas.shared.models import ErrorEntry, TargetConfig
from lambdas.collector.fingerprint import deduplicate_errors

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

logs_client = boto3.client("logs", region_name=Config.AWS_REGION)
sqs_client = boto3.client("sqs", region_name=Config.AWS_REGION)


def handler(event: dict, context) -> dict:
    """Main Lambda handler."""
    logger.info("Error collector started. Event: %s", json.dumps(event))

    targets = _load_targets(event)
    all_errors: list[ErrorEntry] = []

    for target in targets:
        errors = _collect_errors_for_target(target)
        logger.info("Target '%s': collected %d errors", target.name, len(errors))
        all_errors.extend(errors)

    # Deduplicate against DynamoDB
    new_errors = deduplicate_errors(all_errors)
    logger.info("After dedup: %d new errors (from %d total)", len(new_errors), len(all_errors))

    # Send to SQS for analysis
    sent = _send_to_sqs(new_errors, targets)

    return {
        "statusCode": 200,
        "body": {
            "total_collected": len(all_errors),
            "new_errors": len(new_errors),
            "sent_to_sqs": sent,
            "targets_processed": len(targets),
        },
    }


def _load_targets(event: dict) -> list[TargetConfig]:
    """Load targets from event override or config file."""
    if "targets" in event:
        return [TargetConfig.from_dict(t) for t in event["targets"]]

    try:
        with open("config/targets.yaml") as f:
            data = yaml.safe_load(f)
        return [TargetConfig.from_dict(t) for t in data.get("targets", [])]
    except FileNotFoundError:
        logger.error("config/targets.yaml not found")
        return []


def _collect_errors_for_target(target: TargetConfig) -> list[ErrorEntry]:
    """Query CloudWatch Logs Insights for errors in a target."""
    errors: list[ErrorEntry] = []
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=Config.LOG_QUERY_HOURS)

    # Resolve log group patterns to actual log groups
    log_groups = _resolve_log_groups(target)
    if not log_groups:
        logger.warning("No log groups found for target '%s'", target.name)
        return errors

    query = """
        fields @timestamp, @message, @logStream
        | filter level = "ERROR" or level = "CRITICAL"
            or @message like /(?i)exception|error|fatal/
        | sort @timestamp desc
        | limit @limit@
    """.replace("@limit@", str(Config.MAX_ERRORS_PER_RUN))

    try:
        response = logs_client.start_query(
            logGroupNames=log_groups,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=query,
            limit=Config.MAX_ERRORS_PER_RUN,
        )
        query_id = response["queryId"]

        # Poll for results
        results = _wait_for_query(query_id)

        for result in results:
            entry = _parse_log_result(result, target)
            if entry:
                errors.append(entry)

    except Exception as e:
        logger.error("Failed to query logs for target '%s': %s", target.name, e)

    return errors


def _resolve_log_groups(target: TargetConfig) -> list[str]:
    """Resolve log group patterns to actual log group names."""
    log_groups = []
    for lg_config in target.log_groups:
        pattern = lg_config.get("pattern", "")
        if "*" in pattern:
            # Use describe_log_groups with prefix
            prefix = pattern.split("*")[0]
            try:
                paginator = logs_client.get_paginator("describe_log_groups")
                for page in paginator.paginate(logGroupNamePrefix=prefix):
                    for group in page.get("logGroups", []):
                        log_groups.append(group["logGroupName"])
            except Exception as e:
                logger.error("Failed to resolve log group pattern '%s': %s", pattern, e)
        else:
            log_groups.append(pattern)
    return log_groups


def _wait_for_query(query_id: str, max_wait: int = 60) -> list[list[dict]]:
    """Poll CloudWatch Logs Insights query until complete."""
    for _ in range(max_wait):
        response = logs_client.get_query_results(queryId=query_id)
        status = response["status"]
        if status == "Complete":
            return response.get("results", [])
        if status in ("Failed", "Cancelled"):
            logger.error("Query %s failed with status: %s", query_id, status)
            return []
        time.sleep(1)
    logger.error("Query %s timed out after %ds", query_id, max_wait)
    return []


def _parse_log_result(result: list[dict], target: TargetConfig) -> ErrorEntry | None:
    """Parse a CloudWatch Logs Insights result row into an ErrorEntry."""
    fields = {item["field"]: item["value"] for item in result}
    message_raw = fields.get("@message", "")

    # Try to parse as JSON (structured logging)
    try:
        log_data = json.loads(message_raw)
        return ErrorEntry(
            timestamp=log_data.get("@timestamp", fields.get("@timestamp", "")),
            service=log_data.get("service", _extract_service(fields.get("@logStream", ""), target)),
            environment=log_data.get("environment", target.name),
            level=log_data.get("level", "ERROR"),
            message=log_data.get("message", ""),
            stack_trace=log_data.get("stack_trace", log_data.get("exception", "")),
            logger=log_data.get("logger_name", ""),
            trace_id=log_data.get("traceId", ""),
            source=fields.get("@logStream", ""),
        )
    except (json.JSONDecodeError, TypeError):
        # Plain text log
        return ErrorEntry(
            timestamp=fields.get("@timestamp", ""),
            service=_extract_service(fields.get("@logStream", ""), target),
            environment=target.name,
            level="ERROR",
            message=message_raw[:500],
            source=fields.get("@logStream", ""),
        )


def _extract_service(log_stream: str, target: TargetConfig) -> str:
    """Extract service name from log stream or target config."""
    # EKS format: {pod-name}_{namespace}_{container}
    # EC2 format: {service-name}/...
    for svc in target.services:
        if svc in log_stream:
            return svc
    return log_stream.split("/")[0] if "/" in log_stream else log_stream.split("_")[0]


def _send_to_sqs(errors: list[ErrorEntry], targets: list[TargetConfig]) -> int:
    """Send errors to SQS in batches for analysis."""
    if not errors or not Config.SQS_QUEUE_URL:
        return 0

    # Build target lookup
    target_map = {t.name: t for t in targets}
    sent = 0

    # SQS batch limit: 10 messages
    batch: list[dict] = []
    for error in errors:
        target = target_map.get(error.environment, targets[0])
        message = {
            "error": error.to_dict(),
            "target": {
                "github_owner": target.github_owner,
                "github_repo": target.github_repo,
                "branch": target.branch,
            },
        }
        batch.append({
            "Id": str(sent),
            "MessageBody": json.dumps(message, default=str),
            "MessageGroupId": error.service,
        })
        sent += 1

        if len(batch) == 10:
            sqs_client.send_message_batch(QueueUrl=Config.SQS_QUEUE_URL, Entries=batch)
            batch = []

    if batch:
        sqs_client.send_message_batch(QueueUrl=Config.SQS_QUEUE_URL, Entries=batch)

    return sent
