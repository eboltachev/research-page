from pathlib import Path

from app.rate_limit import SqliteRateLimiter


def test_sqlite_rate_limiter_persists_between_instances(tmp_path: Path) -> None:
    db = tmp_path / "rl.db"

    limiter1 = SqliteRateLimiter(db, max_requests=1, window_seconds=60)
    assert limiter1.allow("ip1") is True
    assert limiter1.allow("ip1") is False

    limiter2 = SqliteRateLimiter(db, max_requests=1, window_seconds=60)
    assert limiter2.allow("ip1") is False
