"""Tests for the ReportProcessor Lambda handler."""

import json

import boto3
import pytest


REPORT_ID = "REPORT-123"
DOCUMENT_ID = "amzn1.spdoc.1.3.test"
SELLER_ALIAS = "SH"
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


def test_happy_path_sends_ses_email(aws, mock_fetch, mocker):
    from handlers.report_processor import lambda_handler

    spy = mocker.spy(
        __import__("handlers.report_processor", fromlist=["notifications"]).notifications,
        "send_report_ready",
    )
    _seed_job()

    lambda_handler(_sqs_event(_notification()), None)

    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert kwargs["seller_alias"] == SELLER_ALIAS
    assert kwargs["report_type"] == REPORT_TYPE
    assert kwargs["report_id"] == REPORT_ID
    assert kwargs["report_size_bytes"] == len(REPORT_PAYLOAD)


def test_ses_failure_does_not_fail_lambda(aws, mock_fetch, mocker):
    """Email sending errors are logged; the job must still settle as COMPLETED
    and the Lambda must not re-raise (or SQS would redrive an already-written report)."""
    from handlers.report_processor import lambda_handler

    mocker.patch(
        "handlers.report_processor.notifications.send_report_ready",
        side_effect=RuntimeError("SES down"),
    )
    _seed_job()

    lambda_handler(_sqs_event(_notification()), None)  # must not raise

    job = _get_job()
    assert job["status"] == "COMPLETED"


def test_ses_skipped_when_recipients_empty(aws, mock_fetch, monkeypatch):
    """With SES_RECIPIENTS empty, the handler still marks the job COMPLETED —
    the unit-level check that no SES call happens lives in test_notifications."""
    from handlers.report_processor import lambda_handler

    monkeypatch.setenv("SES_RECIPIENTS", "")
    _seed_job()

    lambda_handler(_sqs_event(_notification()), None)

    assert _get_job()["status"] == "COMPLETED"


def test_notifications_returns_none_when_recipients_empty(aws, monkeypatch, mocker):
    """send_report_ready is a no-op when SES_RECIPIENTS is empty — no boto3 clients created."""
    from sincerelyhers_amazon import notifications

    monkeypatch.setenv("SES_RECIPIENTS", "")
    boto_spy = mocker.spy(notifications.boto3, "client")

    result = notifications.send_report_ready(
        bucket=BUCKET_NAME,
        key="some/key.tsv",
        seller_alias=SELLER_ALIAS,
        report_type=REPORT_TYPE,
        report_id=REPORT_ID,
        report_size_bytes=10,
    )

    assert result is None
    boto_spy.assert_not_called()


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


def test_unknown_report_is_skipped_not_failed(aws, mock_fetch):
    """REPORT_PROCESSING_FINISHED notifications for reports we didn't request
    (e.g., another client of the same SPP app) must be dropped, not written
    as FAILED, because that would pollute our job table with events we
    don't own. The DDB row for this report_id should not exist after the call."""
    from handlers.report_processor import lambda_handler

    # Deliberately do NOT seed a job row for REPORT_ID.
    lambda_handler(_sqs_event(_notification()), None)

    mock_fetch.assert_not_called()
    response = boto3.resource("dynamodb", region_name="us-east-2").Table(TABLE_NAME).get_item(
        Key={"report_id": REPORT_ID},
    )
    assert "Item" not in response


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
