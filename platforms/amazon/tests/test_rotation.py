"""Tests for the credential-rotation pipeline (D6: verify-then-write,
D7: old-secret monitoring, D3 policy C: alert-only ExpiryHandler)."""

import json
from datetime import datetime, timedelta, timezone

import boto3
import httpx
import pytest
from moto import mock_aws


APP_SHORT_NAME = "services"
SECRETS_PREFIX = "sp-api/sincerely-services"
ROTATION_TABLE = "test-amazon-services-rotation-events"
SES_SENDER = "test-sender@example.com"
SES_RECIPIENTS = "test-recipient@example.com"
REGION = "us-east-2"

APP_CREDS = {
    "client_id": "amzn1.application-oa2-client.test",
    "client_secret": "old-client-secret",
}
NEW_CLIENT_SECRET = "new-rotated-client-secret"


@pytest.fixture(autouse=True)
def _rotation_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("APP_SHORT_NAME", APP_SHORT_NAME)
    monkeypatch.setenv("SECRETS_PREFIX", SECRETS_PREFIX)
    monkeypatch.setenv("ROTATION_EVENTS_TABLE", ROTATION_TABLE)
    monkeypatch.setenv("SES_SENDER_EMAIL", SES_SENDER)
    monkeypatch.setenv("SES_RECIPIENTS", SES_RECIPIENTS)
    monkeypatch.setenv("EXPIRY_ALERT_HOURS", "24")


