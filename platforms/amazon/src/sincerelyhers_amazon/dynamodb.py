"""DynamoDB helpers for the Amazon report-jobs table."""

import os
from datetime import datetime, timezone

import boto3


def create_job(
    report_id: str,
    seller_alias: str,
    report_type: str,
    marketplace_id: str,
) -> None:
    """Write a new report-job row with status ``REQUESTED``."""
    table_name = os.environ["REPORT_JOBS_TABLE"]
    table = boto3.resource("dynamodb").Table(table_name)
    table.put_item(
        Item={
            "report_id": report_id,
            "seller_alias": seller_alias,
            "report_type": report_type,
            "marketplace_id": marketplace_id,
            "status": "REQUESTED",
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
    )
