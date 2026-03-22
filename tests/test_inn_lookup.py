"""Tests for /api/inn-lookup endpoint."""
import pytest


def test_inn_lookup_router_exists():
    from app.api.routes.inn_lookup import router
    assert router is not None


def test_inn_lookup_routes():
    from app.api.routes.inn_lookup import router
    paths = [r.path for r in router.routes]
    assert any("inn" in p.lower() or "lookup" in p.lower() for p in paths)
