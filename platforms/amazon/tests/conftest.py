"""Shared fixtures for Amazon platform tests."""

import json

import boto3
import pytest
from moto import mock_aws


SELLER_ALIAS = "sincerely-hers"
SECRETS_PREFIX = "sp-api/sincerely-services"
TABLE_NAME = "test-amazon-report-jobs"

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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    monkeypatch.setenv("REPORT_JOBS_TABLE", TABLE_NAME)
    monkeypatch.setenv("SECRETS_PREFIX", SECRETS_PREFIX)


@pytest.fixture()
def aws(monkeypatch):
    """Yield a moto-mocked AWS session with DynamoDB table + secret pre-seeded."""
    with mock_aws():
        # DynamoDB table
        ddb = boto3.resource("dynamodb", region_name="us-east-2")
        ddb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "report_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "report_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Secrets Manager secret
        sm = boto3.client("secretsmanager", region_name="us-east-2")
        sm.create_secret(
            Name=f"{SECRETS_PREFIX}/{SELLER_ALIAS}/credentials",
            SecretString=json.dumps(SECRET_PAYLOAD),
        )

        yield
