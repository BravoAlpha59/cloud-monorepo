"""DynamoDB helpers for the Amazon report-jobs table."""

import os
from datetime import datetime, timezone
from typing import Any

import boto3


def _table():
    return boto3.resource("dynamodb").Table(os.environ["REPORT_JOBS_TABLE"])


def create_job(
    report_id: str,
    seller_alias: str,
    report_type: str,
    marketplace_id: str,
) -> None:
    """Write a new report-job row with status ``REQUESTED``."""
    _table().put_item(
        Item={
            "report_id": report_id,
            "seller_alias": seller_alias,
            "report_type": report_type,
            "marketplace_id": marketplace_id,
            "status": "REQUESTED",
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def get_job(report_id: str) -> dict:
    """Fetch the report-job row for *report_id*. Raises ``KeyError`` if absent."""
    response = _table().get_item(Key={"report_id": report_id})
    if "Item" not in response:
        raise KeyError(f"No job for report_id={report_id}")
    return response["Item"]


def update_status(report_id: str, status: str, **extra: Any) -> None:
    """Set ``status`` (plus any keyword-provided fields) on the report-job row."""
    set_parts = ["#status = :status"]
    expr_names = {"#status": "status"}
    expr_values = {":status": status}
    for key, value in extra.items():
        set_parts.append(f"#{key} = :{key}")
        expr_names[f"#{key}"] = key
        expr_values[f":{key}"] = value

    _table().update_item(
        Key={"report_id": report_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )
