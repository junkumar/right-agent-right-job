from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    api_key: str


def load_llm_config() -> LLMConfig | None:
    provider = os.getenv("DEMO_LLM_PROVIDER", "").lower()
    if provider == "openai" or (not provider and os.getenv("OPENAI_API_KEY")):
        return LLMConfig(
            provider="openai",
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            api_key=os.environ["OPENAI_API_KEY"],
        )
    if provider in {"anthropic", "claude"} or (not provider and os.getenv("CLAUDE_API_KEY")):
        return LLMConfig(
            provider="anthropic",
            model=os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-latest"),
            api_key=os.environ["CLAUDE_API_KEY"],
        )
    if provider == "gemini" or (not provider and os.getenv("GEMINI_API_KEY")):
        return LLMConfig(
            provider="gemini",
            model=os.getenv("GEMINI_MODEL", "gemini-1.5-pro"),
            api_key=os.environ["GEMINI_API_KEY"],
        )
    return None


class LLMClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or load_llm_config()

    @property
    def enabled(self) -> bool:
        return self.config is not None

    def complete_text(self, *, system: str, prompt: str) -> str:
        if not self.config:
            raise LLMError("No configured LLM provider")
        if self.config.provider == "openai":
            return self._openai_text(system=system, prompt=prompt)
        if self.config.provider == "anthropic":
            return self._anthropic_text(system=system, prompt=prompt)
        if self.config.provider == "gemini":
            return self._gemini_text(system=system, prompt=prompt)
        raise LLMError(f"Unsupported provider: {self.config.provider}")

    def complete_json(self, *, system: str, prompt: str) -> dict[str, Any]:
        text = self.complete_text(system=system, prompt=prompt)
        return extract_json_object(text)

    def _openai_text(self, *, system: str, prompt: str) -> str:
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        payload = self._post_json(
            "https://api.openai.com/v1/chat/completions",
            body,
            {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            return payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected OpenAI response: {payload}") from exc

    def _anthropic_text(self, *, system: str, prompt: str) -> str:
        body = {
            "model": self.config.model,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 800,
            "temperature": 0,
        }
        payload = self._post_json(
            "https://api.anthropic.com/v1/messages",
            body,
            {
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        try:
            return "".join(
                block["text"] for block in payload["content"] if block.get("type") == "text"
            ).strip()
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Anthropic response: {payload}") from exc

    def _gemini_text(self, *, system: str, prompt: str) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.config.model}:generateContent?key={self.config.api_key}"
        )
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0},
        }
        payload = self._post_json(url, body, {"Content-Type": "application/json"})
        try:
            return payload["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Gemini response: {payload}") from exc

    def _post_json(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"Network error: {exc}") from exc


def extract_json_object(text: str) -> dict[str, Any]:
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMError(f"No JSON object found in model output: {text}")
    return json.loads(candidate[start : end + 1])


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_+-]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
    return cleaned.strip()
