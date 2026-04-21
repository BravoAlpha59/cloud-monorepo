"""Tests for the ReportProcessor Lambda handler."""

import json

import boto3
import pytest


REPORT_ID = "REPORT-123"
DOCUMENT_ID = "amzn1.spdoc.1.3.test"
SELLER_ALIAS = "sincerely-hers"
REPORT_TYPE = "GET_FLAT_FILE_OPEN_LISTINGS_DATA"
MARKETPLACE_ID = "ATVPDKIKX0DER"
TABLE_NAME = "test-amazon-report-jobs"
BUCKET_NAME = "test-sincerelyhers-reports-dev"

REPORT_PAYLOAD = b"sku\tasin\tprice\nABC-1\tB00TEST\t19.99\n"


def _notification(**overrides) -> dict:
    base = {
        "sellerId": "A1EXAMPLE",
        "accountId": "A1EXAMPLE",
        "reportId": REPORT_ID,
        "reportType": REPORT_TYPE,
        "processingStatus": "DONE",
        "reportDocumentId": DOCUMENT_ID,
    }
    base.update(overrides)
    return base


def _sqs_event(notification: dict) -> dict:
    body = {
        "notificationType": "REPORT_PROCESSING_FINISHED",
        "eventTime": "2026-04-21T08:30:00.000Z",
        "payload": {"reportProcessingFinishedNotification": notification},
    }
    return {
        "Records": [
            {
                "messageId": "test-message-id",
                "body": json.dumps(body),
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:us-east-2:431412299701:dev-sp-api-report-ready",
                "awsRegion": "us-east-2",
            }
        ]
    }


def _seed_job() -> None:
    table = boto3.resource("dynamodb", region_name="us-east-2").Table(TABLE_NAME)
    table.put_item(
        Item={
            "report_id": REPORT_ID,
            "seller_alias": SELLER_ALIAS,
            "report_type": REPORT_TYPE,
            "marketplace_id": MARKETPLACE_ID,
            "status": "REQUESTED",
            "requested_at": "2026-04-21T08:00:00+00:00",
        }
    )


def _get_job() -> dict:
    table = boto3.resource("dynamodb", region_name="us-east-2").Table(TABLE_NAME)
    return table.get_item(Key={"report_id": REPORT_ID})["Item"]


@pytest.fixture()
def mock_fetch(mocker):
    """Patch report_document.fetch_document so no real SP-API/network call happens."""
    return mocker.patch(
        "handlers.report_processor.report_document.fetch_document",
        return_value=REPORT_PAYLOAD,
    )


def test_happy_path_writes_s3_and_marks_completed(aws, mock_fetch):
    from handlers.report_processor import lambda_handler

    _seed_job()

    lambda_handler(_sqs_event(_notification()), None)

    mock_fetch.assert_called_once_with(DOCUMENT_ID, mocker_creds())

    job = _get_job()
    assert job["status"] == "COMPLETED"
    assert job["document_id"] == DOCUMENT_ID
    assert "completed_at" in job
    expected_prefix = (
        f"amazon/sincerely-services/{SELLER_ALIAS}/{REPORT_TYPE}/"
    )
    assert job["s3_key"].startswith(expected_prefix)
    assert job["s3_key"].endswith(f"{REPORT_ID}.tsv")

    s3 = boto3.client("s3", region_name="us-east-2")
    obj = s3.get_object(Bucket=BUCKET_NAME, Key=job["s3_key"])
    assert obj["Body"].read() == REPORT_PAYLOAD


def test_non_done_status_marks_failed_and_skips_download(aws, mock_fetch):
    from handlers.report_processor import lambda_handler

    _seed_job()

    lambda_handler(_sqs_event(_notification(processingStatus="CANCELLED")), None)

    mock_fetch.assert_not_called()
    job = _get_job()
    assert job["status"] == "FAILED"
    assert "CANCELLED" in job["error_message"]


def test_missing_document_id_marks_failed(aws, mock_fetch):
    from handlers.report_processor import lambda_handler

    _seed_job()

    notification = _notification()
    notification.pop("reportDocumentId")
    lambda_handler(_sqs_event(notification), None)

    mock_fetch.assert_not_called()
    job = _get_job()
    assert job["status"] == "FAILED"
    assert "reportDocumentId" in job["error_message"]


def test_download_failure_marks_failed_and_reraises(aws, mock_fetch):
    from handlers.report_processor import lambda_handler

    _seed_job()
    mock_fetch.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        lambda_handler(_sqs_event(_notification()), None)

    job = _get_job()
    assert job["status"] == "FAILED"
    assert job["error_message"] == "boom"


def test_s3_key_uses_report_type_extension(aws, mock_fetch):
    from handlers.report_processor import lambda_handler

    # Seed with an XML report type so the handler picks .xml instead of .tsv.
    table = boto3.resource("dynamodb", region_name="us-east-2").Table(TABLE_NAME)
    table.put_item(
        Item={
            "report_id": REPORT_ID,
            "seller_alias": SELLER_ALIAS,
            "report_type": "GET_XML_BROWSE_TREE_DATA",
            "marketplace_id": MARKETPLACE_ID,
            "status": "REQUESTED",
            "requested_at": "2026-04-21T08:00:00+00:00",
        }
    )

    lambda_handler(
        _sqs_event(_notification(reportType="GET_XML_BROWSE_TREE_DATA")),
        None,
    )

    job = _get_job()
    assert job["s3_key"].endswith(f"{REPORT_ID}.xml")


def mocker_creds() -> dict:
    """Expected creds-dict shape after credentials.get_sp_api_credentials resolves the test secret."""
    return {
        "lwa_app_id": "amzn1.application-oa2-client.test",
        "lwa_client_secret": "test-client-secret",
        "refresh_token": "Atzr|test-refresh-token",
    }
