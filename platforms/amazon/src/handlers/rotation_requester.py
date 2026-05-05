"""Lambda handler — call POST /applications/2023-11-30/clientSecret.

Manually invoked (D3 policy C). Reads app/credentials, exchanges for an LWA
grantless token with the rotation scope, calls the SP-API rotation endpoint,
records the request in DynamoDB, and emits an SES alert. The new secret
arrives asynchronously on the new-secret SQS queue and is processed by
CredentialRotationProcessor.
"""

import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context: object) -> dict:
    # Implementation lands in next commit; stub keeps the deploy green.
    logger.info("RotationRequester invoked")
    return {"status": "stub"}
