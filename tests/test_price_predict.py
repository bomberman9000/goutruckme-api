"""Tests for price prediction engine."""

from src.core.services.price_predict import _linear_regression


class TestLinearRegression:
    def test_rising_trend(self):
        points = [(0, 100), (1, 110), (2, 120), (3, 130)]
        slope, intercept = _linear_regression(points)
        assert slope > 0
        assert abs(slope - 10) < 0.1

    def test_falling_trend(self):
        points = [(0, 200), (1, 180), (2, 160), (3, 140)]
        slope, _ = _linear_regression(points)
        assert slope < 0

    def test_stable(self):
        points = [(0, 100), (1, 100), (2, 100)]
        slope, _ = _linear_regression(points)
        assert abs(slope) < 0.1

    def test_single_point(self):
        slope, _ = _linear_regression([(0, 50)])
        assert slope == 0.0

    def test_empty(self):
        slope, intercept = _linear_regression([])
        assert slope == 0.0
        assert intercept == 0.0

    def test_prediction_accuracy(self):
        points = [(0, 100), (1, 110), (2, 120), (3, 130), (4, 140)]
        slope, intercept = _linear_regression(points)
        predicted_day7 = slope * 7 + intercept
        assert 160 < predicted_day7 < 180


class TestTeamRoles:
    def test_valid_roles(self):
        from src.api.teams import VALID_ROLES
        assert "admin" in VALID_ROLES
        assert "manager" in VALID_ROLES
        assert "carrier" in VALID_ROLES
        assert "hacker" not in VALID_ROLES
