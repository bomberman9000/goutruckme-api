from app.ai.scoring import MarketStats, RatePoint, compute_ai_score, compute_market_median


def test_score_clamp():
    stats = MarketStats.from_rate_points(
        points=[
            RatePoint("samara", "moscow", "tent", 100.0),
            RatePoint("samara", "moscow", "tent", 110.0),
            RatePoint("samara", "moscow", "tent", 90.0),
        ]
    )
    load = {
        "from_city": "Samara",
        "to_city": "Moscow",
        "truck_type": "tent",
        "price": 1000,   # Очень низко относительно рынка.
        "distance": 1500,
        "ai_risk": "high",
    }
    result = compute_ai_score(load, stats)
    assert 0 <= result["ai_score"] <= 100
    assert result["ai_score"] == 0


def test_fallback_median():
    points = []

    # route bucket: 5 (<10)
    points.extend([RatePoint("samara", "moscow", "tent", 100.0) for _ in range(5)])
    # destination bucket: +3 => 8 (<10)
    points.extend([RatePoint("ufa", "moscow", "tent", 90.0) for _ in range(3)])
    # vehicle bucket total: +4 => 12 (используется fallback)
    points.extend([RatePoint("kazan", "spb", "tent", 70.0) for _ in range(4)])

    stats = MarketStats.from_rate_points(points=points)
    load = {
        "from_city": "Samara",
        "to_city": "Moscow",
        "truck_type": "tent",
        "price": 100000,
        "distance": 1000,
    }
    market_rate, sample_size, source = compute_market_median(load, stats)
    assert source == "vehicle_bucket"
    assert sample_size == 12
    assert round(market_rate, 2) == 90.0


def test_risk_penalty_applied():
    stats = MarketStats.from_rate_points(
        points=[RatePoint("samara", "moscow", "tent", 100.0) for _ in range(12)]
    )

    base_load = {
        "from_city": "Samara",
        "to_city": "Moscow",
        "truck_type": "tent",
        "price": 100000,
        "distance": 1000,
    }
    low_risk = compute_ai_score({**base_load, "ai_risk": "low"}, stats)
    high_risk = compute_ai_score({**base_load, "ai_risk": "high"}, stats)

    assert low_risk["ai_score"] == 60
    assert high_risk["ai_score"] == 25
    assert low_risk["ai_score"] - high_risk["ai_score"] == 35
