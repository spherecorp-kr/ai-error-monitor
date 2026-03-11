"""Error Analyzer Lambda.

Triggered by SQS messages from error-collector.
Classifies errors with GPT-5 Nano, analyzes actionable ones with Codex-Mini,
and creates GitHub issues.
"""
from __future__ import annotations

import json
import logging

import boto3

from lambdas.shared.config import Config
from lambdas.shared.models import ErrorEntry, ErrorAnalysis
from lambdas.analyzer.openai_client import classify_error, analyze_error
from lambdas.analyzer.github_client import create_issue

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb", region_name=Config.AWS_REGION)


def handler(event: dict, context) -> dict:
    """Process SQS messages containing errors to analyze."""
    records = event.get("Records", [])
    logger.info("Analyzer received %d records", len(records))

    results = {"analyzed": 0, "issues_created": 0, "skipped": 0, "errors": 0}

    for record in records:
        try:
            body = json.loads(record["body"])
            error_data = body["error"]
            target = body["target"]

            error = ErrorEntry(**{k: v for k, v in error_data.items() if k != "fingerprint"})

            # Step 1: Classify with GPT-5 Nano
            classification = classify_error(error)
            logger.info(
                "Classified [%s] %s: %s/%s (actionable=%s)",
                error.service,
                error.fingerprint,
                classification["category"],
                classification["severity"],
                classification["is_actionable"],
            )

            analysis = ErrorAnalysis(
                fingerprint=error.fingerprint,
                category=classification["category"],
                severity=classification["severity"],
                is_actionable=classification["is_actionable"],
                summary=classification["summary"],
            )

            # Step 2: Deep analysis for actionable errors
            if classification["is_actionable"]:
                deep = analyze_error(
                    error,
                    classification,
                    target["github_owner"],
                    target["github_repo"],
                    target.get("branch", "main"),
                )
                analysis.root_cause = deep.get("root_cause", "")
                analysis.affected_files = deep.get("affected_files", [])
                analysis.suggested_fix = deep.get("suggested_fix", "")
                analysis.confidence = deep.get("confidence", 0.0)

            # Step 3: Create GitHub issue (for medium+ severity)
            if classification["severity"] in ("critical", "high", "medium"):
                issue_url = create_issue(
                    error, analysis, target["github_owner"], target["github_repo"]
                )
                if issue_url:
                    analysis.issue_url = issue_url
                    results["issues_created"] += 1

                # Save analysis to DynamoDB
                _save_analysis(analysis)
                results["analyzed"] += 1
            else:
                results["skipped"] += 1

        except Exception as e:
            logger.error("Failed to process record: %s", e, exc_info=True)
            results["errors"] += 1

    logger.info("Analyzer results: %s", results)
    return {"statusCode": 200, "body": results}


def _save_analysis(analysis: ErrorAnalysis) -> None:
    """Save analysis result to DynamoDB."""
    try:
        table = dynamodb.Table(Config.DYNAMODB_TABLE)
        table.update_item(
            Key={"fingerprint": analysis.fingerprint},
            UpdateExpression=(
                "SET category = :cat, severity = :sev, "
                "root_cause = :rc, suggested_fix = :fix, "
                "issue_url = :url, confidence = :conf"
            ),
            ExpressionAttributeValues={
                ":cat": analysis.category,
                ":sev": analysis.severity,
                ":rc": analysis.root_cause or "N/A",
                ":fix": analysis.suggested_fix or "N/A",
                ":url": analysis.issue_url or "",
                ":conf": str(analysis.confidence),
            },
        )
    except Exception as e:
        logger.error("Failed to save analysis: %s", e)
