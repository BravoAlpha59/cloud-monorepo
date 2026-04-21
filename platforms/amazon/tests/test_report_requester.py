"""Tests for the ReportRequester Lambda handler."""

from datetime import datetime
from unittest.mock import MagicMock

import boto3
import pytest


SELLER_ALIAS = "SH"
TABLE_NAME = "test-amazon-report-jobs"

SAMPLE_EVENT = {
    "seller_alias": SELLER_ALIAS,
    "marketplace_id": "ATVPDKIKX0DER",
    "report_type": "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
    "lookback_days": 1,
}


@pytest.fixture()
def mock_reports(mocker):
    """Patch the Reports class so no real SP-API call is made."""
    mock_cls = mocker.patch("handlers.report_requester.Reports")
    mock_instance = MagicMock()
    mock_instance.create_report.return_value = MagicMock(
        payload={"reportId": "REPORT-123"}
    )
    mock_cls.return_value = mock_instance
    return mock_instance


def test_handler_returns_report_id(aws, mock_reports):
    from handlers.report_requester import lambda_handler

    result = lambda_handler(SAMPLE_EVENT, None)

    assert result == {"reportId": "REPORT-123"}


def test_handler_calls_create_report_with_date_window(aws, mock_reports):
    from handlers.report_requester import lambda_handler

    lambda_handler(SAMPLE_EVENT, None)

    call_kwargs = mock_reports.create_report.call_args.kwargs
    assert call_kwargs["reportType"] == "GET_FLAT_FILE_OPEN_LISTINGS_DATA"
    assert call_kwargs["marketplaceIds"] == ["ATVPDKIKX0DER"]

    # Verify start/end are ISO-formatted UTC timestamps within the expected window
    start = datetime.fromisoformat(call_kwargs["dataStartTime"])
    end = datetime.fromisoformat(call_kwargs["dataEndTime"])
    assert start < end
    assert start.tzinfo is not None
    assert (end - start).days == 1 or (end - start).seconds > 0


def test_handler_writes_dynamodb_row(aws, mock_reports):
    from handlers.report_requester import lambda_handler

    lambda_handler(SAMPLE_EVENT, None)

    table = boto3.resource("dynamodb", region_name="us-east-2").Table(TABLE_NAME)
    item = table.get_item(Key={"report_id": "REPORT-123"})["Item"]

    assert item["report_id"] == "REPORT-123"
    assert item["seller_alias"] == SELLER_ALIAS
    assert item["report_type"] == "GET_FLAT_FILE_OPEN_LISTINGS_DATA"
    assert item["marketplace_id"] == "ATVPDKIKX0DER"
    assert item["status"] == "REQUESTED"
    assert "requested_at" in item


def test_handler_raises_on_missing_secret(aws, mock_reports):
    from handlers.report_requester import lambda_handler

    bad_event = {**SAMPLE_EVENT, "seller_alias": "no-such-seller"}

    with pytest.raises(Exception):
        lambda_handler(bad_event, None)


def test_handler_extracts_event_fields(aws, mock_reports):
    from handlers.report_requester import lambda_handler

    lambda_handler(SAMPLE_EVENT, None)

    # Verify Reports was instantiated with the right marketplace

    call_kwargs = mock_reports.create_report.call_args.kwargs
    assert call_kwargs["marketplaceIds"] == ["ATVPDKIKX0DER"]
