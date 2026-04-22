"""Shared fixtures for Amazon platform tests."""

import json

import boto3
import pytest
from moto import mock_aws


SELLER_ALIAS = "SH"
SECRETS_PREFIX = "sp-api/sincerely-services"
TABLE_NAME = "test-amazon-report-jobs"
BUCKET_NAME = "test-sincerelyhers-reports-dev"
REGION = "us-east-2"
SES_SENDER = "test-sender@example.com"
SES_RECIPIENTS = "test-recipient@example.com"

SECRET_PAYLOAD = {
    "client_id": "amzn1.application-oa2-client.test",
    "client_secret": "test-client-secret",
    "refresh_token": "Atzr|test-refresh-token",
}


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch):
    """Point code at moto and set required env vars."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("REPORT_JOBS_TABLE", TABLE_NAME)
    monkeypatch.setenv("SECRETS_PREFIX", SECRETS_PREFIX)
    monkeypatch.setenv("REPORTS_BUCKET", BUCKET_NAME)
    monkeypatch.setenv("SES_SENDER_EMAIL", SES_SENDER)
    monkeypatch.setenv("SES_RECIPIENTS", SES_RECIPIENTS)


@pytest.fixture()
def aws():
    """Yield a moto-mocked AWS session with table, secret, and bucket pre-seeded."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "report_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "report_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        sm = boto3.client("secretsmanager", region_name=REGION)
        sm.create_secret(
            Name=f"{SECRETS_PREFIX}/{SELLER_ALIAS}/credentials",
            SecretString=json.dumps(SECRET_PAYLOAD),
        )

        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(
            Bucket=BUCKET_NAME,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )

        ses = boto3.client("ses", region_name=REGION)
        ses.verify_email_identity(EmailAddress=SES_SENDER)

        yield
