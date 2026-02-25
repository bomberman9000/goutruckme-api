from datetime import datetime, timedelta

from src.core.services.referral import build_referral_deeplink, extend_premium_until


def test_build_referral_deeplink():
    link = build_referral_deeplink("@my_bot", 12345)
    assert link == "https://t.me/my_bot?start=ref_12345"


def test_extend_premium_until_from_future():
    base = datetime.now() + timedelta(days=2)
    result = extend_premium_until(base, 7)
    assert result > base
