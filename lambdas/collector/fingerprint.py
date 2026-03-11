"""Error fingerprinting and deduplication via DynamoDB."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.shared.config import Config
from lambdas.shared.models import ErrorEntry

logger = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb", region_name=Config.AWS_REGION)


def deduplicate_errors(errors: list[ErrorEntry]) -> list[ErrorEntry]:
    """Filter out errors already seen within DUPLICATE_TTL_HOURS."""
    if not errors:
        return []

    table = dynamodb.Table(Config.DYNAMODB_TABLE)
    new_errors: list[ErrorEntry] = []
    now_ts = int(time.time())
    ttl = Config.DUPLICATE_TTL_HOURS * 3600

    # Batch check fingerprints
    fingerprints = {e.fingerprint: e for e in errors}

    # DynamoDB BatchGetItem (max 100 keys)
    keys = [{"fingerprint": fp} for fp in fingerprints.keys()]
    existing_fps: set[str] = set()

    for i in range(0, len(keys), 100):
        batch_keys = keys[i : i + 100]
        try:
            response = dynamodb.meta.client.batch_get_item(
                RequestItems={
                    Config.DYNAMODB_TABLE: {
                        "Keys": batch_keys,
                        "ProjectionExpression": "fingerprint, #ts",
                        "ExpressionAttributeNames": {"#ts": "ttl"},
                    }
                }
            )
            for item in response.get("Responses", {}).get(Config.DYNAMODB_TABLE, []):
                existing_fps.add(item["fingerprint"])
        except Exception as e:
            logger.error("DynamoDB batch_get_item failed: %s", e)

    # Filter and record new fingerprints
    for fp, error in fingerprints.items():
        if fp not in existing_fps:
            new_errors.append(error)
            # Write new fingerprint
            try:
                table.put_item(
                    Item={
                        "fingerprint": fp,
                        "service": error.service,
                        "message": error.message[:200],
                        "first_seen": error.timestamp,
                        "last_seen": error.timestamp,
                        "count": 1,
                        "ttl": now_ts + ttl,
                    }
                )
            except Exception as e:
                logger.error("DynamoDB put_item failed for %s: %s", fp, e)
        else:
            # Update count and last_seen for existing
            try:
                table.update_item(
                    Key={"fingerprint": fp},
                    UpdateExpression="SET #count = #count + :inc, last_seen = :ts, #ttl = :ttl",
                    ExpressionAttributeNames={"#count": "count", "#ttl": "ttl"},
                    ExpressionAttributeValues={
                        ":inc": 1,
                        ":ts": error.timestamp,
                        ":ttl": now_ts + ttl,
                    },
                )
            except Exception as e:
                logger.error("DynamoDB update_item failed for %s: %s", fp, e)

    return new_errors
