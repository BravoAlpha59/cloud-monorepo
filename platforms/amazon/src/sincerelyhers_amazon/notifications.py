"""Email a report-ready notification via SES with a pre-signed S3 URL.

Pre-signed URL caveat: URLs signed by a Lambda execution role expire
with the role's temporary credentials. With MaxSessionDuration=12h on
the execution role the URL lasts up to 12 hours — short of the 7-day
spec in architecture.md, which would require long-lived IAM user
credentials or CloudFront signed URLs. Good enough for dev; revisit
before prod if 7-day deliverability matters.
"""

import logging
import os
from typing import Optional

import boto3


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PRESIGN_EXPIRES_IN = 43200  # 12h — bounded above by the role's MaxSessionDuration


def send_report_ready(
    bucket: str,
    key: str,
    seller_alias: str,
    report_type: str,
    report_id: str,
    report_size_bytes: int,
) -> Optional[str]:
    """Send a REPORT_READY email via SES. Returns the SES MessageId, or None if disabled.

    If ``SES_RECIPIENTS`` is unset or empty, email sending is disabled
    and this function is a no-op. Any SES-level failure is re-raised to
    the caller — the handler swallows it so a failed email does not
    mark the job as FAILED or trigger SQS redrive.
    """
    recipients = _recipient_list()
    if not recipients:
        logger.info("SES_RECIPIENTS unset; skipping email for %s", report_id)
        return None

    sender = os.environ["SES_SENDER_EMAIL"]

    url = boto3.client("s3").generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=PRESIGN_EXPIRES_IN,
    )

    subject = f"[{seller_alias}] {report_type} report ready"
    body = (
        f"Seller:        {seller_alias}\n"
        f"Report type:   {report_type}\n"
        f"Report ID:     {report_id}\n"
        f"Size:          {report_size_bytes:,} bytes\n\n"
        f"Download (URL valid up to 12 hours):\n{url}\n\n"
        f"S3 location:\ns3://{bucket}/{key}\n"
    )

    response = boto3.client("ses").send_email(
        Source=sender,
        Destination={"ToAddresses": recipients},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        },
    )
    message_id = response["MessageId"]
    logger.info("Sent report-ready email for %s (SES MessageId %s)", report_id, message_id)
    return message_id


def _recipient_list() -> list[str]:
    raw = os.environ.get("SES_RECIPIENTS", "")
    return [r.strip() for r in raw.split(",") if r.strip()]
