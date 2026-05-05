"""Lambda handler — receive APPLICATION_OAUTH_CLIENT_SECRET_EXPIRY notifications.

D3 policy C (alert-only): log the event to DynamoDB and emit an SES alert
naming the rotation command. Does NOT call rotate. Operator runs
RotationRequester explicitly when ready.
"""

import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context: object) -> None:
    # Implementation lands in next commit; stub keeps the deploy green.
    logger.info(
        "ExpiryHandler invoked with %d record(s)", len(event.get("Records", []))
    )
