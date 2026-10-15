"""Bounded file preview → canonical ingestion → scoped source-referenced retrieval."""

from __future__ import annotations

import hashlib
import io
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from xml.sax.saxutils import escape

import httpx
import jwt
import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.knowledge_file_route import MAX_MULTIPART_BYTES
from app.api.routes import knowledge as routes
from app.core.auth import get_current_tenant
from app.core.config import Settings, get_settings
from app.core.dependencies import get_rag_service
from app.core.errors import (
    AuthError,
    ServiceUnavailableError,
    ValidationAppError,
    register_exception_handlers,
)
from app.db.base import Base
from app.db.models import Chunk, Document, Organization, User, UserStrategy
from app.db.session import get_session
from app.providers.embeddings import MockEmbeddingsProvider
from app.providers.qdrant import InMemoryVectorStore
from app.rag.file_import_worker import MAX_FILE_BYTES, MAX_TEXT_CHARACTERS
from app.rag.text_processing import compute_source_hash
from app.schemas.common import DocumentSourceType, MembershipRole
from app.schemas.rag import IngestDocumentRequest, RagQuery
from app.security.tenant import TenantContext
from app.services import knowledge_file_import as file_service
from app.services.knowledge_file_import import preview_file, verify_file_save
from app.services.rag_service import RagService
from tests.support.knowledge_indexing import drain_indexing

ORG_A, ORG_B, USER_A, USER_A2, USER_B = (uuid4() for _ in range(5))
TEXT = "SYNTHETIC IMPORT\n\nWait for confirmed evidence. Risk BLOCK remains authoritative."
SETTINGS = Settings(
    _env_file=None,
    provider_mode="mock",
    environment="local",
    database_url="sqlite+pysqlite:///:memory:",
    jwt_secret="knowledge-local-synthetic-jwt-secret-32-characters",
    execution_mode="paper",
    enable_real_trading=False,
    rate_limit_use_redis=False,
    market_data_cache_use_redis=False,
)


def docx_bytes(text: str = TEXT) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body></w:document>',
        )
    return stream.getvalue()


