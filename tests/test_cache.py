import time

from tenk.cache import JsonCache


def test_round_trip(tmp_path):
    cache = JsonCache(tmp_path / "c.sqlite3")
    cache.set("k", {"a": [1, 2]}, ttl_seconds=60)
    assert cache.get("k") == {"a": [1, 2]}


def test_missing_key_returns_none():
    assert JsonCache(":memory:").get("nope") is None


def test_expired_entry_is_dropped(monkeypatch):
    cache = JsonCache(":memory:")
    cache.set("k", 1, ttl_seconds=10)
    real_time = time.time()
    monkeypatch.setattr(time, "time", lambda: real_time + 11)
    assert cache.get("k") is None
