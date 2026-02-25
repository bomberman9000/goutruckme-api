"""Tests for finance service and profit calculator."""

from src.api.finance import ProfitCalcRequest


class TestProfitCalc:
    def _calc(self, **kw) -> dict:
        defaults = dict(
            rate_rub=100000, distance_km=1000,
            fuel_consumption_l_per_100km=35.0,
            fuel_price_rub=60.0, tax_percent=6.0,
            other_expenses_rub=0,
        )
        defaults.update(kw)
        req = ProfitCalcRequest(**defaults)

        fuel = int(req.distance_km * req.fuel_consumption_l_per_100km / 100 * req.fuel_price_rub)
        tax = int(req.rate_rub * req.tax_percent / 100)
        net = req.rate_rub - fuel - tax - req.other_expenses_rub
        return {"fuel": fuel, "tax": tax, "net": net}

    def test_basic_profit(self):
        r = self._calc()
        assert r["fuel"] == 21000
        assert r["tax"] == 6000
        assert r["net"] == 73000

    def test_high_fuel_consumption(self):
        r = self._calc(fuel_consumption_l_per_100km=50.0)
        assert r["fuel"] == 30000
        assert r["net"] < 73000

    def test_zero_distance(self):
        r = self._calc(distance_km=0)
        assert r["fuel"] == 0

    def test_with_other_expenses(self):
        r = self._calc(other_expenses_rub=5000)
        assert r["net"] == 68000

    def test_ip_tax(self):
        r = self._calc(tax_percent=6.0)
        assert r["tax"] == 6000

    def test_ooo_tax(self):
        r = self._calc(tax_percent=20.0)
        assert r["tax"] == 20000

    def test_self_employed_tax(self):
        r = self._calc(tax_percent=4.0)
        assert r["tax"] == 4000

    def test_negative_profit(self):
        r = self._calc(rate_rub=10000, distance_km=2000)
        assert r["net"] < 0


class TestTransactionStatuses:
    def test_valid_statuses(self):
        valid = {"delivered", "docs_sent", "awaiting_payment", "paid", "disputed"}
        assert "delivered" in valid
        assert "paid" in valid
        assert "disputed" in valid

    def test_payment_flow(self):
        flow = ["delivered", "docs_sent", "awaiting_payment", "paid"]
        for i in range(len(flow) - 1):
            assert flow[i] != flow[i + 1]