def pdf_bytes(*, text: str | None = TEXT, pages: int = 1, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        if text is not None:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            content = DecodedStreamObject()
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content.set_data(f"BT /F1 12 Tf 10 10 Td ({safe}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(content)
    if encrypted:
        writer.encrypt("synthetic-only")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def preview(content: bytes = TEXT.encode(), *, filename: str = "rules.txt", **updates):
    values = dict(  # noqa: C408
        filename=filename,
        media_type=None,
        title="Synthetic rules",
        source_type=DocumentSourceType.RISK_POLICY,
        organization_id=ORG_A,
        user_id=USER_A,
        settings=SETTINGS,
    )
    values.update(updates)
    return preview_file(content, **values)


def save_preview(content: bytes, result, **updates):
    values = dict(  # noqa: C408
        filename=result.filename,
        media_type=result.media_type,
        title=result.title,
        source_type=result.source_type,
        organization_id=ORG_A,
        user_id=USER_A,
        settings=SETTINGS,
        preview_receipt=result.preview_receipt,
        confirm=True,
    )
    values.update(updates)
    return verify_file_save(content, **values)


@pytest.mark.parametrize(
    "filename,content,expected",
    [
        ("rules.txt", TEXT.encode(), "Risk BLOCK"),
        ("rules.md", ("# Rules\n\n" + TEXT).encode(), "# Rules"),
        ("rules.docx", docx_bytes(), "Risk BLOCK"),
        ("rules.pdf", pdf_bytes(), "Risk BLOCK"),
        ("unicode.txt", "Правила рынка".encode("utf-16"), "Правила"),
    ],
)
def test_supported_file_preview_never_claims_storage_and_preserves_byte_hash(
    filename, content, expected
):
    result = preview(content, filename=filename)
    assert expected in result.extracted_text
    assert result.raw_content_hash == hashlib.sha256(content).hexdigest()
    assert result.extracted_text_hash == hashlib.sha256(result.extracted_text.encode()).hexdigest()
    assert not result.saved and result.vector_index_status == "not_started"
    text, provenance = save_preview(content, result)
    assert text == result.extracted_text
    assert provenance.filename == filename
    assert provenance.byte_size == len(content)
    assert provenance.parser_version == "knowledge-file/v1"


@pytest.mark.parametrize(
    "filename,content,message",
    [
        ("empty.txt", b"", "nonempty"),
        ("large.txt", b"a" * (MAX_FILE_BYTES + 1), "5 MiB"),
        ("long.md", b"a" * (MAX_TEXT_CHARACTERS + 1), "100,000"),
        ("binary.txt", b"\x00\x01content", "binary"),
        ("invalid.txt", b"\xffrandom", "UTF-8"),
        ("archive.docx", b"broken zip", "malformed"),
        ("empty.docx", docx_bytes(""), "no readable"),
        ("broken.pdf", b"%PDF-1.4\nnot a PDF", "malformed"),
        ("scanned.pdf", pdf_bytes(text=None), "Scanned PDFs need OCR"),
        ("encrypted.pdf", pdf_bytes(encrypted=True), "Encrypted PDFs"),
        ("many-pages.pdf", pdf_bytes(pages=101, text=None), "100-page"),
        ("script.html", b"text", "TXT"),
    ],
)
def test_malformed_unsupported_empty_and_oversize_files_fail_clearly(filename, content, message):
    with pytest.raises(ValidationAppError, match=message):
        preview(content, filename=filename)


def test_docx_expansion_and_entities_refused_without_unbounded_parsing():
    with pytest.raises(ValidationAppError, match="16 MiB"):
        preview(docx_bytes("a" * (17 * 1024 * 1024)), filename="bomb.docx")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml", '<!DOCTYPE doc [<!ENTITY e "expanded">]><doc>&e;</doc>'
        )
    with pytest.raises(ValidationAppError, match="malformed"):
        preview(stream.getvalue(), filename="entity.docx")


def test_docx_missing_body_or_malformed_content_types_refused():
    for content_types, body in [
        ("not XML", "<w:body><w:p><w:r><w:t>Text</w:t></w:r></w:p></w:body>"),
        ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>', ""),
    ]:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr(
                "word/document.xml",
                f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">{body}</w:document>',
            )
        with pytest.raises(ValidationAppError, match=r"malformed|missing"):
            preview(stream.getvalue(), filename="broken.docx")


def test_docx_table_text_and_mixed_pdf_pages_are_truthfully_previewed():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table risk rule</w:t></w:r></w:p>"
            "</w:tc></w:tr></w:tbl></w:body></w:document>",
        )
        archive.writestr("word/header1.xml", "Header is deliberately excluded")
    docx = preview(stream.getvalue(), filename="table.docx")
    assert docx.extracted_text == "Table risk rule"
    assert "Headers" in docx.warnings[0]
    writer = PdfWriter()
    writer.append(io.BytesIO(pdf_bytes()))
    writer.add_blank_page(width=612, height=792)
    output = io.BytesIO()
    writer.write(output)
    pdf = preview(output.getvalue(), filename="mixed.pdf")
    assert "Risk BLOCK" in pdf.extracted_text
    assert any("1 PDF pages had no selectable text" in warning for warning in pdf.warnings)


def test_declared_file_type_must_match_extension():
    with pytest.raises(ValidationAppError, match="does not match"):
        preview(TEXT.encode(), media_type="application/pdf")


@pytest.mark.parametrize(
    "change",
    ["bytes", "title", "category", "filename", "user", "org", "expired", "confirm", "tampered"],
)
def test_save_requires_the_exact_unexpired_principal_scoped_review(change):
    content = TEXT.encode()
    result = preview(content)
    updates = {}
    if change == "bytes":
        content += b" changed"
    elif change == "title":
        updates["title"] = "Changed title"
    elif change == "category":
        updates["source_type"] = DocumentSourceType.GENERAL_NOTE
    elif change == "filename":
        updates["filename"] = "renamed.txt"
    elif change == "user":
        updates["user_id"] = USER_A2
    elif change == "org":
        updates["organization_id"] = ORG_B
    elif change == "confirm":
        updates["confirm"] = False
    elif change == "tampered":
        updates["preview_receipt"] = result.preview_receipt + "invalid"
    elif change == "expired":
        payload = jwt.decode(
            result.preview_receipt,
            SETTINGS.jwt_secret,
            algorithms=["HS256"],
            audience="knowledge-file-preview",
        )
        payload["exp"] = datetime.now(UTC) - timedelta(seconds=1)
        updates["preview_receipt"] = jwt.encode(payload, SETTINGS.jwt_secret, algorithm="HS256")
    with pytest.raises(ValidationAppError):
        save_preview(content, result, **updates)


def test_parser_timeout_and_capacity_fail_without_waiting_or_leaking_content(monkeypatch):
    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic-parser", 8)

    monkeypatch.setattr(file_service.subprocess, "run", time_out)
    with pytest.raises(ValidationAppError, match="time limit"):
        preview()
    assert file_service._PARSER_SLOTS.acquire(blocking=False)
    assert file_service._PARSER_SLOTS.acquire(blocking=False)
    try:
        with pytest.raises(ServiceUnavailableError, match="busy"):
            preview()
    finally:
        file_service._PARSER_SLOTS.release()
        file_service._PARSER_SLOTS.release()


