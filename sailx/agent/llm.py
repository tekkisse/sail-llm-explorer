"""LLM client for any OpenAI-compatible endpoint (vLLM, Ollama, TGI, LiteLLM...)."""
from __future__ import annotations

from typing import Any, Protocol

from ..config import AgentSettings


class LLM(Protocol):
    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Return {"content": str | None, "tool_calls": [{"id", "name", "arguments"}]}."""


class OpenAICompatibleLLM:
    def __init__(self, settings: AgentSettings):
        import httpx
        from openai import AsyncOpenAI

        self.settings = settings
        self.client = AsyncOpenAI(
            base_url=settings.llm_base_url, api_key=settings.llm_api_key,
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=settings.llm_connect_timeout_seconds),
            max_retries=settings.llm_max_retries)

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = await self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        return {
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"}
                for tc in (msg.tool_calls or [])
            ],
        }
