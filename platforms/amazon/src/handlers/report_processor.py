"""Lambda handler — process a REPORT_PROCESSING_FINISHED notification from SQS."""

import json
import logging
import os
from datetime import datetime, timezone

import boto3

from sincerelyhers_amazon import credentials, dynamodb, report_document

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context: object) -> None:
    for record in event["Records"]:
        body = json.loads(record["body"])
        notification = body["payload"]["reportProcessingFinishedNotification"]
        report_id: str = notification["reportId"]
        processing_status: str = notification["processingStatus"]
        document_id = notification.get("reportDocumentId")

        if processing_status != "DONE":
            dynamodb.update_status(
                report_id,
                "FAILED",
                error_message=f"SP-API processing status = {processing_status}",
            )
            logger.warning("Report %s not DONE (%s); marked FAILED", report_id, processing_status)
            continue

        if not document_id:
            dynamodb.update_status(
                report_id,
                "FAILED",
                error_message="DONE status but no reportDocumentId in notification",
            )
            continue

        try:
            _process_one(report_id, document_id)
        except Exception as exc:
            logger.exception("Failed to process report %s", report_id)
            dynamodb.update_status(report_id, "FAILED", error_message=str(exc))
            raise


def _process_one(report_id: str, document_id: str) -> None:
    job = dynamodb.get_job(report_id)
    seller_alias: str = job["seller_alias"]
    report_type: str = job["report_type"]

    dynamodb.update_status(report_id, "PROCESSING", document_id=document_id)

    creds = credentials.get_sp_api_credentials(seller_alias)
    raw = report_document.fetch_document(document_id, creds)

    now = datetime.now(timezone.utc)
    bucket = os.environ["REPORTS_BUCKET"]
    key = _s3_key(seller_alias, report_type, report_id, now)
    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=raw)

    dynamodb.update_status(
        report_id,
        "COMPLETED",
        s3_key=key,
        completed_at=now.isoformat(),
    )
    logger.info("Completed report %s -> s3://%s/%s", report_id, bucket, key)


def _s3_key(
    seller_alias: str,
    report_type: str,
    report_id: str,
    when: datetime,
) -> str:
    ext = _extension_for_report_type(report_type)
    date = when.strftime("%Y-%m-%d")
    return f"amazon/sincerely-services/{seller_alias}/{report_type}/{date}/{report_id}.{ext}"


def _extension_for_report_type(report_type: str) -> str:
    if report_type.startswith("GET_FLAT_FILE_"):
        return "tsv"
    if report_type.startswith("GET_XML_"):
        return "xml"
    if report_type.endswith("_JSON"):
        return "json"
    return "bin"
