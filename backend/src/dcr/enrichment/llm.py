"""Claude API wrapper: structured JSON output, schema validation, cost accounting, refusal handling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypeVar

import anthropic
import structlog
from pydantic import BaseModel, ValidationError

from dcr.config import EnrichmentConfig
from dcr.enrichment.schemas import strict_json_schema

log = structlog.get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """Non-retryable enrichment failure for one item (caller falls back to heuristics)."""

    def __init__(self, message: str, cost_usd: float = 0.0, input_tokens: int = 0, output_tokens: int = 0) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


@dataclass
class LLMResult:
    data: BaseModel
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str


def price(cfg: EnrichmentConfig, model: str, input_tokens: int, output_tokens: int) -> float:
    p = cfg.pricing.get(model)
    if p is None:
        return 0.0
    return input_tokens / 1e6 * p.input_per_mtok + output_tokens / 1e6 * p.output_per_mtok


class ClaudeClient:
    """Async structured-output client. Uses server-side refusal fallbacks (``fallbacks: "default"``)."""

    def __init__(
        self, cfg: EnrichmentConfig, api_key: str | None = None, client: anthropic.AsyncAnthropic | None = None
    ) -> None:
        self.cfg = cfg
        self.client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=3)
        self._fallbacks_supported = True

    async def structured(self, system: str, user: str, schema: type[T], *, max_tokens: int | None = None) -> LLMResult:
        """Call Claude and return a validated ``schema`` instance (raises :class:`LLMError`)."""
        params: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": max_tokens or self.cfg.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "effort": self.cfg.effort,
                "format": {"type": "json_schema", "schema": strict_json_schema(schema)},
            },
        }
        try:
            if self._fallbacks_supported:
                try:
                    resp = await self.client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
                except anthropic.BadRequestError as exc:
                    if "fallback" not in str(exc).lower():
                        raise
                    # Model/account doesn't accept fallbacks; retry once without and remember.
                    self._fallbacks_supported = False
                    resp = await self.client.messages.create(**params)
            else:
                resp = await self.client.messages.create(**params)
        except anthropic.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("connection error") from exc

        usage = resp.usage
        in_tok = int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        model = getattr(resp, "model", self.cfg.model) or self.cfg.model
        cost = price(self.cfg, model, in_tok, out_tok) or price(self.cfg, self.cfg.model, in_tok, out_tok)
        if resp.stop_reason == "refusal":
            raise LLMError("model declined the request", cost, in_tok, out_tok)
        if resp.stop_reason == "max_tokens":
            raise LLMError("output truncated (max_tokens)", cost, in_tok, out_tok)
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
        if text is None:
            raise LLMError("no text block in response", cost, in_tok, out_tok)
        try:
            data = schema.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMError(f"schema validation failed: {str(exc)[:200]}", cost, in_tok, out_tok) from exc
        return LLMResult(data=data, input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost, model=model)
