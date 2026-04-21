"""Lambda handler — request an SP-API report and track the job in DynamoDB."""

import logging
from datetime import datetime, timedelta, timezone

from sp_api.api import Reports
from sp_api.base import Marketplaces

from sincerelyhers_amazon import credentials, dynamodb

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context: object) -> dict:
    seller_alias: str = event["seller_alias"]
    marketplace_id: str = event["marketplace_id"]
    report_type: str = event["report_type"]
    lookback_days: int = int(event.get("lookback_days", 1))

    creds = credentials.get_sp_api_credentials(seller_alias)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)

    report_api = Reports(marketplace=Marketplaces.US, credentials=creds)
    response = report_api.create_report(
        reportType=report_type,
        marketplaceIds=[marketplace_id],
        dataStartTime=start.isoformat(),
        dataEndTime=end.isoformat(),
    )
    report_id: str = response.payload["reportId"]

    dynamodb.create_job(
        report_id=report_id,
        seller_alias=seller_alias,
        report_type=report_type,
        marketplace_id=marketplace_id,
    )

    logger.info("Requested report %s for %s", report_id, seller_alias)
    return {"reportId": report_id}
