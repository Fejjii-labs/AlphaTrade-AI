"""Startup validation for configured LLM model cost coverage."""

from __future__ import annotations

import structlog

from app.core.config import Environment, Settings
from app.core.errors import ValidationAppError
from app.services.cost_estimator import configured_llm_models, model_has_cost_entry

logger = structlog.get_logger(__name__)


def missing_cost_table_models(settings: Settings) -> tuple[str, ...]:
    return tuple(
        model
        for model in configured_llm_models(settings)
        if not model_has_cost_entry(model)
    )


def validate_llm_cost_configuration(settings: Settings) -> None:
    """Ensure every configured router/base model has a static cost-table row."""
    missing = missing_cost_table_models(settings)
    if not missing:
        return
    detail = {"missing_cost_table_models": list(missing)}
    message = "Configured LLM model(s) missing cost table entries."
    if settings.environment is Environment.STAGING:
        raise ValidationAppError(message, details=detail)
    logger.error("llm_cost_table_incomplete", **detail)
