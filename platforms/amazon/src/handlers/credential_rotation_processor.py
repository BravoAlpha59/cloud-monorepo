"""Lambda handler — process the "Application Client New Secret" notification.

D6 verify-then-write: exchanges the new client_secret for an LWA grantless
token before calling secretsmanager:PutSecretValue on app/credentials.
Verification failure -> the message goes back to SQS, eventually the DLQ;
the existing app/credentials is preserved.
"""

import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context: object) -> None:
    # Implementation lands in next commit; stub keeps the deploy green.
    logger.info(
        "CredentialRotationProcessor invoked with %d record(s)",
        len(event.get("Records", [])),
    )
