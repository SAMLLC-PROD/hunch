"""Talk to a language model. Standard library only (urllib), so nothing extra to install.

Supported:
  anthropic  - Claude, via ANTHROPIC_API_KEY
  xai        - Grok, via XAI_API_KEY
  openai     - any OpenAI-compatible server (Ollama, LM Studio, vLLM, OpenRouter, a local
               Hermes model...) via HUNCH_BASE_URL and optional HUNCH_API_KEY

Pick one with --provider / HUNCH_PROVIDER, or let Hunch use whichever key it finds.
Override the model with --model / HUNCH_MODEL.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "xai": "grok-4.7",
    "openai": "",
}


class ProviderError(Exception):
    pass


def _post(url: str, headers: dict, body: dict, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:600]
        raise ProviderError(f"{e.code} from {url}: {detail}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"Could not reach {url}: {e.reason}") from e


class Provider:
    name = "base"

    def __init__(self, model: str):
        self.model = model

    def complete(self, system: str, messages: list[dict], max_tokens: int = 2048,
                 temperature: float | None = None) -> str:
        raise NotImplementedError

    def __repr__(self):
        return f"{self.name}:{self.model}"


class Anthropic(Provider):
    name = "anthropic"
    url = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, api_key: str):
        super().__init__(model)
        self.key = api_key

    def complete(self, system, messages, max_tokens=2048, temperature=None):
        body = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": messages}
        # Sonnet 5 returns 400 if temperature/top_p/top_k are set. Do not send them.
        del temperature
        data = _post(self.url, {"x-api-key": self.key, "anthropic-version": "2023-06-01"}, body)
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


class OpenAICompatible(Provider):
    name = "openai"

    def __init__(self, model: str, base_url: str, api_key: str | None, name: str = "openai"):
        super().__init__(model)
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.name = name

    def complete(self, system, messages, max_tokens=2048, temperature=None):
        body = {"model": self.model, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, *messages]}
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"authorization": f"Bearer {self.key}"} if self.key else {}
        data = _post(self.base + "/chat/completions", headers, body)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise ProviderError(f"Unexpected response: {str(data)[:300]}") from e


def get_provider(name: str | None = None, model: str | None = None, required: bool = True) -> Provider | None:
    name = (name or os.environ.get("HUNCH_PROVIDER") or "").strip().lower()
    model = model or os.environ.get("HUNCH_MODEL") or ""
    if name in ("grok", "x.ai"):
        name = "xai"
    if name in ("claude",):
        name = "anthropic"
    if not name:
        if os.environ.get("ANTHROPIC_API_KEY"):
            name = "anthropic"
        elif os.environ.get("XAI_API_KEY"):
            name = "xai"
        elif os.environ.get("HUNCH_BASE_URL"):
            name = "openai"
        elif required:
            raise ProviderError(
                "No AI provider configured. Set ANTHROPIC_API_KEY (Claude) or XAI_API_KEY (Grok), "
                "or HUNCH_BASE_URL for a local/OpenAI-compatible server."
            )
        else:
            return None

    if name == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not set.")
        return Anthropic(model or DEFAULT_MODELS["anthropic"], key)
    if name == "xai":
        key = os.environ.get("XAI_API_KEY")
        if not key:
            raise ProviderError("XAI_API_KEY is not set.")
        return OpenAICompatible(model or DEFAULT_MODELS["xai"], "https://api.x.ai/v1", key, name="xai")
    if name == "openai":
        base = os.environ.get("HUNCH_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        key = os.environ.get("HUNCH_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not base:
            base = "https://api.openai.com/v1" if key else ""
        if not base:
            raise ProviderError("Set HUNCH_BASE_URL (e.g. http://localhost:11434/v1 for Ollama).")
        if not model:
            raise ProviderError("Set --model or HUNCH_MODEL for OpenAI-compatible servers.")
        return OpenAICompatible(model, base, key, name="openai")
    raise ProviderError(f"Unknown provider '{name}'. Use anthropic, xai, or openai.")


def extract_json(text: str):
    """Pull the first JSON object/array out of a model reply (tolerates ``` fences and chatter)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        end = t.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in model reply")
