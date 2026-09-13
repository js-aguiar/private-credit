"""Tests for Opea document URL normalization and open-link refresh."""

from __future__ import annotations

from shared.opea_documents import (
    normalize_opea_document_url,
    opea_file_id,
    refresh_opea_presigned_url,
)


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


def test_refresh_opea_presigned_url_matches_file_id(monkeypatch):
    children = [
        {
            "id": "file-1",
            "url": (
                "https://bkt-opea-outros-bau-sistemas-cedoc.s3.sa-east-1.amazonaws.com/"
                "files/aaa.pdf?X-Amz-Signature=one"
            ),
        },
        {
            "id": "file-2",
            "url": (
                "https://bkt-opea-outros-bau-sistemas-cedoc.s3.sa-east-1.amazonaws.com/"
                "files/bbb.pdf?X-Amz-Signature=two"
            ),
        },
    ]
    monkeypatch.setattr(
        "shared.opea_documents._cedoc_children",
        lambda id_cedoc, timeout_seconds: children,
    )
    url = refresh_opea_presigned_url(id_cedoc="cedoc-1", file_id="file-2")
    assert url.endswith("bbb.pdf?X-Amz-Signature=two")


def test_refresh_opea_presigned_url_matches_stored_path(monkeypatch):
    children = [
        {
            "id": "file-9",
            "url": (
                "https://bkt-opea-outros-bau-sistemas-cedoc.s3.sa-east-1.amazonaws.com/"
                "files/ccc.pdf?X-Amz-Signature=fresh"
            ),
        }
    ]
    monkeypatch.setattr(
        "shared.opea_documents._cedoc_children",
        lambda id_cedoc, timeout_seconds: children,
    )
    stored = (
        "https://bkt-opea-outros-bau-sistemas-cedoc.s3.sa-east-1.amazonaws.com/files/ccc.pdf"
    )
    url = refresh_opea_presigned_url(id_cedoc="cedoc-1", stored_url=stored)
    assert "X-Amz-Signature=fresh" in url