@pytest.fixture()
def aws_rotation():
    """Yield a moto-mocked env: rotation-events table + app/credentials secret + SES verified sender."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName=ROTATION_TABLE,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "event_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "event_id", "AttributeType": "S"},
                {"AttributeName": "app_short_name", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "by-app-and-time",
                    "KeySchema": [
                        {"AttributeName": "app_short_name", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )

        sm = boto3.client("secretsmanager", region_name=REGION)
        sm.create_secret(
            Name=f"{SECRETS_PREFIX}/app/credentials",
            SecretString=json.dumps(APP_CREDS),
        )

        ses = boto3.client("ses", region_name=REGION)
        ses.verify_email_identity(EmailAddress=SES_SENDER)

        yield


def _expiry_sqs_event(**overrides) -> dict:
    payload = {
        "clientId": APP_CREDS["client_id"],
        "clientSecretExpiryTime": "2026-05-12T08:00:00Z",
        "clientSecretExpiryReason": "PERIODIC_ROTATION",
    }
    payload.update(overrides)
    body = {
        "notificationVersion": "1.0",
        "notificationType": "APPLICATION_OAUTH_CLIENT_SECRET_EXPIRY",
        "payloadVersion": "1.0",
        "eventTime": "2026-05-05T08:00:00Z",
        "payload": {"applicationOAuthClientSecretExpiry": payload},
        "notificationMetadata": {
            "applicationId": "amzn1.sp.app.test",
            "notificationId": "test-notif-id-1",
        },
    }
    return {"Records": [{"messageId": "m1", "body": json.dumps(body)}]}


def _new_secret_sqs_event(new_secret: str = NEW_CLIENT_SECRET, **overrides) -> dict:
    payload = {
        "clientId": APP_CREDS["client_id"],
        "newClientSecret": new_secret,
        "oldClientSecretExpiryTime": (
            datetime.now(timezone.utc) + timedelta(days=7)
        ).isoformat(),
    }
    payload.update(overrides)
    body = {
        "notificationVersion": "1.0",
        "notificationType": "APPLICATION_OAUTH_CLIENT_NEW_SECRET",
        "payloadVersion": "1.0",
        "eventTime": "2026-05-05T08:30:00Z",
        "payload": {"applicationOAuthClientNewSecret": payload},
        "notificationMetadata": {
            "applicationId": "amzn1.sp.app.test",
            "notificationId": "test-notif-id-2",
        },
    }
    return {"Records": [{"messageId": "m1", "body": json.dumps(body)}]}


def _scan_events() -> list[dict]:
    table = boto3.resource("dynamodb", region_name=REGION).Table(ROTATION_TABLE)
    return table.scan()["Items"]


# ---------- ExpiryHandler (D3 policy C — alert-only) ----------


def test_expiry_writes_event_and_does_not_call_rotate(aws_rotation, mocker):
    from handlers.expiry_handler import lambda_handler

    grantless = mocker.patch("handlers.expiry_handler.rotation.grantless_access_token")

    lambda_handler(_expiry_sqs_event(), None)

    grantless.assert_not_called()
    rows = _scan_events()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "EXPIRY_WARNING"
    assert rows[0]["app_short_name"] == APP_SHORT_NAME
    assert rows[0]["reason"] == "PERIODIC_ROTATION"


# ---------- RotationRequester ----------


def test_requester_204_writes_event(aws_rotation, mocker):
    from handlers.rotation_requester import lambda_handler

    fake_response = mocker.Mock(spec=httpx.Response)
    fake_response.status_code = 204
    mocker.patch(
        "handlers.rotation_requester.rotation.grantless_access_token",
        return_value="ya29.test-access-token",
    )
    rotate = mocker.patch(
        "handlers.rotation_requester.rotation.call_rotate",
        return_value=fake_response,
    )

    result = lambda_handler({}, None)

    rotate.assert_called_once_with("ya29.test-access-token")
    assert result["status"] == "REQUESTED"

    rows = _scan_events()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "ROTATION_REQUESTED"
    assert rows[0]["client_id"] == APP_CREDS["client_id"]


def test_requester_non_204_raises_and_records_failure(aws_rotation, mocker):
    from handlers.rotation_requester import lambda_handler

    fake_response = mocker.Mock(spec=httpx.Response)
    fake_response.status_code = 429
    fake_response.text = "Too Many Requests"
    mocker.patch(
        "handlers.rotation_requester.rotation.grantless_access_token",
        return_value="t",
    )
    mocker.patch(
        "handlers.rotation_requester.rotation.call_rotate",
        return_value=fake_response,
    )

    with pytest.raises(RuntimeError):
        lambda_handler({}, None)

    rows = _scan_events()
    assert any(r["event_type"] == "ROTATION_REQUEST_FAILED" for r in rows)


# ---------- CredentialRotationProcessor (D6 — verify-then-write) ----------


def test_processor_verifies_then_writes_secret_and_records_completion(
    aws_rotation, mocker
):
    """Happy path: new secret exchanges for an LWA token, gets persisted,
    and a ROTATION_COMPLETED row is written to DynamoDB."""
    from handlers.credential_rotation_processor import lambda_handler

    grantless = mocker.patch(
        "handlers.credential_rotation_processor.rotation.grantless_access_token",
        return_value="verified-access-token",
    )

    lambda_handler(_new_secret_sqs_event(), None)

    grantless.assert_called_once()
    # Critical: verification used the NEW secret, not the existing one
    assert grantless.call_args.kwargs["client_secret"] == NEW_CLIENT_SECRET

    # Secret in Secrets Manager has been updated
    sm = boto3.client("secretsmanager", region_name=REGION)
    persisted = json.loads(
        sm.get_secret_value(SecretId=f"{SECRETS_PREFIX}/app/credentials")[
            "SecretString"
        ]
    )
    assert persisted["client_secret"] == NEW_CLIENT_SECRET
    assert persisted["client_id"] == APP_CREDS["client_id"]

    rows = _scan_events()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "ROTATION_COMPLETED"


def test_processor_verification_failure_preserves_old_secret(aws_rotation, mocker):
    """D6: verification before write. If the new secret can't exchange for an
    LWA token, the old secret stays put and a VERIFICATION_FAILED row is
    written. The handler re-raises so SQS redrives -> eventually DLQ."""
    from handlers.credential_rotation_processor import lambda_handler

    mocker.patch(
        "handlers.credential_rotation_processor.rotation.grantless_access_token",
        side_effect=httpx.HTTPStatusError(
            "401",
            request=mocker.Mock(spec=httpx.Request),
            response=mocker.Mock(spec=httpx.Response),
        ),
    )

    with pytest.raises(httpx.HTTPStatusError):
        lambda_handler(_new_secret_sqs_event(new_secret="bogus"), None)

    sm = boto3.client("secretsmanager", region_name=REGION)
    persisted = json.loads(
        sm.get_secret_value(SecretId=f"{SECRETS_PREFIX}/app/credentials")[
            "SecretString"
        ]
    )
    assert persisted["client_secret"] == APP_CREDS["client_secret"]  # unchanged

    rows = _scan_events()
    assert any(r["event_type"] == "ROTATION_VERIFICATION_FAILED" for r in rows)


# ---------- OldSecretMonitor (D7) ----------


def test_monitor_alerts_when_old_secret_expires_within_threshold(aws_rotation, mocker):
    """A ROTATION_COMPLETED row whose old_secret_expires_at is within the
    threshold triggers a single SES alert."""
    from sincerelyhers_amazon import rotation

    # Seed a completed rotation whose old secret expires in ~12 hours.
    rotation.write_event(
        event_type="ROTATION_COMPLETED",
        extra={
            "client_id": APP_CREDS["client_id"],
            "old_secret_expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=12)
            ).isoformat(),
        },
    )

    alert = mocker.patch("sincerelyhers_amazon.rotation.send_alert")

    from handlers.old_secret_monitor import lambda_handler

    lambda_handler({}, None)

    alert.assert_called_once()
    subject = alert.call_args.kwargs["subject"]
    assert APP_SHORT_NAME in subject
    assert "1 old-secret" in subject


def test_monitor_silent_when_no_rotations_at_risk(aws_rotation, mocker):
    """A ROTATION_COMPLETED row outside the alert window must NOT trigger
    an alert. Catches the most likely regression: alerting on every cron fire."""
    from sincerelyhers_amazon import rotation

    rotation.write_event(
        event_type="ROTATION_COMPLETED",
        extra={
            "client_id": APP_CREDS["client_id"],
            "old_secret_expires_at": (
                datetime.now(timezone.utc) + timedelta(days=6)
            ).isoformat(),
        },
    )

    alert = mocker.patch("sincerelyhers_amazon.rotation.send_alert")

    from handlers.old_secret_monitor import lambda_handler

    lambda_handler({}, None)

    alert.assert_not_called()
