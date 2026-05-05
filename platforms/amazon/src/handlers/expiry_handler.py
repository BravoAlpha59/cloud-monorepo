"""Lambda handler — receive APPLICATION_OAUTH_CLIENT_SECRET_EXPIRY notifications.

D3 policy C (alert-only): log the event to DynamoDB and emit an SES alert
naming the rotation command. Does NOT call rotate. Operator runs
RotationRequester explicitly when ready.

Notification envelope (per SP-API Notifications spec):

    {
      "notificationVersion": "1.0",
      "notificationType": "APPLICATION_OAUTH_CLIENT_SECRET_EXPIRY",
      "payloadVersion": "1.0",
      "eventTime": "...",
      "payload": {
        "applicationOAuthClientSecretExpiry": {
          "clientId": "...",
          "clientSecretExpiryTime": "...",
          "clientSecretExpiryReason": "PERIODIC_ROTATION" | ...
        }
      },
      "notificationMetadata": { "applicationId": "...", ... }
    }
"""

import json
import logging
import os

from sincerelyhers_amazon import rotation


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context: object) -> None:
    for record in event["Records"]:
        body = json.loads(record["body"])
        payload = body["payload"]["applicationOAuthClientSecretExpiry"]
        meta = body.get("notificationMetadata", {})

        client_id = payload["clientId"]
        expiry_time = payload["clientSecretExpiryTime"]
        reason = payload.get("clientSecretExpiryReason", "UNSPECIFIED")
        application_id = meta.get("applicationId", "unknown")
        notification_id = meta.get("notificationId", "unknown")

        rotation.write_event(
            event_type="EXPIRY_WARNING",
            extra={
                "application_id": application_id,
                "client_id": client_id,
                "expiry_time": expiry_time,
                "reason": reason,
                "notification_id": notification_id,
            },
        )

        app = os.environ["APP_SHORT_NAME"]
        function_name = (
            f"{os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '<rotation-requester>')}"
        )
        rotation.send_alert(
            subject=f"[{app}] LWA client_secret expiry warning ({reason})",
            body=(
                f"App:               {app} ({application_id})\n"
                f"Client ID:         {client_id}\n"
                f"Expiry time:       {expiry_time}\n"
                f"Reason:            {reason}\n"
                f"Notification ID:   {notification_id}\n\n"
                "Per D3 policy C this Lambda does NOT auto-rotate. To rotate,\n"
                "manually invoke the RotationRequester Lambda for this app:\n\n"
                f"  aws lambda invoke --function-name {function_name.replace('ExpiryHandler', 'RotationRequester')} \\\n"
                "    --payload '{}' /tmp/out.json\n"
            ),
        )
        logger.info(
            "Logged + alerted expiry warning for app=%s client_id=%s expires=%s",
            app,
            client_id,
            expiry_time,
        )
