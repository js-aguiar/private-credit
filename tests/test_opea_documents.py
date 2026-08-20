"""Tests for Opea document URL normalization."""

from shared.opea_documents import normalize_opea_document_url, opea_file_id


def test_normalize_opea_document_url_strips_presigned_query():
    url = (
        "https://bkt-opea-outros-bau-sistemas-cedoc.s3.sa-east-1.amazonaws.com/"
        "files/db12c5a3-bc6a-41b4-b55a-e08502f97bb5.pdf"
        "?X-Amz-Expires=2400&X-Amz-Signature=abc"
    )
    assert (
        normalize_opea_document_url(url)
        == "https://bkt-opea-outros-bau-sistemas-cedoc.s3.sa-east-1.amazonaws.com/"
        "files/db12c5a3-bc6a-41b4-b55a-e08502f97bb5.pdf"
    )


def test_opea_file_id_reads_cedoc_uuid():
    assert opea_file_id({"id": "a0dc4335-05c6-44cf-9d83-ec6b8784cfd0"}) == (
        "a0dc4335-05c6-44cf-9d83-ec6b8784cfd0"
    )
    assert opea_file_id({}) is None
