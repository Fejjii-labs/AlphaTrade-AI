"""L17: organization-scoped repository reads fail closed without an org."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.repositories.approvals import ApprovalRepository
from app.repositories.audit import AuditRepository
from app.repositories.base import require_organization_id
from app.repositories.documents import DocumentRepository
from app.repositories.journal import JournalRepository
from app.repositories.orders import OrderRepository
from app.repositories.positions import PositionRepository
from app.repositories.proposals import ProposalRepository
from app.repositories.usage import UsageRepository

_ID = uuid.uuid4()

# (repository class, method name, extra positional args)
_SCOPED: list[tuple[type[Any], str, tuple[Any, ...]]] = [
    (OrderRepository, "list_orders", ()),
    (AuditRepository, "list_events", ()),
    (ProposalRepository, "list_proposals", ()),
    (ProposalRepository, "get_scoped", (_ID,)),
    (JournalRepository, "list_entries", ()),
    (JournalRepository, "get_scoped", (_ID,)),
    (UsageRepository, "list_events", ()),
    (UsageRepository, "summarize", ()),
    (ApprovalRepository, "list_approvals", ()),
    (DocumentRepository, "list_documents", ()),
    (PositionRepository, "list_positions", ()),
    (PositionRepository, "list_closed_for_analytics", ()),
    (PositionRepository, "get_scoped", (_ID,)),
]


def _ids(case: tuple[type[Any], str, tuple[Any, ...]]) -> str:
    return f"{case[0].__name__}.{case[1]}"


@pytest.mark.parametrize("case", _SCOPED, ids=_ids)
def test_organization_id_is_a_required_keyword(
    case: tuple[type[Any], str, tuple[Any, ...]],
) -> None:
    repo_cls, name, _args = case
    param = inspect.signature(getattr(repo_cls, name)).parameters["organization_id"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


@pytest.mark.parametrize("case", _SCOPED, ids=_ids)
def test_none_organization_raises_before_any_query(
    case: tuple[type[Any], str, tuple[Any, ...]],
) -> None:
    repo_cls, name, args = case

    class _NoQuerySession(Session):
        def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not run
            raise AssertionError("query executed without an organization scope")

        scalar = scalars = execute

    method: Callable[..., Any] = getattr(repo_cls(_NoQuerySession()), name)
    with pytest.raises(ValueError, match="organization_id is required"):
        method(*args, organization_id=None)


def test_require_organization_id_passes_a_uuid_through() -> None:
    assert require_organization_id(_ID) == _ID
    with pytest.raises(ValueError):
        require_organization_id(None)
