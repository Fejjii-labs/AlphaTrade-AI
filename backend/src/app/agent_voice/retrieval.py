"""One prepaid, durably identified embedding query per canonical voice turn.

The existing RagService retains vector/SQL authority checks and provider policy.
Unknown usage or a failed transport is not a new request or a valid vector result.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import UUID, uuid5

from app.agent_voice.accounting import VoiceLedger
from app.agent_voice.config import VoiceUnavailableError
from app.db.models import UsageEvent
from app.interactive_agent.vector_adapter import DetachedUsage
from app.providers.base import ProviderHealth, ProviderStatus
from app.providers.embeddings import (
    EmbeddingResult,
    EmbeddingsProvider,
    OpenAIEmbeddingsProvider,
)
from app.schemas.usage import UsageEvent as UsageEventRecord
from app.schemas.usage import UsageEventCreate
from app.services.usage_service import _to_event


def embedding_limitation(ledger: VoiceLedger) -> str | None:
    price, settings = ledger.limits.embedding, ledger.settings
    reason = None
    if price is None:
        reason = "no verified embedding spending receipt"
    elif price.valid_until <= datetime.now(UTC):
        reason = "verified embedding receipt expired"
    elif price.model != settings.embeddings_model:
        reason = "configured embedding route differs from its verified receipt"
    elif not settings.openai_api_key.strip():
        reason = "configured provider credential is unbound"
    elif settings.provider_mode.strip().lower() != "live":
        reason = "approved live embedding route is unavailable"
    return "Vector embedding unavailable: " + reason + "." if reason else None


class VoiceEmbeddingsProvider:
    def __init__(self, provider: EmbeddingsProvider, ledger: VoiceLedger, key: str):
        if isinstance(provider, OpenAIEmbeddingsProvider):
            # Preserve ordinary text defaults. Only this prepaid voice route
            # refuses raw missing/model-coerced metadata and all mock fallback.
            provider = OpenAIEmbeddingsProvider(
                model=ledger.settings.embeddings_model,
                api_key=ledger.settings.openai_api_key,
                base_url=ledger.settings.openai_base_url,
                dimensions=provider.dimensions,
                fail_closed=True,
                require_usage_receipt=True,
            )
        self.provider, self.ledger, self.key = provider, ledger, key
        self.name, self.kind = provider.name, provider.kind

    @property
    def dimensions(self) -> int:
        return self.provider.dimensions

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=ProviderHealth.DEGRADED,
            is_mock=False,
            using_fallback=False,
            detail="Bounded configured embedding route; live access has not been probed.",
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.embed_with_metadata(texts).vectors

    def embed_with_metadata(self, texts: list[str]) -> EmbeddingResult:
        if embedding_limitation(self.ledger):
            raise VoiceUnavailableError("voice_embedding_unavailable")
        if len(texts) != 1:
            raise VoiceUnavailableError("voice_embedding_batch_refused")
        self.ledger.embedding_attempt(self.key, texts[0])
        try:
            result = self.provider.embed_with_metadata(texts)
        except Exception:
            self.ledger.embedding_done(self.key, None, valid=False)
            raise
        price = self.ledger.limits.embedding
        assert price is not None
        valid = (
            isinstance(result, EmbeddingResult)
            and result.model == price.model
            and result.fallback_used is False
            and isinstance(result.input_tokens, int)
            and not isinstance(result.input_tokens, bool)
            and 0 < result.input_tokens <= price.maximum_input_tokens
            and isinstance(result.vectors, list)
            and len(result.vectors) == 1
            and result.dimensions == self.dimensions
            and isinstance(result.vectors[0], list)
            and len(result.vectors[0]) == self.dimensions
            and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in result.vectors[0]
            )
        )
        self.ledger.embedding_done(self.key, result, valid=valid)
        if not valid:
            raise VoiceUnavailableError("voice_embedding_response_outside_receipt")
        return result


class VoiceEmbeddingUsage(DetachedUsage):
    """RagService reuses the verified event, never its placeholder price estimate."""

    def __init__(self, ledger: VoiceLedger, key: str):
        super().__init__(ledger.sessions)
        self.ledger, self.key = ledger, key

    def record(self, data: UsageEventCreate) -> UsageEventRecord:
        with self.sessions() as session:
            row = session.get(UsageEvent, uuid5(UUID(self.key), "voice-embedding:query"))
            if (
                row is None
                or row.organization_id != data.organization_id
                or row.user_id != self.principal_id
                or row.organization_id != self.ledger.organization_id
                or row.user_id != self.ledger.user_id
            ):
                raise VoiceUnavailableError("voice_embedding_usage_receipt_missing")
            return _to_event(row)
