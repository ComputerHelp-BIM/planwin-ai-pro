"""LLM provider clients (Claude, OpenAI-compatible, Ollama) returning JSON actions.

API keys are read from the OS keyring (Windows Credential Manager) with
environment-variable fallback; they are never written to project files.
Only a compact model summary is sent to the provider – no drawings.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests

from .actions import ACTION_SCHEMA

KEYRING_SERVICE = "PlanWinAIPro"
TIMEOUT = 90

DEFAULT_MODELS = {"claude": "claude-sonnet-5-5", "openai": "gpt-4o-mini", "ollama": "llama3.1"}

SYSTEM_PROMPT = """You are the built-in assistant of PlanWin AI Pro, a structural engineering application
(RCC framed buildings, Indian codes IS 456:2000, IS 875, IS 1893-1:2016). You help engineers create and modify
building models, run analysis/design and export STAAD/ETABS/DXF/Excel/PDF.

Respond ONLY with a JSON object: {"reply": "<short helpful answer>", "actions": [ ... ]}
Each action is {"action": "<name>", ...params}. Available actions and parameters:
%s
Rules:
- Use metres and kN. G+N means ground + N upper floors (upper_floors = N).
- Prefer "modify_building" to change an existing parametric model, "new_building" for a new one.
- Only include actions the user asked for (or that are clearly required, e.g. analyze before export).
- For pure questions use no actions (or "answer"). Be concise and technically correct; cite IS clauses when useful.
- Never invent results – the application computes them.
Current model summary:
%s
"""


class ProviderError(RuntimeError):
    pass


def get_key(provider: str) -> Optional[str]:
    env = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
    try:
        import keyring

        k = keyring.get_password(KEYRING_SERVICE, provider)
        if k:
            return k
    except Exception:
        pass
    return os.environ.get(env) if env else None


def set_key(provider: str, key: str) -> bool:
    try:
        import keyring

        if key:
            keyring.set_password(KEYRING_SERVICE, provider, key)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, provider)
            except Exception:
                pass
        return True
    except Exception:
        return False


@dataclass
class ProviderConfig:
    provider: str = "offline"  # offline | claude | openai | ollama
    model: str = ""
    base_url: str = ""  # OpenAI-compatible / Ollama endpoint override


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {"reply": text, "actions": []}


def chat(cfg: ProviderConfig, history: list[dict], summary: str) -> dict:
    """Send the conversation; return {"reply": str, "actions": list}."""
    system = SYSTEM_PROMPT % (json.dumps(ACTION_SCHEMA, indent=1), summary)
    model = cfg.model or DEFAULT_MODELS.get(cfg.provider, "")
    msgs = [{"role": m["role"], "content": m["content"]} for m in history[-12:]]
    try:
        if cfg.provider == "claude":
            key = get_key("claude")
            if not key:
                raise ProviderError("No Claude API key – add it in Settings › AI")
            r = requests.post((cfg.base_url or "https://api.anthropic.com") + "/v1/messages", timeout=TIMEOUT,
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                              json={"model": model, "max_tokens": 1500, "system": system, "messages": msgs})
            if r.status_code >= 400:
                raise ProviderError(f"Claude API error {r.status_code}: {r.text[:300]}")
            data = r.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        elif cfg.provider == "openai":
            key = get_key("openai")
            if not key:
                raise ProviderError("No OpenAI API key – add it in Settings › AI")
            r = requests.post((cfg.base_url or "https://api.openai.com/v1") + "/chat/completions", timeout=TIMEOUT,
                              headers={"Authorization": f"Bearer {key}"},
                              json={"model": model, "temperature": 0.2, "response_format": {"type": "json_object"},
                                    "messages": [{"role": "system", "content": system}] + msgs})
            if r.status_code >= 400:
                raise ProviderError(f"OpenAI API error {r.status_code}: {r.text[:300]}")
            text = r.json()["choices"][0]["message"]["content"]
        elif cfg.provider == "ollama":
            r = requests.post((cfg.base_url or "http://localhost:11434") + "/api/chat", timeout=TIMEOUT * 2,
                              json={"model": model, "stream": False, "format": "json",
                                    "messages": [{"role": "system", "content": system}] + msgs})
            if r.status_code >= 400:
                raise ProviderError(f"Ollama error {r.status_code}: {r.text[:300]}")
            text = r.json()["message"]["content"]
        else:
            raise ProviderError(f"Unknown provider {cfg.provider}")
    except requests.RequestException as exc:
        raise ProviderError(f"Could not reach {cfg.provider}: {exc}") from exc
    out = _extract_json(text)
    if not isinstance(out.get("actions"), list):
        out["actions"] = []
    out["reply"] = str(out.get("reply", ""))
    return out
