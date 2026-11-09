"""Lambda handler — process the "Application Client New Secret" notification.

D6 verify-then-write: exchanges the new client_secret for an LWA grantless
token before calling secretsmanager:PutSecretValue on app/credentials.
Verification failure -> the message goes back to SQS, eventually the DLQ;
the existing app/credentials is preserved.

Notification envelope (per SP-API Notifications spec; exact payload
schema TBD until first observed live in Layer 2 smoke test):

    {
      "notificationVersion": "1.0",
      "notificationType": "APPLICATION_OAUTH_CLIENT_NEW_SECRET",  # provisional
      "payloadVersion": "1.0",
      "eventTime": "...",
      "payload": {
        "applicationOAuthClientNewSecret": {
          "clientId": "...",
          "newClientSecret": "...",
          "oldClientSecretExpiryTime": "..."
        }
      },
      "notificationMetadata": { "applicationId": "...", ... }
    }

Once the real payload is observed against BobNathan-Test, replace the
provisional schema above and update this handler accordingly.
"""

import json
import logging
import os
from typing import NoReturn

import boto3

from sincerelyhers_amazon import rotation


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class NewSecretParseError(Exception):
    """The notification did not match the expected new-secret shape."""


def lambda_handler(event: dict, context: object) -> None:
    for record in event["Records"]:
        try:
            body = json.loads(record["body"])
            payload = body["payload"]["applicationOAuthClientNewSecret"]
            meta = body.get("notificationMetadata", {})
            client_id = payload["clientId"]
            new_client_secret = payload["newClientSecret"]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            _handle_parse_failure(record, exc)

        old_expires_at = payload.get("oldClientSecretExpiryTime", "unknown")
        application_id = meta.get("applicationId", "unknown")
        notification_id = meta.get("notificationId", "unknown")

        # D6: verify the new secret BEFORE persisting it. If verification
        # raises, the SQS message goes back unprocessed; SQS will redrive
        # it (visibility timeout) and eventually DLQ after maxReceiveCount.
        # The existing app/credentials stays intact.
        try:
            rotation.grantless_access_token(
                client_id=client_id,
                client_secret=new_client_secret,
                scope=rotation.ROTATE_SCOPE,
            )
        except Exception as exc:
            rotation.write_event(
                event_type="ROTATION_VERIFICATION_FAILED",
                extra={
                    "application_id": application_id,
                    "client_id": client_id,
                    "notification_id": notification_id,
                    "error": str(exc)[:1000],
                },
            )
            app = os.environ["APP_SHORT_NAME"]
            rotation.send_alert(
                subject=f"[{app}] Rotation verification FAILED — old secret retained",
                body=(
                    f"App:               {app}\n"
                    f"Client ID:         {client_id}\n"
                    f"Notification ID:   {notification_id}\n"
                    f"Error:             {exc}\n\n"
                    "The new client_secret did not pass LWA token exchange.\n"
                    "Existing app/credentials in Secrets Manager has NOT been\n"
                    "updated. Message will redrive and eventually DLQ.\n"
                ),
            )
            raise

        sm = boto3.client("secretsmanager")
        sm.put_secret_value(
            SecretId=rotation.app_credentials_secret_id(),
            SecretString=json.dumps(
                {"client_id": client_id, "client_secret": new_client_secret}
            ),
        )

        item = rotation.write_event(
            event_type="ROTATION_COMPLETED",
            extra={
                "application_id": application_id,
                "client_id": client_id,
                "old_secret_expires_at": old_expires_at,
                "notification_id": notification_id,
            },
        )

        app = os.environ["APP_SHORT_NAME"]
        rotation.send_alert(
            subject=f"[{app}] LWA client_secret rotation COMPLETED",
            body=(
                f"App:                       {app}\n"
                f"Client ID:                 {client_id}\n"
                f"Event ID:                  {item['event_id']}\n"
                f"Old secret expires at:     {old_expires_at}\n\n"
                "The new client_secret has been verified via grantless LWA\n"
                "exchange and written to app/credentials in Secrets Manager.\n"
                "Existing Lambdas pick up the new value on next invocation.\n"
            ),
        )
        logger.info(
            "Rotation completed for app=%s client_id=%s old_expires=%s",
            app,
            client_id,
            old_expires_at,
        )


def _handle_parse_failure(record: dict, exc: Exception) -> NoReturn:
    """Record an unparseable notification, then raise so SQS redrives it to the DLQ.

    The message body may hold the new client_secret under a field name we did
    not expect, so nothing here logs, stores, or emails a value: only the
    body's key structure and the exception type (plus the missing key name
    for a KeyError). The message itself stays recoverable in the DLQ.
    """
    try:
        shape = rotation.field_names(json.loads(record["body"]))
    except ValueError, KeyError, TypeError:
        shape = None
    detail = type(exc).__name__
    if isinstance(exc, KeyError):
        detail += f" (missing key {exc.args[0]!r})"
    message_id = record.get("messageId", "unknown")
    shape_text = json.dumps(shape, sort_keys=True)

    logger.error(
        "Unparseable new-secret notification message_id=%s error=%s field_names=%s",
        message_id,
        detail,
        shape_text,
    )
    rotation.write_event(
        event_type="NEW_SECRET_PARSE_FAILED",
        extra={
            "message_id": message_id,
            "error": detail,
            "field_names": shape_text[:4000],
        },
    )
    app = os.environ["APP_SHORT_NAME"]
    rotation.send_alert(
        subject=f"[{app}] New-secret notification could not be parsed — kept for DLQ",
        body=(
            f"App:          {app}\n"
            f"Message ID:   {message_id}\n"
            f"Error:        {detail}\n"
            f"Field names:  {shape_text}\n\n"
            "The message did not match the expected new-secret shape. Nothing\n"
            "was written to Secrets Manager. SQS will redrive it and then move\n"
            "it to the new-secret DLQ, where the new client_secret is still\n"
            "recoverable. Update the handler to the field names above.\n"
        ),
    )
    raise NewSecretParseError(f"message_id={message_id}: {detail}") from None
