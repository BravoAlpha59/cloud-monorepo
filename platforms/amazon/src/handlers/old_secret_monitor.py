"""Lambda handler — daily check for old-secret expiry within threshold.

D7: scans the rotation-events table for completed rotations whose
old_secret_expires_at is within EXPIRY_ALERT_HOURS of now; emits an SES
alert per row. Catches integrations that haven't yet picked up the new
secret before the 7-day cliff.
"""

import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context: object) -> None:
    # Implementation lands in next commit; stub keeps the deploy green.
    logger.info("OldSecretMonitor cron fired")
