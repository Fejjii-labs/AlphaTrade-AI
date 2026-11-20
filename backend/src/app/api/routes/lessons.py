"""Lesson review workflow API (Slice 37)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.core.dependencies import LessonCandidateServiceDep, SessionDep
from app.schemas.common import LessonCandidateStatus
from app.schemas.lesson import (
    AcceptedLesson,
    LessonCandidate,
    LessonCandidateAccept,
    LessonCandidateArchive,
    LessonCandidateCreate,
    LessonCandidateReject,
    PaginatedAcceptedLessons,
    PaginatedLessonCandidates,
)
from app.security.rbac import TraderDep

router = APIRouter(prefix="/lessons", tags=["lessons"])


@router.get(
    "/candidates",
    response_model=PaginatedLessonCandidates,
    summary="List lesson candidates",
)
def list_lesson_candidates(
    tenant: TraderDep,
    service: LessonCandidateServiceDep,
    status: LessonCandidateStatus | None = Query(default=None),
    mistake_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedLessonCandidates:
    items, total = service.list_candidates(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        status=status,
        mistake_type=mistake_type,
        limit=limit,
        offset=offset,
    )
    return PaginatedLessonCandidates(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/candidates/{lesson_id}",
    response_model=LessonCandidate,
    summary="Get lesson candidate",
)
def get_lesson_candidate(
    lesson_id: uuid.UUID,
    tenant: TraderDep,
    service: LessonCandidateServiceDep,
) -> LessonCandidate:
    return service.get(
        lesson_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.post(
    "/candidates",
    response_model=LessonCandidate,
    summary="Create lesson candidate",
)
def create_lesson_candidate(
    body: LessonCandidateCreate,
    tenant: TraderDep,
    service: LessonCandidateServiceDep,
    session: SessionDep,
) -> LessonCandidate:
    result = service.create(
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.patch(
    "/candidates/{lesson_id}/accept",
    response_model=AcceptedLesson,
    summary="Accept lesson candidate",
)
def accept_lesson_candidate(
    lesson_id: uuid.UUID,
    body: LessonCandidateAccept,
    tenant: TraderDep,
    service: LessonCandidateServiceDep,
    session: SessionDep,
) -> AcceptedLesson:
    result = service.accept(
        lesson_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.patch(
    "/candidates/{lesson_id}/reject",
    response_model=LessonCandidate,
    summary="Reject lesson candidate",
)
def reject_lesson_candidate(
    lesson_id: uuid.UUID,
    body: LessonCandidateReject,
    tenant: TraderDep,
    service: LessonCandidateServiceDep,
    session: SessionDep,
) -> LessonCandidate:
    result = service.reject(
        lesson_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.patch(
    "/candidates/{lesson_id}/archive",
    response_model=LessonCandidate,
    summary="Archive lesson candidate",
)
def archive_lesson_candidate(
    lesson_id: uuid.UUID,
    body: LessonCandidateArchive,
    tenant: TraderDep,
    service: LessonCandidateServiceDep,
    session: SessionDep,
) -> LessonCandidate:
    result = service.archive(
        lesson_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get("/accepted", response_model=PaginatedAcceptedLessons, summary="List accepted lessons")
def list_accepted_lessons(
    tenant: TraderDep,
    service: LessonCandidateServiceDep,
    mistake_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedAcceptedLessons:
    items, total = service.list_accepted(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        mistake_type=mistake_type,
        limit=limit,
        offset=offset,
    )
    return PaginatedAcceptedLessons(items=items, total=total, limit=limit, offset=offset)
