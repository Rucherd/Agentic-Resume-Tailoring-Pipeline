from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, RateLimitError

from config import MAX_API_CONCURRENCY, MAX_RETRIES, MODEL_PRICING


@dataclass
class UsageRecord:
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


class OpenAIService:
    def __init__(self) -> None:
        self.client = AsyncOpenAI()
        self.semaphore = asyncio.Semaphore(MAX_API_CONCURRENCY)

    @staticmethod
    def _usage(stage: str, model: str, response: Any) -> UsageRecord:
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        pricing = MODEL_PRICING.get(model)
        if pricing:
            estimated = (
                input_tokens * pricing["input"] / 1_000_000
                + output_tokens * pricing["output"] / 1_000_000
            )
        else:
            estimated = None

        return UsageRecord(
            stage=stage,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated,
        )

    async def call_json(
        self,
        *,
        stage: str,
        model: str,
        effort: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int = 8000,
    ) -> tuple[dict[str, Any], UsageRecord]:
        async with self.semaphore:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    response = await self.client.responses.create(
                        model=model,
                        input=prompt,
                        reasoning={"effort": effort},
                        max_output_tokens=max_output_tokens,
                        text={
                            "format": {
                                "type": "json_schema",
                                "name": schema_name,
                                "schema": schema,
                                "strict": True,
                            }
                        },
                    )
                    data = json.loads(response.output_text)
                    return data, self._usage(stage, model, response)

                except RateLimitError as exc:
                    text = str(exc).lower()
                    if "insufficient_quota" in text or "billing" in text or "credit" in text:
                        raise
                    if attempt == MAX_RETRIES:
                        raise
                    await asyncio.sleep(2 ** attempt)

                except (APITimeoutError, APIConnectionError):
                    if attempt == MAX_RETRIES:
                        raise
                    await asyncio.sleep(2 ** attempt)

        raise RuntimeError("Unreachable retry state")


service = OpenAIService()
