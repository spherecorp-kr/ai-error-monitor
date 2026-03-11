"""Shared configuration for all lambdas."""
import json
import os
import logging

import boto3

logger = logging.getLogger(__name__)

_secrets_cache: dict[str, str] = {}


def _get_secret(arn: str) -> str:
    """Fetch secret from AWS Secrets Manager with caching."""
    if arn in _secrets_cache:
        return _secrets_cache[arn]
    if not arn or not arn.startswith("arn:"):
        return ""
    try:
        client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-southeast-7"))
        response = client.get_secret_value(SecretId=arn)
        value = response["SecretString"]
        # Handle JSON secrets (e.g., {"api_key": "sk-..."})
        try:
            parsed = json.loads(value)
            value = parsed.get("api_key") or parsed.get("token") or parsed.get("value") or value
        except (json.JSONDecodeError, TypeError):
            pass
        _secrets_cache[arn] = value
        return value
    except Exception as e:
        logger.error("Failed to fetch secret %s: %s", arn, e)
        return ""


class Config:
    # AWS
    AWS_REGION = os.environ.get("AWS_REGION_OVERRIDE", os.environ.get("AWS_REGION", "ap-southeast-7"))
    SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL", "")
    DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "ai-error-monitor")

    # OpenAI (from Secrets Manager or env var for local dev)
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or _get_secret(os.environ.get("OPENAI_API_KEY_ARN", ""))
    CLASSIFY_MODEL = os.environ.get("CLASSIFY_MODEL", "gpt-5-nano")
    ANALYZE_MODEL = os.environ.get("ANALYZE_MODEL", "codex-mini-latest")

    # GitHub (from Secrets Manager or env var for local dev)
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or _get_secret(os.environ.get("GITHUB_TOKEN_ARN", ""))

    # Monitoring
    LOG_QUERY_HOURS = int(os.environ.get("LOG_QUERY_HOURS", "24"))
    MAX_ERRORS_PER_RUN = int(os.environ.get("MAX_ERRORS_PER_RUN", "200"))
    DUPLICATE_TTL_HOURS = int(os.environ.get("DUPLICATE_TTL_HOURS", "72"))
