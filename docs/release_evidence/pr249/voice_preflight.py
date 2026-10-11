"""Validate the existing private approval; make no provider or exchange calls."""
import hashlib, json, sys
from datetime import UTC, datetime
from pathlib import Path

repo=Path('/workspace/AlphaTrade-AI')
sys.path.insert(0,str(repo/'backend/src'))
from app.agent_voice.config import VoiceLimits
from app.core.config import Settings

path=Path(sys.argv[1] if len(sys.argv)>1 else '/workspace/agent-realtime-approved-limits.json')
if not path.is_file():
    print(json.dumps({'ready':False,'reason':'verified_realtime_limits_unbound','expected_file':str(path),'paid_calls':0}))
    sys.exit(2)
try:
    raw=path.read_bytes()
    limits=VoiceLimits.model_validate_json(raw)
    settings=Settings(_env_file=None)
    now=datetime.now(UTC)
    checks={
        'approval_not_future':limits.budget_as_of<=now,
        'approval_unexpired':limits.receipt_deadline()>now,
        'reasoning_model_matches':limits.reasoning.model==settings.agent_reasoning_model,
        'embedding_receipt_present':limits.embedding is not None,
        'embedding_model_matches':limits.embedding is not None and limits.embedding.model==settings.embeddings_model,
        'reservation_within_existing_approval':limits.reservation(settings)<=min(limits.session_ceiling_usd,limits.remaining_budget_usd),
        'ceiling_at_most_one_usd':limits.session_ceiling_usd<=1,
        'existing_realtime_setting_enabled':settings.agent_realtime_enabled,
        'existing_provider_mode_live':settings.provider_mode=='live',
        'provider_key_bound':bool(settings.openai_api_key),
        'qdrant_binding_present':bool(settings.qdrant_url and settings.qdrant_api_key),
        'runtime_points_to_approval':Path(settings.agent_realtime_limits_file)==path if settings.agent_realtime_limits_file else False,
    }
    print(json.dumps({'ready':all(checks.values()),'checks':checks,'receipt_sha256':hashlib.sha256(raw).hexdigest(),'reservation_usd':str(limits.reservation(settings)),'maximum_seconds':limits.maximum_seconds,'maximum_turns':limits.maximum_turns,'paid_calls':0}))
    sys.exit(0 if all(checks.values()) else 2)
except Exception as exc:
    # Validation errors can include private receipt values; only emit the type.
    print(json.dumps({'ready':False,'reason':'verified_limits_or_runtime_invalid','error_type':type(exc).__name__,'paid_calls':0}))
    sys.exit(2)
