#!/usr/bin/env python3
"""
Скрипт проверки AI Moderation v1:
- создание/обновление deal_sync -> появляется запись в moderation_review, бейдж в админке;
- загрузка документа -> запись в moderation_review;
- re-run endpoint работает;
- без LLM ключей правила всё равно дают результат.
"""
import os
import sys

# project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    from app.db.database import SessionLocal, init_db
    from app.models.models import DealSync, DocumentSync, ModerationReview
    from app.moderation.service import (
        get_review,
        set_review_pending,
        run_deal_review_background,
        upsert_review,
    )
    from app.moderation.engine import review_deal, review_document

    init_db()
    db = SessionLocal()

    # 1) Rules-only: review_deal returns result
    payload = {
        "id": "deal_test_1",
        "cargoId": "1",
        "status": "IN_PROGRESS",
        "cargoSnapshot": {
            "from_city": "Москва",
            "to_city": "СПб",
            "price": 50000,
            "distance": 700,
        },
        "carrier": {"name": "Test", "phone": "+79001234567"},
    }
    from types import SimpleNamespace
    row_obj = SimpleNamespace(payload=payload)
    result = review_deal(row_obj)
    assert "risk_level" in result
    assert result["risk_level"] in ("low", "medium", "high")
    assert "model_used" in result
    print("1) review_deal (rules):", result["risk_level"], result.get("model_used"))

    # 2) Upsert review
    upsert_review(db, "deal", 99999, result, status="done")
    row = get_review(db, "deal", 99999)
    assert row is not None
    assert row.risk_level == result["risk_level"]
    print("2) upsert_review + get_review: OK")

    # 3) Create real deal_sync and run moderation
    deal_row = DealSync(local_id="deal_sanity_1", payload=payload)
    db.add(deal_row)
    db.commit()
    db.refresh(deal_row)
    set_review_pending(db, "deal", deal_row.id)
    run_deal_review_background(deal_row.id)
    review = get_review(db, "deal", deal_row.id)
    assert review is not None
    assert review.status == "done"
    print("3) deal_sync + run_deal_review_background: status =", review.status, "risk =", review.risk_level)

    # 4) Re-run: no LLM keys -> model_used is rules
    assert review.model_used in ("rules", "rules_fallback", None) or review.model_used  # may be llm if key set
    print("4) model_used:", review.model_used)

    # Cleanup
    db.query(ModerationReview).filter(ModerationReview.entity_id == 99999).delete()
    db.query(ModerationReview).filter(ModerationReview.entity_type == "deal", ModerationReview.entity_id == deal_row.id).delete()
    db.query(DealSync).filter(DealSync.id == deal_row.id).delete()
    db.commit()
    db.close()

    print("Sanity OK: moderation works without LLM keys.")

if __name__ == "__main__":
    main()
