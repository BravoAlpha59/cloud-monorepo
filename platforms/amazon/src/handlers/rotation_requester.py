"""Lambda handler — call POST /applications/2023-11-30/clientSecret.

Manually invoked (D3 policy C) — `aws lambda invoke` with empty payload.
Reads app/credentials, exchanges for an LWA grantless token with the
``client_credential:rotate`` scope, calls the SP-API rotation endpoint
(expects HTTP 204), records the request in DynamoDB, and emits an SES
alert. The new secret arrives asynchronously on the new-secret SQS queue
and is processed by CredentialRotationProcessor.
"""

import logging
import os

from sincerelyhers_amazon import rotation


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context: object) -> dict:
    creds = rotation.read_app_credentials()

    access_token = rotation.grantless_access_token(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        scope=rotation.ROTATE_SCOPE,
    )

    response = rotation.call_rotate(access_token)
    if response.status_code != 204:
        body = response.text
        logger.error(
            "rotateApplicationClientSecret returned %d: %s",
            response.status_code,
            body,
        )
        rotation.write_event(
            event_type="ROTATION_REQUEST_FAILED",
            extra={
                "client_id": creds["client_id"],
                "http_status": response.status_code,
                "response_body": body[:1000],
            },
        )
        raise RuntimeError(f"rotate returned HTTP {response.status_code}: {body[:200]}")

    item = rotation.write_event(
        event_type="ROTATION_REQUESTED",
        extra={"client_id": creds["client_id"]},
    )

    app = os.environ["APP_SHORT_NAME"]
    rotation.send_alert(
        subject=f"[{app}] LWA client_secret rotation requested",
        body=(
            f"App:           {app}\n"
            f"Client ID:     {creds['client_id']}\n"
            f"Event ID:      {item['event_id']}\n\n"
            "POST /applications/2023-11-30/clientSecret returned HTTP 204.\n"
            "The new client_secret will arrive on the new-secret SQS queue\n"
            "and be processed by CredentialRotationProcessor (D6: verified\n"
            "via grantless LWA exchange before secretsmanager:PutSecretValue).\n\n"
            "Old client_secret remains valid for 7 days from now.\n"
        ),
    )
    return {"event_id": item["event_id"], "status": "REQUESTED"}
