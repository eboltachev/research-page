from __future__ import annotations

import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._events: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        threshold = now - self.window_seconds
        with self._lock:
            events = [t for t in self._events[key] if t >= threshold]
            if len(events) >= self.max_requests:
                self._events[key] = events
                return False
            events.append(now)
            self._events[key] = events
            return True


class SqliteRateLimiter:
    def __init__(self, db_path: str | Path, max_requests: int, window_seconds: int) -> None:
        self.db_path = str(db_path)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS requests (key TEXT NOT NULL, ts REAL NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_key_ts ON requests(key, ts)")
            conn.commit()

    def allow(self, key: str) -> bool:
        now = time.time()
        threshold = now - self.window_seconds
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM requests WHERE ts < ?", (threshold,))
            count = conn.execute(
                "SELECT COUNT(*) FROM requests WHERE key = ? AND ts >= ?", (key, threshold)
            ).fetchone()[0]
            if count >= self.max_requests:
                conn.commit()
                return False
            conn.execute("INSERT INTO requests(key, ts) VALUES(?, ?)", (key, now))
            conn.commit()
            return True


class RedisRateLimiter:
    def __init__(
        self,
        redis_url: str,
        max_requests: int,
        window_seconds: int,
        *,
        ssl_enabled: bool = False,
        ssl_ca_cert: str | None = None,
        ssl_certfile: str | None = None,
        ssl_keyfile: str | None = None,
    ) -> None:
        try:
            import redis
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError("redis package is required for RedisRateLimiter") from exc

        self.max_requests = max_requests
        self.window_seconds = window_seconds
        kwargs = {
            "decode_responses": True,
            "ssl": ssl_enabled,
            "ssl_ca_certs": ssl_ca_cert,
            "ssl_certfile": ssl_certfile,
            "ssl_keyfile": ssl_keyfile,
        }
        filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        self.client = redis.from_url(redis_url, **filtered_kwargs)

    def allow(self, key: str) -> bool:
        current = self.client.incr(key)
        if current == 1:
            self.client.expire(key, self.window_seconds)
        return current <= self.max_requests


class RedisClusterRateLimiter:
    def __init__(
        self,
        startup_nodes: str,
        max_requests: int,
        window_seconds: int,
        *,
        ssl_enabled: bool = False,
        ssl_ca_cert: str | None = None,
        ssl_certfile: str | None = None,
        ssl_keyfile: str | None = None,
    ) -> None:
        try:
            from redis.cluster import RedisCluster
        except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("redis package with cluster support is required") from exc

        nodes = []
        for host_port in startup_nodes.split(","):
            host, port = host_port.split(":", 1)
            nodes.append({"host": host.strip(), "port": int(port)})

        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.client = RedisCluster(
            startup_nodes=nodes,
            decode_responses=True,
            ssl=ssl_enabled,
            ssl_ca_certs=ssl_ca_cert,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
        )

    def allow(self, key: str) -> bool:
        current = self.client.incr(key)
        if current == 1:
            self.client.expire(key, self.window_seconds)
        return current <= self.max_requests
