import importlib.util
from pathlib import Path

import pytest

REQUIRED_MODULES = ["fastapi", "jinja2", "markdown", "bleach", "pydantic", "yaml"]
missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
if missing:
    pytest.skip(
        f"Skipped integration tests because modules are missing: {', '.join(missing)}",
        allow_module_level=True,
    )

import yaml
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    routers = tmp_path / "routers.yml"
    info = tmp_path / "information.md"
    monkeypatch.setattr(main, "ROUTERS_FILE", routers)
    monkeypatch.setattr(main, "INFORMATION_FILE", info)
    main.cache._routers_mtime = None
    main.cache._info_mtime = None
    main.rate_limiter = main.InMemoryRateLimiter(max_requests=1000, window_seconds=60)
    return TestClient(main.app)


def test_healthcheck(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_invalid_router_record_does_not_break_page(client: TestClient) -> None:
    data = [
        {"name": "broken"},
        {
            "path": "eboltachev/demo",
            "url": "http://example.com/demo",
            "password": "StrongPassword123!",
            "name": "ok",
            "description": "desc",
            "sources": [{"href": "https://github.com"}, {"href": "bad://url"}],
        },
    ]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    response = client.get("/")
    assert response.status_code == 200
    assert "ok" in response.text
    assert "/eboltachev/demo" in response.text
    assert "broken" not in response.text


def test_loads_routers_from_multiple_yaml_documents(client: TestClient) -> None:
    main.ROUTERS_FILE.write_text(
        """
- path: eboltachev/demo-1
  url: http://example.com/demo-1
  password: ""
  name: demo-1
  description: desc-1
---
- path: eboltachev/demo-2
  url: http://example.com/demo-2
  password: ""
  name: demo-2
  description: desc-2
""".strip(),
        encoding="utf-8",
    )

    response = client.get("/")
    assert response.status_code == 200
    assert "/eboltachev/demo-1" in response.text
    assert "/eboltachev/demo-2" in response.text


def test_loads_routers_from_routers_key(client: TestClient) -> None:
    main.ROUTERS_FILE.write_text(
        """
routers:
  - path: eboltachev/demo-3
    url: http://example.com/demo-3
    password: ""
    name: demo-3
    description: desc-3
""".strip(),
        encoding="utf-8",
    )

    response = client.get("/")
    assert response.status_code == 200
    assert "/eboltachev/demo-3" in response.text


def test_external_links_sorted(client: TestClient) -> None:
    data = [
        {
            "path": "eboltachev/demo",
            "url": "http://example.com/demo",
            "password": "StrongPassword123!",
            "name": "demo",
            "description": "desc",
            "sources": [{"href": "https://www.kaggle.com"}, {"href": "https://github.com"}],
        }
    ]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    response = client.get("/")
    assert response.status_code == 200
    github_pos = response.text.find("github.com")
    kaggle_pos = response.text.find("kaggle.com")
    assert github_pos != -1 and kaggle_pos != -1
    assert github_pos < kaggle_pos


def test_information_sanitized(client: TestClient) -> None:
    main.INFORMATION_FILE.write_text("# title\n<script>alert(1)</script>", encoding="utf-8")

    response = client.get("/information")
    assert response.status_code == 200
    assert "<script>" not in response.text


def test_metrics_endpoint(client: TestClient) -> None:
    client.get("/")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text


def test_rate_limit_returns_429(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "ROUTERS_FILE", tmp_path / "routers.yml")
    monkeypatch.setattr(main, "INFORMATION_FILE", tmp_path / "information.md")
    main.ROUTERS_FILE.write_text("[]", encoding="utf-8")
    main.rate_limiter = main.InMemoryRateLimiter(max_requests=1, window_seconds=60)
    local_client = TestClient(main.app)

    first = local_client.get("/")
    second = local_client.get("/")

    assert first.status_code == 200
    assert second.status_code == 429


def test_password_gate_redirect_on_valid_password(client: TestClient) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "StrongPassword123!",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    response = client.post(
        "/go/eboltachev/demo",
        data={"password": "StrongPassword123!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/eboltachev/demo"
    assert "research_access_" in response.headers["set-cookie"]


def test_password_gate_denies_invalid_password(client: TestClient) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "StrongPassword123!",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    response = client.post("/go/eboltachev/demo", data={"password": "wrong"})
    assert response.status_code == 401
    assert "Неверный пароль" in response.text


def test_entrypoint_redirects_to_password_gate_for_protected_route(client: TestClient) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "StrongPassword123!",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    response = client.get("/eboltachev/demo", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/go/eboltachev/demo"


def test_entrypoint_redirects_directly_for_public_route(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    async def fake_proxy_request(_request, target_url: str, path_prefix: str):
        from fastapi.responses import PlainTextResponse

        assert target_url == "http://example.com/demo"
        assert path_prefix == "/eboltachev/demo"
        return PlainTextResponse("proxied", status_code=200)

    monkeypatch.setattr(main, "_proxy_request", fake_proxy_request)
    response = client.get("/eboltachev/demo")
    assert response.status_code == 200
    assert response.text == "proxied"


def test_password_gate_redirects_when_password_not_set(client: TestClient) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    response = client.get("/go/eboltachev/demo", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/eboltachev/demo"


def test_entrypoint_proxies_nested_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    async def fake_proxy_request(_request, target_url: str, path_prefix: str):
        from fastapi.responses import PlainTextResponse

        assert target_url == "http://example.com/demo/assets/app.js"
        assert path_prefix == "/eboltachev/demo"
        return PlainTextResponse("nested-proxied", status_code=200)

    monkeypatch.setattr(main, "_proxy_request", fake_proxy_request)
    response = client.get("/eboltachev/demo/assets/app.js")
    assert response.status_code == 200
    assert response.text == "nested-proxied"


def test_entrypoint_proxies_protected_route_after_password_submit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "StrongPassword123!",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    async def fake_proxy_request(_request, target_url: str, path_prefix: str):
        from fastapi.responses import PlainTextResponse

        assert target_url == "http://example.com/demo"
        assert path_prefix == "/eboltachev/demo"
        return PlainTextResponse("protected-proxied", status_code=200)

    monkeypatch.setattr(main, "_proxy_request", fake_proxy_request)
    submit = client.post(
        "/go/eboltachev/demo",
        data={"password": "StrongPassword123!"},
        follow_redirects=False,
    )
    assert submit.status_code == 303
    cookie_name = next(iter(submit.cookies.keys()))
    client.cookies.set(cookie_name, submit.cookies.get(cookie_name))

    response = client.get("/eboltachev/demo")
    assert response.status_code == 200
    assert response.text == "protected-proxied"


def test_entrypoint_returns_404_for_root_absolute_assets_without_project_prefix(
    client: TestClient,
) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    response = client.get("/openapi.json")
    assert response.status_code == 404


def test_entrypoint_routes_root_absolute_assets_using_referer_context(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    async def fake_proxy_request(_request, target_url: str, path_prefix: str):
        from fastapi.responses import PlainTextResponse

        assert target_url == "http://example.com/demo/openapi.json"
        assert path_prefix == "/eboltachev/demo"
        return PlainTextResponse("asset-proxied", status_code=200)

    monkeypatch.setattr(main, "_proxy_request", fake_proxy_request)
    response = client.get(
        "/openapi.json",
        headers={"referer": "https://research.aicorex.tech/eboltachev/demo/docs"},
    )
    assert response.status_code == 200
    assert response.text == "asset-proxied"


def test_proxy_route_uses_relaxed_csp_for_embedded_apps(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    async def fake_proxy_request(_request, _target_url: str, _path_prefix: str):
        from fastapi.responses import HTMLResponse

        return HTMLResponse("<html><body>ok</body></html>", status_code=200)

    monkeypatch.setattr(main, "_proxy_request", fake_proxy_request)
    response = client.get("/eboltachev/demo/docs")
    assert response.status_code == 200
    assert "script-src 'self' 'unsafe-inline' 'unsafe-eval'" in response.headers[
        "content-security-policy"
    ]


def test_proxy_rewrites_root_absolute_imports_in_javascript(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = [{
        "path": "eboltachev/demo",
        "url": "http://example.com/demo",
        "password": "",
        "name": "demo",
        "description": "desc",
        "sources": [],
    }]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    class UpstreamResponse:
        status_code = 200
        content = b'import "/src/main.ts";\nconst a = "/api/status";'
        headers = {"content-type": "application/javascript; charset=utf-8"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            return

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, **kwargs):
            assert kwargs["url"] == "http://example.com/demo/@vite/client"
            return UpstreamResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    response = client.get("/eboltachev/demo/@vite/client")
    assert response.status_code == 200
    assert '"/eboltachev/demo/src/main.ts"' in response.text
    assert '"/eboltachev/demo/api/status"' in response.text
