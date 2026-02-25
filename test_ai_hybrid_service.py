import pytest

from app.ai.ai_service import AIService, AIUnavailableError


def _build_service() -> AIService:
    return AIService(
        local_ollama_url="http://localhost:11434",
        local_model="llama3:8b",
        vps_ai_url="https://ai.example.com/generate",
        vps_model="mistral",
        timeout=10,
    )


def test_ai_router_local_success(monkeypatch):
    service = _build_service()

    monkeypatch.setattr(service, "check_local_available", lambda: True)
    monkeypatch.setattr(
        service,
        "_request_local",
        lambda **kwargs: {"text": "local answer", "model": "llama3:8b", "source": "local"},
    )

    def _must_not_call_vps(**kwargs):
        raise AssertionError("VPS should not be called when local succeeds")

    monkeypatch.setattr(service, "_request_vps", _must_not_call_vps)

    result = service.ask(prompt="hello")

    assert result["source"] == "local"
    assert result["model"] == "llama3:8b"
    assert result["text"] == "local answer"


def test_ai_router_fallback_to_vps(monkeypatch):
    service = _build_service()

    monkeypatch.setattr(service, "check_local_available", lambda: True)

    def _local_fail(**kwargs):
        raise RuntimeError("local failed")

    monkeypatch.setattr(service, "_request_local", _local_fail)
    monkeypatch.setattr(
        service,
        "_request_vps",
        lambda **kwargs: {"text": "vps answer", "model": "mistral", "source": "vps"},
    )

    result = service.ask(prompt="fallback please")

    assert result["source"] == "vps"
    assert result["model"] == "mistral"
    assert result["text"] == "vps answer"


def test_ai_router_both_fail(monkeypatch):
    service = _build_service()

    monkeypatch.setattr(service, "check_local_available", lambda: False)

    def _vps_fail(**kwargs):
        raise RuntimeError("vps failed")

    monkeypatch.setattr(service, "_request_vps", _vps_fail)

    with pytest.raises(AIUnavailableError) as exc:
        service.ask(prompt="will fail")

    assert exc.value.local_error == "local_ollama_unavailable"
    assert "vps failed" in (exc.value.vps_error or "")
