from dataclasses import dataclass

from app.moderation.engine import review_deal, review_document
from app.moderation.flags import CANONICAL_FLAGS, normalize_flags


@dataclass
class _DealRow:
    id: int
    payload: dict


@dataclass
class _DocRow:
    id: int
    doc_type: str


def test_normalize_flags_from_legacy_keys():
    flags = normalize_flags(
        [
            "rate_per_km_very_low",
            "missing_or_empty_file",
            "file_hash_reused",
            "suspicious_keyword:предоплата 100%",
            "some_unknown_flag",
        ]
    )
    assert "low_price_outlier" in flags
    assert "doc_empty_or_missing" in flags
    assert "doc_duplicate_hash" in flags
    assert "prepay_100" in flags
    assert "suspicious_words" in flags
    assert all(flag in CANONICAL_FLAGS for flag in flags)


def test_normalize_flags_ignores_past_date_flags():
    flags = normalize_flags(["date_in_past", "past_date", "loading_date_in_past", "low_price_outlier"])
    assert "low_price_outlier" in flags
    assert "date_in_past" not in flags
    assert "past_date" not in flags
    assert "loading_date_in_past" not in flags


def test_deal_review_returns_only_canonical_flags(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "")

    row = _DealRow(
        id=1,
        payload={
            "from_city": "Москва",
            "to_city": "Казань",
            "comments": "Срочно, только наличка, предоплата 100%",
            "cargoSnapshot": {"price": 1000, "distance": 1000},
            "carrier": {"name": "X"},
        },
    )
    result = review_deal(row)
    assert result["risk_level"] in {"low", "medium", "high"}
    assert all(flag in CANONICAL_FLAGS for flag in result.get("flags") or [])
    assert "cash_only" in (result.get("flags") or [])
    assert "prepay_100" in (result.get("flags") or [])
    assert "low_price_outlier" in (result.get("flags") or [])


def test_deal_payload_for_llm_has_flat_shape(monkeypatch):
    captured: dict = {}

    def _fake_llm(entity_type, entity_id, payload):
        captured["entity_type"] = entity_type
        captured["entity_id"] = entity_id
        captured["payload"] = payload
        return None

    monkeypatch.setattr("app.moderation.engine.llm_analyze_review", _fake_llm)
    row = _DealRow(
        id=77,
        payload={
            "from_city": "Самара",
            "to_city": "Москва",
            "body_type": "тент",
            "pickup_date": "2026-02-19",
            "payment_terms": "предоплата 100%",
            "notes": "срочно, только наличка, без документов",
            "carrier_trust_score": 77,
            "carrier_stars": 4,
            "client_trust_score": 35,
            "client_stars": 2,
            "cargoSnapshot": {
                "weight": 20,
                "price": 180000,
                "distance": 1050,
            },
        },
    )

    review_deal(row)

    assert captured["entity_type"] == "deal"
    assert captured["entity_id"] == 77
    payload = captured["payload"]
    assert payload["from_city"] == "Самара"
    assert payload["to_city"] == "Москва"
    assert payload["weight_t"] == 20.0
    assert payload["body_type"] == "тент"
    assert payload["pickup_date"] == "2026-02-19"
    assert payload["price_total"] == 180000.0
    assert payload["distance_km"] == 1050.0
    assert payload["payment_terms"] == "предоплата 100%"
    assert payload["client_trust_score"] == 35.0
    assert payload["client_stars"] == 2.0
    assert payload["carrier_trust_score"] == 77.0
    assert payload["carrier_stars"] == 4.0
    assert isinstance(payload["rules"], dict)
    assert "risk_level" in payload["rules"]
    assert isinstance(payload["rules"].get("flags"), list)


def test_document_review_returns_only_canonical_flags(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "")

    row = _DocRow(id=10, doc_type="CONTRACT")
    result = review_document(
        row,
        deal_payload={"status": "CANCELLED"},
        file_exists=False,
        file_size=0,
        file_hash_seen_elsewhere=True,
    )
    assert result["risk_level"] in {"low", "medium", "high"}
    assert all(flag in CANONICAL_FLAGS for flag in result.get("flags") or [])
    assert "doc_empty_or_missing" in (result.get("flags") or [])
    assert "doc_duplicate_hash" in (result.get("flags") or [])
    assert "doc_type_mismatch" in (result.get("flags") or [])
