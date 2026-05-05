"""Shared utilities for the credential-rotation pipeline.

Three responsibilities, all reused across the four rotation Lambdas:

* LWA grantless token exchange — used both to call the SP-API rotation
  endpoint (RotationRequester) and to verify a freshly-rotated
  client_secret before persisting it (CredentialRotationProcessor, D6).
* DynamoDB row writes against the rotation-events table — single source
  of truth for the audit trail.
* SES alert emails — operator-facing notifications for each pipeline
  state transition.

App-level credentials live at ``SECRETS_PREFIX/app/credentials`` with
``client_id`` and ``client_secret`` keys. See ``credentials.py`` for the
seller-side reader.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import boto3
import httpx


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SP_API_ENDPOINT = "https://sellingpartnerapi-na.amazon.com"

ROTATE_SCOPE = "sellingpartnerapi::client_credential:rotate"
ROTATE_PATH = "/applications/2023-11-30/clientSecret"


def app_credentials_secret_id() -> str:
    return f"{os.environ['SECRETS_PREFIX']}/app/credentials"


def read_app_credentials() -> dict:
    """Return ``{client_id, client_secret}`` from the app-level secret."""
    sm = boto3.client("secretsmanager")
    raw = sm.get_secret_value(SecretId=app_credentials_secret_id())["SecretString"]
    return json.loads(raw)


def grantless_access_token(client_id: str, client_secret: str, scope: str) -> str:
    """Exchange app credentials for a grantless LWA access token.

    Used for (a) calling rotateApplicationClientSecret and (b) verifying a
    freshly-rotated client_secret before persisting it.
    """
    response = httpx.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "scope": scope,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def call_rotate(access_token: str) -> httpx.Response:
    """POST /applications/2023-11-30/clientSecret. Returns the raw response.

    Successful response is HTTP 204 No Content. The new client_secret is
    delivered asynchronously via the new-secret SQS queue.
    """
    return httpx.post(
        f"{SP_API_ENDPOINT}{ROTATE_PATH}",
        headers={"x-amz-access-token": access_token},
        timeout=30.0,
    )


def write_event(
    *,
    event_type: str,
    extra: Optional[dict] = None,
) -> dict:
    """Insert a row in the rotation-events table.

    Returns the full item written so handlers can quote the event_id in
    logs / alerts. Single-PK schema (event_id) plus a GSI over
    (app_short_name, created_at) — see rotation-template.yaml.
    """
    table_name = os.environ["ROTATION_EVENTS_TABLE"]
    app = os.environ["APP_SHORT_NAME"]
    now = datetime.now(timezone.utc).isoformat()

    item = {
        "event_id": str(uuid.uuid4()),
        "app_short_name": app,
        "event_type": event_type,
        "created_at": now,
    }
    if extra:
        item.update(extra)

    boto3.resource("dynamodb").Table(table_name).put_item(Item=item)
    logger.info(
        "Wrote rotation-events row %s (%s, app=%s)", item["event_id"], event_type, app
    )
    return item


def send_alert(*, subject: str, body: str) -> Optional[str]:
    """Send an operator alert via SES. No-op if SES_RECIPIENTS is empty.

    Failures are logged but never re-raised — an alert outage must not
    cause a handler to fail and trigger SQS redrive of an event we've
    already processed in DynamoDB.
    """
    recipients = _recipient_list()
    if not recipients:
        logger.info("SES_RECIPIENTS unset; skipping alert: %s", subject)
        return None

    sender = os.environ["SES_SENDER_EMAIL"]
    try:
        response = boto3.client("ses").send_email(
            Source=sender,
            Destination={"ToAddresses": recipients},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )
        return response["MessageId"]
    except Exception:
        logger.exception("SES alert failed: %s", subject)
        return None


def _recipient_list() -> list[str]:
    raw = os.environ.get("SES_RECIPIENTS", "")
    return [r.strip() for r in raw.split(",") if r.strip()]
