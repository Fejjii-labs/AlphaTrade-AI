"""Knowledge base / RAG API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.knowledge_file_route import KnowledgeFileRoute
from app.core.auth import TenantDep
from app.core.dependencies import RagServiceDep, SettingsDep
from app.core.errors import ValidationAppError
from app.rag.file_import_worker import MAX_FILE_BYTES
from app.schemas.common import DocumentSourceType
from app.schemas.rag import (
    DocumentCreateRequest,
    FileImportPreview,
    IngestDocumentRequest,
    IngestDocumentResponse,
    PaginatedRagChunks,
    PaginatedRagDocuments,
    RagDocument,
    RagQuery,
    RagSearchResponse,
)
from app.security.quota_enforcement import require_quota
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import TraderDep
from app.services.knowledge_file_import import preview_file, verify_file_save

router = APIRouter(prefix="/knowledge", tags=["knowledge"], route_class=KnowledgeFileRoute)

_KNOWLEDGE_INGEST_RATE_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "knowledge:ingest",
        limit=20,
        window_seconds=3600,
        ip_limit=40,
        user_limit=20,
    )
)
_RAG_INGEST_QUOTA = require_quota("rag_ingest")

_FILE_PREVIEW_RATE_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "knowledge:file-preview", limit=20, window_seconds=3600, ip_limit=40, user_limit=20
    )
)


async def _read_bounded_upload(file: UploadFile) -> bytes:
    try:
        content = await file.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ValidationAppError("Choose a file no larger than 5 MiB.")
        return content
    finally:
        await file.close()


@router.post(
    "/files/preview",
    response_model=FileImportPreview,
    dependencies=[_FILE_PREVIEW_RATE_LIMIT],
    summary="Preview text without saving a file",
)
async def preview_knowledge_file(
    file: UploadFile,
    tenant: TraderDep,
    settings: SettingsDep,
    title: str = Form(min_length=1, max_length=255),
    source_type: DocumentSourceType = Form(),
) -> FileImportPreview:
    content = await _read_bounded_upload(file)
    return await run_in_threadpool(
        preview_file,
        content,
        filename=file.filename or "",
        media_type=file.content_type,
        title=title,
        source_type=source_type,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        settings=settings,
    )


@router.post(
    "/files/import",
    response_model=IngestDocumentResponse,
    dependencies=[_KNOWLEDGE_INGEST_RATE_LIMIT, _RAG_INGEST_QUOTA],
    summary="Explicitly save a previously previewed Knowledge file",
)
async def import_knowledge_file(
    file: UploadFile,
    tenant: TraderDep,
    settings: SettingsDep,
    rag_service: RagServiceDep,
    title: str = Form(min_length=1, max_length=255),
    source_type: DocumentSourceType = Form(),
    preview_receipt: str = Form(min_length=1, max_length=4096),
    confirm: bool = Form(),
) -> IngestDocumentResponse:
    content = await _read_bounded_upload(file)
    text, provenance = await run_in_threadpool(
        verify_file_save,
        content,
        filename=file.filename or "",
        media_type=file.content_type,
        title=title,
        source_type=source_type,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        settings=settings,
        preview_receipt=preview_receipt,
        confirm=confirm,
    )
    payload = IngestDocumentRequest(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        source_type=source_type,
        title=title.strip(),
        text=text,
        source_uri=f"upload://sha256/{provenance.raw_content_hash}",
    )
    return rag_service.ingest(payload, file_provenance=provenance)


@router.post(
    "/documents",
    response_model=RagDocument,
    summary="Register knowledge document metadata",
)
async def create_document(
    body: DocumentCreateRequest,
    tenant: TraderDep,
    rag_service: RagServiceDep,
) -> RagDocument:
    payload = body.model_copy(
        update={"organization_id": tenant.organization_id, "user_id": tenant.user_id}
    )
    document = rag_service.create_document(payload)
    return document


@router.post(
    "/ingest",
    response_model=IngestDocumentResponse,
    summary="Ingest plain-text document into the knowledge base",
    dependencies=[_KNOWLEDGE_INGEST_RATE_LIMIT, _RAG_INGEST_QUOTA],
)
async def ingest_document(
    body: IngestDocumentRequest,
    tenant: TraderDep,
    rag_service: RagServiceDep,
) -> IngestDocumentResponse:
    payload = body.model_copy(
        update={"organization_id": tenant.organization_id, "user_id": tenant.user_id}
    )
    return rag_service.ingest(payload)


@router.get(
    "/documents",
    response_model=PaginatedRagDocuments,
    summary="List knowledge documents",
)
async def list_documents(
    tenant: TenantDep,
    rag_service: RagServiceDep,
    source_type: DocumentSourceType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedRagDocuments:
    items, total = rag_service.list_documents(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        source_type=source_type,
        limit=limit,
        offset=offset,
    )
    return PaginatedRagDocuments(items=items, total=total, limit=limit, offset=offset)


@router.delete(
    "/documents/{document_id}", status_code=204, summary="Delete your knowledge document"
)
def delete_document(document_id: uuid.UUID, tenant: TraderDep, rag_service: RagServiceDep) -> None:
    rag_service.delete_document(
        document_id, organization_id=tenant.organization_id, user_id=tenant.user_id
    )


@router.post(
    "/documents/{document_id}/retry-indexing",
    response_model=IngestDocumentResponse,
    dependencies=[_KNOWLEDGE_INGEST_RATE_LIMIT],
    summary="Retry failed indexing of your document",
)
def retry_indexing(
    document_id: uuid.UUID, tenant: TraderDep, rag_service: RagServiceDep
) -> IngestDocumentResponse:
    return rag_service.retry_indexing(
        document_id, organization_id=tenant.organization_id, user_id=tenant.user_id
    )


@router.get(
    "/chunks",
    response_model=PaginatedRagChunks,
    summary="List document chunks",
)
async def list_chunks(
    tenant: TenantDep,
    rag_service: RagServiceDep,
    document_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedRagChunks:
    items, total = rag_service.list_chunks(
        document_id=document_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )
    return PaginatedRagChunks(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/search",
    response_model=RagSearchResponse,
    summary="Search the knowledge base",
)
async def search_knowledge(
    body: RagQuery,
    tenant: TenantDep,
    rag_service: RagServiceDep,
) -> RagSearchResponse:
    payload = body.model_copy(
        update={"organization_id": tenant.organization_id, "user_id": tenant.user_id}
    )
    return rag_service.search(payload)
