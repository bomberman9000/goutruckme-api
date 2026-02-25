from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx


logger = logging.getLogger(__name__)


class AIUnavailableError(RuntimeError):
    """Raised when both local and VPS AI providers are unavailable."""

    def __init__(
        self,
        message: str = "AI services are unavailable",
        *,
        local_error: str | None = None,
        vps_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.local_error = local_error
        self.vps_error = vps_error

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "AI_UNAVAILABLE",
            "message": str(self),
            "local_error": self.local_error,
            "vps_error": self.vps_error,
        }


class AIService:
    """Hybrid AI router: local Ollama first, VPS fallback second."""

    def __init__(
        self,
        *,
        local_ollama_url: str | None = None,
        local_model: str | None = None,
        vps_ai_url: str | None = None,
        vps_model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.local_ollama_url = (
            local_ollama_url
            or os.getenv("LOCAL_OLLAMA_URL")
            or os.getenv("OLLAMA_BASE_URL")
            or "http://localhost:11434"
        ).rstrip("/")
        self.local_model = local_model or os.getenv("LOCAL_MODEL") or os.getenv("OLLAMA_MODEL") or "llama3:8b"
        self.vps_ai_url = (vps_ai_url or os.getenv("VPS_AI_URL") or "").strip()
        self.vps_model = vps_model or os.getenv("VPS_MODEL") or "mistral"
        self.timeout = float(timeout if timeout is not None else os.getenv("AI_TIMEOUT", "10"))

    def check_local_available(self) -> bool:
        """Ping local Ollama /api/tags and return availability."""
        tags_url = f"{self.local_ollama_url}/api/tags"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(tags_url)
                if response.status_code != 200:
                    return False
                payload = response.json()
                return isinstance(payload, dict)
        except Exception:
            return False

    def ask(
        self,
        *,
        prompt: str,
        model_override: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, str]:
        if not prompt or not prompt.strip():
            raise ValueError("prompt must not be empty")

        prompt_text = prompt.strip()
        prompt_len = len(prompt_text)
        started = time.perf_counter()
        resolved_temperature = self._resolve_temperature(temperature, prompt_text)

        local_model = model_override or self.local_model
        vps_model = model_override or self.vps_model

        local_error: str | None = None
        vps_error: str | None = None

        if self.check_local_available():
            try:
                result = self._request_local(
                    prompt=prompt_text,
                    model=local_model,
                    temperature=resolved_temperature,
                    max_tokens=max_tokens,
                    system_prompt=system_prompt,
                )
                self._log_result(
                    source="local",
                    model=result["model"],
                    prompt_len=prompt_len,
                    started=started,
                )
                return result
            except Exception as exc:
                local_error = str(exc)
                logger.warning("ai.local.failed model=%s error=%s", local_model, local_error)
        else:
            local_error = "local_ollama_unavailable"
            logger.info("ai.local.unavailable url=%s", self.local_ollama_url)

        try:
            result = self._request_vps(
                prompt=prompt_text,
                model=vps_model,
                temperature=resolved_temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )
            self._log_result(
                source="vps",
                model=result["model"],
                prompt_len=prompt_len,
                started=started,
            )
            return result
        except Exception as exc:
            vps_error = str(exc)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.error(
                "ai.unavailable model=%s duration_ms=%s prompt_len=%s local_error=%s vps_error=%s",
                vps_model,
                elapsed_ms,
                prompt_len,
                local_error,
                vps_error,
            )
            raise AIUnavailableError(local_error=local_error, vps_error=vps_error) from exc

    def _request_local(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int | None,
        system_prompt: str | None,
    ) -> dict[str, str]:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            payload["system"] = system_prompt

        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload["options"] = options

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.local_ollama_url}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()

        text = self._extract_text(data)
        if not text:
            raise RuntimeError("Local Ollama returned empty response")

        return {
            "text": text,
            "model": str(data.get("model") or model),
            "source": "local",
        }

    def _request_vps(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int | None,
        system_prompt: str | None,
    ) -> dict[str, str]:
        if not self.vps_ai_url:
            raise RuntimeError("VPS_AI_URL is not configured")

        payload: dict[str, Any] = {
            "prompt": prompt,
            "model": model,
            "stream": False,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if system_prompt:
            payload["system_prompt"] = system_prompt

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self.vps_ai_url, json=payload)
            response.raise_for_status()
            data = response.json()

        text = self._extract_text(data)
        if not text:
            raise RuntimeError("VPS AI returned empty response")

        return {
            "text": text,
            "model": str(data.get("model") or model),
            "source": "vps",
        }

    def _resolve_temperature(self, temperature: float | None, prompt: str) -> float:
        if temperature is not None:
            return max(0.0, min(2.0, float(temperature)))

        prompt_len = len(prompt)
        if prompt_len > 1200:
            return 0.1
        if prompt_len > 400:
            return 0.2
        return 0.3

    def _extract_text(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""

        for key in ("text", "response", "answer", "output"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                text = first.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()

        result = payload.get("result")
        if isinstance(result, dict):
            nested_text = self._extract_text(result)
            if nested_text:
                return nested_text

        return ""

    def _log_result(self, *, source: str, model: str, prompt_len: int, started: float) -> None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "ai.ask source=%s model=%s duration_ms=%s prompt_len=%s",
            source,
            model,
            elapsed_ms,
            prompt_len,
        )


ai_service = AIService()


def check_local_available() -> bool:
    return ai_service.check_local_available()
