import importlib.util
import os
from pathlib import Path

import pytest

REQUIRED_MODULES = ["fastapi", "redis", "yaml", "jinja2", "markdown", "bleach", "pydantic"]
missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
if missing:
    reason = (
        "Skipped redis-cluster integration tests because modules are missing: "
        f"{', '.join(missing)}"
    )
    pytest.skip(reason, allow_module_level=True)

from fastapi.testclient import TestClient

import app.main as main


def test_redis_cluster_backend_builds_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "redis-cluster")
    monkeypatch.setenv(
        "RATE_LIMIT_REDIS_CLUSTER_NODES",
        os.getenv("RATE_LIMIT_REDIS_CLUSTER_NODES", "127.0.0.1:7000"),
    )
    monkeypatch.setattr(main, "ROUTERS_FILE", tmp_path / "routers.yml")
    monkeypatch.setattr(main, "INFORMATION_FILE", tmp_path / "information.md")

    main.ROUTERS_FILE.write_text("[]", encoding="utf-8")
    main.rate_limiter = main._build_rate_limiter()

    client = TestClient(main.app)
    response = client.get("/healthz")
    assert response.status_code == 200
