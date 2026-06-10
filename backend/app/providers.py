"""Provider-agnostic LLM access — the app accepts a key from ANY AI provider.

One method, `generate_json(system, prompt, schema) -> dict`, returns a parsed
dict matching `schema` and raises on any failure (missing SDK/key, network,
schema mismatch). Callers in `llm.py` wrap that in try/except → a deterministic
offline fallback, so the app runs fully with no key configured.

Selection is env-driven (no keys in the browser):
  - Auto-detect from whichever key is set: ANTHROPIC_API_KEY / OPENAI_API_KEY /
    GEMINI_API_KEY (GOOGLE_API_KEY aliases the last).
  - Override with LLM_PROVIDER (anthropic|openai|gemini|openai_compatible),
    LLM_MODEL, LLM_BASE_URL.
  - LLM_BASE_URL routes OpenAI selection to the OpenAI-compatible adapter, which
    reaches Azure / Groq / OpenRouter / Mistral / DeepSeek / Ollama / local.

SDKs are imported lazily inside each adapter, so a tester with one key installs
only that one SDK; a missing SDK surfaces as ImportError → graceful fallback.
"""

from __future__ import annotations

import functools
import json
import os
from typing import Protocol

import jsonschema

_DEFAULT_MODEL = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.1",
    "gemini": "gemini-2.5-flash",
    # openai_compatible has no sensible default — model ids are provider-specific.
}


class Provider(Protocol):
    name: str
    model: str

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        """Return JSON matching `schema`; raise on any failure."""
        ...


def _validated(data: object, schema: dict) -> dict:
    """Parse-guard: every provider's output passes through here before return."""
    jsonschema.validate(data, schema)
    return data  # type: ignore[return-value]


# ── Adapters ─────────────────────────────────────────────────────────────────

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str) -> None:
        import anthropic  # lazy

        self.model = model
        self._client = anthropic.Anthropic()  # raises if no credentials

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 1024,
            "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        resp = self._client.messages.create(**kwargs)
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if not text:
            raise ValueError("no text block in response")
        return _validated(json.loads(text), schema)


class OpenAIProvider:
    """OpenAI and any OpenAI-compatible endpoint (base_url) — Azure, Groq,
    OpenRouter, Mistral, DeepSeek, Ollama, local vLLM, etc."""

    def __init__(self, model: str, base_url: str | None, name: str = "openai") -> None:
        from openai import OpenAI  # lazy

        self.name = name
        self.model = model
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=base_url)

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "result", "strict": True, "schema": schema},
                },
            )
        except Exception:
            # A compatible provider that ignores/rejects json_schema (e.g. Ollama):
            # fall back to JSON mode with the schema embedded in the prompt. OpenAI's
            # json_object mode requires the literal word "JSON" to appear — it does.
            sys_json = (system or "") + "\nReturn ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema)
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": sys_json}, {"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
        return _validated(json.loads(resp.choices[0].message.content), schema)


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str) -> None:
        from google import genai  # lazy (package: google-genai)

        self.model = model
        self._client = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        config: dict = {"response_mime_type": "application/json", "response_json_schema": schema}
        if system:
            config["system_instruction"] = system
        resp = self._client.models.generate_content(model=self.model, contents=prompt, config=config)
        return _validated(json.loads(resp.text), schema)


# ── Selection ────────────────────────────────────────────────────────────────

def _resolve() -> tuple[str, str, str | None] | None:
    """(provider, model, base_url) from env, or None when nothing is configured."""
    base_url = os.getenv("LLM_BASE_URL") or None
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower() or None

    if provider is None:  # auto-detect by the first key present
        if os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        elif os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            provider = "gemini"
        else:
            return None  # offline → caller uses the deterministic fallback

    # A base_url means "talk to an OpenAI-compatible endpoint", even if provider=openai.
    if base_url and provider in ("openai", "openai_compatible"):
        provider = "openai_compatible"

    model = os.getenv("LLM_MODEL") or _DEFAULT_MODEL.get(provider)
    if not model:
        raise ValueError(f"provider {provider!r} requires LLM_MODEL (no default)")
    return provider, model, base_url


@functools.lru_cache(maxsize=1)
def get_provider() -> Provider | None:
    """The configured provider (built once), or None when nothing is configured.

    Cached: env is read once after .env is loaded at startup. `get_provider.cache_clear()`
    (free with lru_cache) lets tests flip keys between cases. Raising here propagates
    to the caller's fallback, so a bad config degrades gracefully rather than crashing."""
    resolved = _resolve()
    if resolved is None:
        return None
    provider, model, base_url = resolved
    if provider == "anthropic":
        return AnthropicProvider(model)
    if provider == "openai":
        return OpenAIProvider(model, base_url=None, name="openai")
    if provider == "openai_compatible":
        return OpenAIProvider(model, base_url=base_url, name="openai_compatible")
    if provider == "gemini":
        return GeminiProvider(model)
    raise ValueError(f"unknown LLM_PROVIDER {provider!r}")
