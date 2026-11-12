"""Клиент LLM без внешних зависимостей (только стандартная библиотека).

Поддерживает:
  * любой OpenAI-совместимый API (OpenRouter, YandexGPT через llm.api.cloud.yandex.net/v1,
    DeepSeek, Mistral, локальный Ollama/LM Studio, vLLM и т.д.);
  * GigaChat (Сбер): OAuth-токен + /api/v1/chat/completions.

Настройка через переменные окружения (см. .env.example).
Если ключ не задан — агент работает в офлайн-режиме «мастера».
"""
from __future__ import annotations

import base64
import json
import os
import ssl
import time
import urllib.request
import uuid


class LLMError(RuntimeError):
    pass


def _post(url: str, payload: dict | str, headers: dict, timeout: int = 60, ctx=None) -> dict:
    data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:500]
        raise LLMError(f"HTTP {e.code}: {body}") from e
    except Exception as e:  # сеть, таймаут, TLS
        raise LLMError(str(e)) from e


class OpenAICompatible:
    def __init__(self, base_url: str, api_key: str, model: str, extra_headers: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_headers = extra_headers or {}
        self.name = f"{model} @ {self.base_url.split('//')[-1].split('/')[0]}"

    def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}",
                   **self.extra_headers}
        res = _post(f"{self.base_url}/chat/completions",
                    {"model": self.model, "messages": messages, "temperature": temperature},
                    headers)
        try:
            return res["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"Неожиданный ответ API: {str(res)[:300]}") from e


class GigaChat:
    AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

    def __init__(self, auth_key: str, scope: str = "GIGACHAT_API_PERS", model: str = "GigaChat",
                 ca_bundle: str | None = None, verify: bool = True):
        self.auth_key = auth_key
        self.scope = scope
        self.model = model
        self.name = f"{model} (Сбер)"
        self._token, self._exp = None, 0
        if ca_bundle:
            self.ctx = ssl.create_default_context(cafile=ca_bundle)
        elif not verify:
            self.ctx = ssl._create_unverified_context()  # только для демо, см. README
        else:
            self.ctx = None

    def _get_token(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                   "RqUID": str(uuid.uuid4()), "Authorization": f"Basic {self.auth_key}"}
        res = _post(self.AUTH_URL, f"scope={self.scope}", headers, ctx=self.ctx)
        self._token = res["access_token"]
        self._exp = res.get("expires_at", 0) / 1000 or time.time() + 1500
        return self._token

    def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._get_token()}"}
        res = _post(self.API_URL, {"model": self.model, "messages": messages,
                                   "temperature": max(temperature, 0.01)}, headers, ctx=self.ctx)
        return res["choices"][0]["message"]["content"] or ""


def from_env():
    """Создаёт клиента по переменным окружения или возвращает None (офлайн-режим)."""
    provider = os.getenv("LLM_PROVIDER", "").lower().strip()
    if provider == "gigachat" or (not provider and os.getenv("GIGACHAT_AUTH_KEY")):
        key = os.getenv("GIGACHAT_AUTH_KEY")
        if not key:
            cid, secret = os.getenv("GIGACHAT_CLIENT_ID"), os.getenv("GIGACHAT_CLIENT_SECRET")
            if cid and secret:
                key = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        if not key:
            return None
        return GigaChat(key, os.getenv("GIGACHAT_SCOPE") or "GIGACHAT_API_PERS",
                        os.getenv("GIGACHAT_MODEL") or "GigaChat",
                        os.getenv("GIGACHAT_CA_BUNDLE") or None,
                        os.getenv("GIGACHAT_VERIFY_SSL", "true").lower() != "false")
    if provider == "yandex":
        key, folder = os.getenv("YANDEX_API_KEY"), os.getenv("YANDEX_FOLDER_ID")
        if not (key and folder):
            return None
        model = os.getenv("YANDEX_MODEL") or "yandexgpt/latest"
        return OpenAICompatible("https://llm.api.cloud.yandex.net/v1", key,
                                f"gpt://{folder}/{model}", {"OpenAI-Project": folder})
    key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    base = os.getenv("LLM_BASE_URL") or None
    if not base and os.getenv("OPENROUTER_API_KEY"):
        base = "https://openrouter.ai/api/v1"
    base = base or "https://api.openai.com/v1"
    if not key and "localhost" not in base and "127.0.0.1" not in base and "ollama" not in base:
        return None
    default_model = "deepseek/deepseek-chat" if "openrouter" in base else "gpt-4o-mini"
    return OpenAICompatible(base, key or "ollama", os.getenv("LLM_MODEL") or default_model)
