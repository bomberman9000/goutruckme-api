from __future__ import annotations

import asyncio

from src.core import ai


def test_parse_cargo_nlp_extracts_volume_m3(monkeypatch):
    monkeypatch.setattr(ai, "client", None)

    parsed = asyncio.run(ai.parse_cargo_nlp("самар казан 20т 86м3 тент завтра"))

    assert parsed is not None
    assert parsed["from_city"] == "Самара"
    assert parsed["to_city"] == "Казань"
    assert parsed["weight"] == 20.0
    assert parsed["volume_m3"] == 86.0