@pytest.fixture
def knowledge_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add_all(
            [
                Organization(id=ORG_A, name="A"),
                Organization(id=ORG_B, name="B"),
                User(id=USER_A, email="file-a@test.example", hashed_password="hash"),
                User(id=USER_A2, email="file-a2@test.example", hashed_password="hash"),
                User(id=USER_B, email="file-b@test.example", hashed_password="hash"),
            ]
        )
        session.commit()
        vectors = InMemoryVectorStore()
        service = RagService(
            session, settings=SETTINGS, embeddings=MockEmbeddingsProvider(), vector_store=vectors
        )
        yield session, service, vectors
    engine.dispose()


def ingest_file(
    service,
    *,
    user=USER_A,
    org=ORG_A,
    title="Synthetic rules",
    filename="rules.md",
    source_type=DocumentSourceType.RISK_POLICY,
):
    content = TEXT.encode()
    shown = preview(
        content,
        filename=filename,
        title=title,
        source_type=source_type,
        organization_id=org,
        user_id=user,
    )
    text, provenance = save_preview(content, shown, organization_id=org, user_id=user)
    return service.ingest(
        IngestDocumentRequest(
            organization_id=org, user_id=user, title=title, text=text, source_type=source_type
        ),
        file_provenance=provenance,
    )


def test_canonical_storage_duplicates_and_retrieval_have_principal_and_source_lineage(knowledge_db):
    session, service, _vectors = knowledge_db
    first = ingest_file(service)
    repeat = ingest_file(service, title="Renamed title", filename="renamed.md")
    other_user = ingest_file(service, user=USER_A2)
    other_org = ingest_file(service, user=USER_B, org=ORG_B)
    assert repeat.duplicate and repeat.document_id == first.document_id
    assert len({first.document_id, other_user.document_id, other_org.document_id}) == 3
    assert repeat.vector_index_status == "pending"
    assert repeat.vector_backend is None and not repeat.fallback_used
    assert session.scalar(select(func.count()).select_from(Document)) == 3
    assert session.scalar(select(func.count()).select_from(UserStrategy)) == 0
    document = session.get(Document, first.document_id)
    assert document.title == "Synthetic rules"
    assert document.ingestion_metadata["file"]["filename"] == "rules.md"
    assert (
        document.ingestion_metadata["file"]["raw_content_hash"]
        == hashlib.sha256(TEXT.encode()).hexdigest()
    )
    assert drain_indexing(service) == ["ready"] * 3
    result = service.search(RagQuery(query="Risk BLOCK", organization_id=ORG_A, user_id=USER_A))
    assert result.chunks and result.citations
    assert {chunk.document_id for chunk in result.chunks} == {first.document_id}
    assert {citation.source_filename for citation in result.citations} == {"rules.md"}
    assert all(
        citation.chunk_id == chunk.chunk_id
        for citation, chunk in zip(result.citations, result.chunks, strict=True)
    )
    assert all("Risk BLOCK" in chunk.content for chunk in result.chunks)


def test_same_owner_legacy_paste_duplicate_remains_compatible_but_other_user_is_independent(
    knowledge_db,
):
    session, service, _ = knowledge_db
    body = IngestDocumentRequest(
        organization_id=ORG_A,
        user_id=USER_A,
        source_type=DocumentSourceType.GENERAL_NOTE,
        title="Legacy",
        text=TEXT,
    )
    original = service.ingest(body)
    document = session.get(Document, original.document_id)
    document.source_hash = compute_source_hash(
        title=body.title, text=TEXT, source_type=body.source_type.value, organization_id=ORG_A
    )
    document.ingestion_metadata = None
    session.commit()
    repeated = service.ingest(body)
    assert repeated.duplicate and repeated.document_id == original.document_id
    assert repeated.vector_index_status == "unknown" and repeated.vector_backend is None
    second = service.ingest(body.model_copy(update={"user_id": USER_A2}))
    assert not second.duplicate and second.document_id != original.document_id


def test_failed_vector_index_preserves_pending_sql_storage(knowledge_db, monkeypatch):
    session, service, vectors = knowledge_db

    def failed(*args):
        raise ServiceUnavailableError("Synthetic vector outage")

    monkeypatch.setattr(vectors, "upsert", failed)
    result = ingest_file(service)
    assert result.vector_index_status == "pending"
    assert drain_indexing(service) == ["pending"]
    assert session.scalar(select(func.count()).select_from(Document)) == 1
    assert session.scalar(select(func.count()).select_from(Chunk)) >= 1


