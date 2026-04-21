"""SP-API report-document retrieval: get the download URL, fetch, gunzip if needed."""

import gzip
import urllib.request
from typing import Optional

from sp_api.api import Reports
from sp_api.base import Marketplaces


def fetch_document(report_document_id: str, creds: dict) -> bytes:
    """Call ``get_report_document`` and return the fully-decompressed payload."""
    api = Reports(marketplace=Marketplaces.US, credentials=creds)
    response = api.get_report_document(report_document_id)
    url: str = response.payload["url"]
    compression: Optional[str] = response.payload.get("compressionAlgorithm")
    return _download_and_decompress(url, compression)


def _download_and_decompress(url: str, compression: Optional[str]) -> bytes:
    with urllib.request.urlopen(url) as http:
        raw = http.read()
    if compression == "GZIP":
        raw = gzip.decompress(raw)
    return raw
