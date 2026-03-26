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
    assert "card-number" not in response.text
    assert "title-arrow" not in response.text
    assert "broken" not in response.text


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


def test_name_and_description_are_normalized(client: TestClient) -> None:
    data = [
        {
            "path": "eboltachev/demo",
            "url": "http://example.com/demo",
            "password": "",
            "name": "Multimodal\nIntelligent\nSearch\nSystem",
            "description": "Интеллектуальная\nсистема\nпоиска",
            "sources": [],
        }
    ]
    main.ROUTERS_FILE.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    response = client.get("/")
    assert response.status_code == 200
    assert "Multimodal Intelligent Search System" in response.text
    assert "Интеллектуальная система поиска" in response.text


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


def test_entrypoint_uses_active_path_cookie_for_root_absolute_assets(
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
    client.cookies.set("research_active_path", "eboltachev/demo")

    async def fake_proxy_request(_request, target_url: str, path_prefix: str):
        from fastapi.responses import PlainTextResponse

        assert target_url == "http://example.com/demo/openapi.json"
        assert path_prefix == "/eboltachev/demo"
        return PlainTextResponse("asset-proxied", status_code=200)

    monkeypatch.setattr(main, "_proxy_request", fake_proxy_request)
    response = client.get("/openapi.json")
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