@pytest.fixture
def knowledge_api(knowledge_db):
    session, service, _ = knowledge_db
    app = FastAPI()
    app.state.settings = SETTINGS
    app.include_router(routes.router)
    register_exception_handlers(app)

    def tenant(authorization: str | None = Header(default=None)):
        if authorization is None:
            raise AuthError("Authentication required.")
        user = USER_A2 if authorization == "second" else USER_A
        return TenantContext(
            user_id=user,
            organization_id=ORG_A,
            email="synthetic@test.example",
            membership_role=MembershipRole.VIEWER
            if authorization == "viewer"
            else MembershipRole.OWNER,
        )

    app.dependency_overrides[get_current_tenant] = tenant
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_rag_service] = lambda: service
    app.dependency_overrides[routes._RAG_INGEST_QUOTA.dependency] = lambda: None
    with TestClient(app) as client:
        yield client, session


def test_http_preview_save_duplicate_and_sql_source_refs(knowledge_api, knowledge_db):
    client, session = knowledge_api
    form = {"title": "Uploaded note", "source_type": "general_note"}
    upload = {"file": ("note.txt", TEXT.encode(), "text/plain")}
    shown = client.post(
        "/knowledge/files/preview", data=form, files=upload, headers={"Authorization": "first"}
    )
    assert shown.status_code == 200, shown.text
    assert not shown.json()["saved"] and shown.json()["vector_index_status"] == "not_started"
    assert session.scalar(select(func.count()).select_from(Document)) == 0
    assert session.scalar(select(func.count()).select_from(Chunk)) == 0
    confirmed = {**form, "preview_receipt": shown.json()["preview_receipt"], "confirm": "true"}
    stolen = client.post(
        "/knowledge/files/import", data=confirmed, files=upload, headers={"Authorization": "second"}
    )
    assert stolen.status_code == 422
    saved = client.post(
        "/knowledge/files/import", data=confirmed, files=upload, headers={"Authorization": "first"}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["sql_chunks_stored"] and saved.json()["vector_index_status"] == "pending"
    repeat = client.post(
        "/knowledge/files/import", data=confirmed, files=upload, headers={"Authorization": "first"}
    )
    assert (
        repeat.json()["duplicate"] and repeat.json()["document_id"] == saved.json()["document_id"]
    )
    documents = client.get("/knowledge/documents", headers={"Authorization": "first"}).json()
    assert documents["items"][0]["ingestion_metadata"]["file"]["filename"] == "note.txt"
    assert (
        client.get("/knowledge/documents", headers={"Authorization": "second"}).json()["total"] == 0
    )
    assert drain_indexing(knowledge_db[1]) == ["ready"]
    searched = client.post(
        "/knowledge/search", json={"query": "Risk BLOCK"}, headers={"Authorization": "first"}
    )
    assert searched.status_code == 200
    assert searched.json()["citations"][0]["source_filename"] == "note.txt"


def test_http_requires_auth_membership_preview_and_bounded_multipart(knowledge_api):
    client, session = knowledge_api
    form = {"title": "Note", "source_type": "general_note"}
    upload = {"file": ("note.txt", TEXT.encode(), "text/plain")}
    assert client.post("/knowledge/files/preview", data=form, files=upload).status_code == 401
    assert (
        client.post(
            "/knowledge/files/preview", data=form, files=upload, headers={"Authorization": "viewer"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/knowledge/files/import",
            data={**form, "confirm": "true"},
            files=upload,
            headers={"Authorization": "first"},
        ).status_code
        == 422
    )
    oversized = {"file": ("huge.txt", b"a" * (MAX_FILE_BYTES + 65537), "text/plain")}
    assert (
        client.post(
            "/knowledge/files/preview",
            data=form,
            files=oversized,
            headers={"Authorization": "first"},
        ).status_code
        == 413
    )
    assert session.scalar(select(func.count()).select_from(Document)) == 0


@pytest.mark.asyncio
async def test_chunked_upload_is_bounded_before_multipart_parsing(knowledge_api):
    client, session = knowledge_api

    async def oversized_body():
        yield b"a" * (MAX_MULTIPART_BYTES // 2)
        yield b"a" * (MAX_MULTIPART_BYTES // 2 + 1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app), base_url="http://testserver"
    ) as http:
        response = await http.post(
            "/knowledge/files/preview",
            content=oversized_body(),
            headers={
                "Authorization": "first",
                "Content-Type": "multipart/form-data; boundary=synthetic",
            },
        )
    assert response.status_code == 413
    assert session.scalar(select(func.count()).select_from(Document)) == 0
