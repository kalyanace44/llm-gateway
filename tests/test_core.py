"""Tests for LLM Gateway core modules."""
import pytest

from gateway.config import BackendConfig, GatewayConfig
from gateway.core.auth import AuthManager
from gateway.core.backends import Backend, BackendError
from gateway.core.costs import UsageTracker
from gateway.core.limiter import RateLimiter
from gateway.core.metrics import record_backend_error, record_request, update_backend_health
from gateway.core.router import Router


class TestAuth:
    def test_register_and_validate(self):
        auth = AuthManager()
        auth.register_key("key1", "test")
        assert auth.validate("key1") is not None
        assert auth.validate("wrong") is None

    def test_revoke(self):
        auth = AuthManager()
        auth.register_key("key1", "test")
        auth.revoke_key("key1")
        assert auth.validate("key1") is None

    def test_budget_exceeded(self):
        auth = AuthManager()
        k = auth.register_key("key1", "test", budget_usd=0.01)
        k.spent_usd = 0.02
        assert auth.validate("key1") is None

    def test_model_access(self):
        auth = AuthManager()
        k = auth.register_key("key1", "test", allowed_models=["gpt-4o"])
        assert auth.check_model_access(k, "gpt-4o")
        assert not auth.check_model_access(k, "claude-sonnet-4.5")

    def test_unrestricted_model_access(self):
        auth = AuthManager()
        k = auth.register_key("key1", "test")
        assert auth.check_model_access(k, "anything")


class TestRateLimiter:
    def test_allows_under_limit(self):
        lim = RateLimiter(default_rpm=60)
        ok, _ = lim.check_request("k1")
        assert ok

    def test_burst_capacity(self):
        lim = RateLimiter(default_rpm=2)
        # Capacity = 2 * 1.5 = 3
        assert lim.check_request("k1")[0]
        assert lim.check_request("k1")[0]
        assert lim.check_request("k1")[0]
        # 4th should fail (bucket empty)
        ok, wait = lim.check_request("k1")
        assert not ok
        assert wait > 0


class TestCosts:
    def test_count_tokens(self):
        t = UsageTracker()
        assert t.count_tokens("hello world") > 0

    def test_calculate_cost(self):
        t = UsageTracker()
        cost = t.calculate_cost("claude-sonnet-4.5", 1000, 500)
        assert cost == pytest.approx(0.003 + 0.0075, abs=0.0001)

    def test_record_and_summary(self):
        t = UsageTracker()
        t.record("h1", "claude-sonnet-4.5", "b1", 100, 50, 200.0)
        s = t.get_summary()
        assert s["requests"] == 1
        assert s["tokens"] == 150

    def test_key_usage_tracking(self):
        t = UsageTracker()
        t.record("h1", "claude-sonnet-4.5", "b1", 1000, 500, 100.0)
        usage = t.get_key_usage("h1")
        assert usage["requests"] == 1
        assert usage["total_cost"] > 0


class TestRouter:
    def _make_router(self, models=None, weight=1.0, priority=0):
        cfg = BackendConfig(
            name="test", provider="openai", base_url="http://fake:8000/v1",
            models=models or ["m1"], weight=weight, priority=priority,
        )
        return Router([Backend(cfg)], GatewayConfig())

    def test_resolve_backend(self):
        r = self._make_router()
        assert r.resolve_backend("m1").name == "test"

    def test_no_backend_raises(self):
        r = self._make_router(models=["m1"])
        with pytest.raises(BackendError):
            r.resolve_backend("nonexistent")

    def test_available_models(self):
        r = self._make_router(models=["m1", "m2"])
        models = r.available_models()
        assert len(models) == 2

    def test_deregister(self):
        r = self._make_router()
        r.deregister_backend("test")
        with pytest.raises(BackendError):
            r.resolve_backend("m1")


class TestMetrics:
    def test_record_request_no_crash(self):
        record_request("m1", "b1", "key123", 100, 50, 1.5, 0.01)

    def test_record_error_no_crash(self):
        record_backend_error("b1", "timeout")

    def test_update_health_no_crash(self):
        update_backend_health("b1", True)
        update_backend_health("b1", False)
