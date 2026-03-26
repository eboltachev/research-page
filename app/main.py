from __future__ import annotations

import json
import logging
import os
import threading
import time
import hmac
import hashlib
from base64 import urlsafe_b64decode, urlsafe_b64encode
from pathlib import Path
from urllib.parse import urlparse

import bleach
import httpx
import markdown
import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.metrics import prometheus_text
from app.rate_limit import (
    InMemoryRateLimiter,
    RedisClusterRateLimiter,
    RedisRateLimiter,
    SqliteRateLimiter,
)
from app.validation import normalize_and_validate_path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIGS_DIR = BASE_DIR / "configs"
ROUTERS_FILE = CONFIGS_DIR / "routers.yml"
INFORMATION_FILE = CONFIGS_DIR / "information.md"

RATE_LIMIT_MAX_REQUESTS = 120
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_KEY_PREFIX = os.getenv("RATE_LIMIT_KEY_PREFIX", "research:rl")
PASSWORD_GATE_SECRET = os.getenv("PASSWORD_GATE_SECRET", "change-me-in-production")
PASSWORD_GATE_COOKIE_MAX_AGE = int(os.getenv("PASSWORD_GATE_COOKIE_MAX_AGE", "43200"))

ICON_BY_DOMAIN = {
    "github.com": "/static/icons/github.svg",
}

ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS).union(
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "span", "hr", "br", "img"}
)
ALLOWED_ATTRIBUTES = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title"],
    "span": ["class"],
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger = logging.getLogger("research_page")
logger.handlers.clear()
logger.addHandler(handler)
logger.setLevel(logging.INFO)

CORE_ROUTES = {"/", "/information", "/healthz", "/metrics"}


class SourceRecord(BaseModel):
    href: str
    label: str | None = None


class ExternalLink(BaseModel):
    href: str
    title: str
    icon: str


class RouterRecord(BaseModel):
    path: str = Field(min_length=3)
    url: str
    password: str = ""
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    sources: list[SourceRecord] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def coerce_sources(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        raw_sources = value.get("sources")
        if raw_sources is None and "external" in value:
            raw_sources = value.get("external")

        if isinstance(raw_sources, list):
            normalized: list[dict[str, str]] = []
            for item in raw_sources:
                if isinstance(item, str):
                    normalized.append({"href": item})
                elif isinstance(item, dict):
                    href = item.get("href") or item.get("url")
                    if not href:
                        normalized.append(item)
                        continue
                    label = item.get("label") or item.get("name")
                    normalized.append({"href": href, "label": label})
                else:
                    normalized.append(item)
            value["sources"] = normalized

        return value


    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be a valid http/https URL")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_and_validate_path(value)


class ConfigCache:
    def __init__(self) -> None:
        self._routers_mtime: float | None = None
        self._routers_cards: list[dict[str, object]] = []
        self._info_mtime: float | None = None
        self._info_html: str = "<p>Information is not configured yet.</p>"
        self._lock = threading.Lock()

    def get_cards(self) -> list[dict[str, object]]:
        mtime = ROUTERS_FILE.stat().st_mtime if ROUTERS_FILE.exists() else None
        with self._lock:
            if mtime != self._routers_mtime:
                self._routers_cards = _load_routers_uncached()
                self._routers_mtime = mtime
            return self._routers_cards

    def get_information_html(self) -> str:
        mtime = INFORMATION_FILE.stat().st_mtime if INFORMATION_FILE.exists() else None
        with self._lock:
            if mtime != self._info_mtime:
                self._info_html = _load_information_html_uncached()
                self._info_mtime = mtime
            return self._info_html


metrics = {
    "http_requests_total": 0,
    "http_429_total": 0,
    "routers_reload_total": 0,
    "information_reload_total": 0,
    "http_slow_requests_total": 0,
}

cache = ConfigCache()


def _build_rate_limiter():
    backend = os.getenv("RATE_LIMIT_BACKEND", "memory").lower()
    ssl_enabled = os.getenv("RATE_LIMIT_REDIS_TLS", "false").lower() == "true"
    ssl_ca = os.getenv("RATE_LIMIT_REDIS_CA_CERT")
    ssl_cert = os.getenv("RATE_LIMIT_REDIS_CLIENT_CERT")
    ssl_key = os.getenv("RATE_LIMIT_REDIS_CLIENT_KEY")

    if backend == "sqlite":
        db_path = os.getenv("RATE_LIMIT_SQLITE_PATH", "/tmp/research_rate_limit.db")
        return SqliteRateLimiter(db_path, RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)
    if backend == "redis":
        redis_url = os.getenv("RATE_LIMIT_REDIS_URL", "redis://localhost:6379/0")
        return RedisRateLimiter(
            redis_url,
            RATE_LIMIT_MAX_REQUESTS,
            RATE_LIMIT_WINDOW_SECONDS,
            ssl_enabled=ssl_enabled,
            ssl_ca_cert=ssl_ca,
            ssl_certfile=ssl_cert,
            ssl_keyfile=ssl_key,
        )
    if backend == "redis-cluster":
        nodes = os.getenv("RATE_LIMIT_REDIS_CLUSTER_NODES", "localhost:6379")
        return RedisClusterRateLimiter(
            nodes,
            RATE_LIMIT_MAX_REQUESTS,
            RATE_LIMIT_WINDOW_SECONDS,
            ssl_enabled=ssl_enabled,
            ssl_ca_cert=ssl_ca,
            ssl_certfile=ssl_cert,
            ssl_keyfile=ssl_key,
        )
    return InMemoryRateLimiter(RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


rate_limiter = _build_rate_limiter()

app = FastAPI(title="research.aicorex.tech")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def _normalize_hostname(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    return host or url


def _icon_for_url(url: str) -> str:
    domain = _normalize_hostname(url)
    return ICON_BY_DOMAIN.get(domain, "/static/icons/web.svg")


def _normalize_external(external: list[SourceRecord]) -> list[dict[str, str]]:
    links: list[ExternalLink] = []
    for source in external:
        href = source.href
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        hostname = _normalize_hostname(href)
        links.append(
            ExternalLink(
                href=href,
                title=source.label or hostname,
                icon=_icon_for_url(href),
            )
        )

    # links.sort(key=lambda x: _normalize_hostname(x.href))
    return [item.model_dump() for item in links]


def _load_routers_uncached() -> list[dict[str, object]]:
    metrics["routers_reload_total"] += 1
    if not ROUTERS_FILE.exists():
        logger.warning("Missing routers file: %s", ROUTERS_FILE)
        return []

    with ROUTERS_FILE.open("r", encoding="utf-8") as f:
        documents = list(yaml.safe_load_all(f))

    raw: list[object] = []
    for doc_idx, doc in enumerate(documents, start=1):
        if doc is None:
            continue
        if isinstance(doc, list):
            raw.extend(doc)
            continue
        if isinstance(doc, dict) and isinstance(doc.get("routers"), list):
            raw.extend(doc["routers"])
            continue
        logger.warning(
            "Skipped YAML document %s in configs/routers.yml: expected list or {routers: [...]}",
            doc_idx,
        )

    cards: list[dict[str, object]] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            logger.warning("Skipped record %s: expected object", idx)
            continue

        try:
            parsed = RouterRecord.model_validate(item)
        except ValidationError as exc:
            logger.warning("Skipped record %s: %s", idx, exc.errors())
            continue

        cards.append(
            {
                "path": parsed.path,
                "url": parsed.url,
                "password": parsed.password,
                "name": parsed.name,
                "description": parsed.description,
                "sources": _normalize_external(parsed.sources),
            }
        )

    return cards


def _load_information_html_uncached() -> str:
    metrics["information_reload_total"] += 1
    if not INFORMATION_FILE.exists():
        return "<p>Information is not configured yet.</p>"

    text = INFORMATION_FILE.read_text(encoding="utf-8")
    html = markdown.markdown(text, extensions=["extra", "sane_lists"])
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


@app.middleware("http")
async def security_and_rate_limit(request: Request, call_next):
    metrics["http_requests_total"] += 1

    client = request.client.host if request.client else "unknown"
    rate_key = f"{RATE_LIMIT_KEY_PREFIX}:{client}"
    if not rate_limiter.allow(rate_key):
        metrics["http_429_total"] += 1
        return JSONResponse(status_code=429, content={"detail": "Too Many Requests"})

    started = time.perf_counter()
    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if (
        request.url.path.startswith("/go/")
        or request.url.path.startswith("/static/")
        or request.url.path in CORE_ROUTES
    ):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self'; "
            "script-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' data: blob: http: https:; "
            "img-src * data: blob:; "
            "style-src 'self' 'unsafe-inline' http: https:; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' http: https:; "
            "connect-src * data: blob:; "
            "font-src 'self' data: http: https:; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms >= 1000:
        metrics["http_slow_requests_total"] += 1
    logger.info(
        "request path=%s status=%s latency_ms=%.2f",
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "cards": cache.get_cards(),
        },
    )


@app.get("/information", response_class=HTMLResponse)
def information(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "information.html",
        {
            "content": cache.get_information_html(),
        },
    )




def _find_router_by_path(path: str) -> dict[str, object] | None:
    norm = path.strip("/")
    for card in cache.get_cards():
        if card.get("path") == norm:
            return card
    return None


def _resolve_router_target(path: str) -> tuple[dict[str, object] | None, str | None]:
    norm = path.strip("/")
    cards = sorted(cache.get_cards(), key=lambda item: len(str(item.get("path", ""))), reverse=True)

    for card in cards:
        card_path = str(card.get("path", "")).strip("/")
        if not card_path:
            continue

        if norm == card_path:
            return card, str(card.get("url"))

        prefix = f"{card_path}/"
        if norm.startswith(prefix):
            suffix = norm[len(prefix) :]
            target = str(card.get("url")).rstrip("/")
            if suffix:
                target = f"{target}/{suffix}"
            return card, target

    return None, None


def _access_cookie_name(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"research_access_{digest}"


def _build_access_cookie_value(path: str) -> str:
    issued_at = str(int(time.time()))
    payload = f"{path}:{issued_at}"
    signature = hmac.new(
        PASSWORD_GATE_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    token = f"{payload}:{signature}"
    return urlsafe_b64encode(token.encode("utf-8")).decode("ascii")


def _has_access_cookie(request: Request, path: str) -> bool:
    raw = request.cookies.get(_access_cookie_name(path))
    if not raw:
        return False
    try:
        decoded = urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
        cookie_path, issued_at_str, signature = decoded.rsplit(":", 2)
        issued_at = int(issued_at_str)
    except (ValueError, UnicodeDecodeError):
        return False

    if cookie_path != path:
        return False
    if time.time() - issued_at > PASSWORD_GATE_COOKIE_MAX_AGE:
        return False

    payload = f"{cookie_path}:{issued_at_str}"
    expected = hmac.new(
        PASSWORD_GATE_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


async def _proxy_request(request: Request, target_url: str, path_prefix: str) -> Response:
    hop_by_hop_headers = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
    outgoing_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in hop_by_hop_headers and key.lower() != "host"
    }
    outgoing_headers["accept-encoding"] = "identity"
    body = await request.body()

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        upstream = await client.request(
            method=request.method,
            url=target_url,
            headers=outgoing_headers,
            params=request.query_params,
            content=body,
        )

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in hop_by_hop_headers
    }
    response_headers.pop("content-length", None)

    location = response_headers.get("location")
    if location and location.startswith("/"):
        response_headers["location"] = f"{path_prefix}{location}"

    content = upstream.content
    content_type = upstream.headers.get("content-type", "")
    if "text/html" in content_type and content:
        html = content.decode("utf-8", errors="ignore")
        html = html.replace('href="/', f'href="{path_prefix}/')
        html = html.replace('src="/', f'src="{path_prefix}/')
        html = html.replace("url: '/", f"url: '{path_prefix}/")
        content = html.encode("utf-8")
        response_headers.pop("content-encoding", None)
        response_headers.pop("etag", None)

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


@app.get("/go/{research_path:path}", response_class=HTMLResponse)
def research_password_form(request: Request, research_path: str) -> HTMLResponse:
    card = _find_router_by_path(research_path)
    if not card:
        raise HTTPException(status_code=404, detail="Research not found")

    if not card.get("password"):
        return RedirectResponse(url=f"/{card.get('path')}", status_code=303)

    return templates.TemplateResponse(
        request,
        "password_gate.html",
        {"research_path": research_path, "research_name": card.get("name"), "error": None},
    )


@app.post("/go/{research_path:path}", response_model=None)
def research_password_submit(
    request: Request,
    research_path: str,
    password: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    card = _find_router_by_path(research_path)
    if not card:
        raise HTTPException(status_code=404, detail="Research not found")

    if password != card.get("password"):
        return templates.TemplateResponse(
            request,
            "password_gate.html",
            {
                "research_path": research_path,
                "research_name": card.get("name"),
                "error": "Неверный пароль",
            },
            status_code=401,
        )

    response = RedirectResponse(url=f"/{card.get('path')}", status_code=303)
    response.set_cookie(
        key=_access_cookie_name(str(card.get("path"))),
        value=_build_access_cookie_value(str(card.get("path"))),
        max_age=PASSWORD_GATE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
    )
    return response

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics_endpoint() -> str:
    return prometheus_text(metrics)


@app.api_route(
    "/{research_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    response_model=None,
)
async def research_entrypoint(request: Request, research_path: str) -> Response:
    card, target_url = _resolve_router_target(research_path)
    if not card or not target_url:
        active_path = request.cookies.get("research_active_path")
        active_card = _find_router_by_path(active_path) if active_path else None
        if active_card:
            target_url = f"{str(active_card.get('url')).rstrip('/')}/{research_path.strip('/')}"
            card = active_card

    if not card or not target_url:
        raise HTTPException(status_code=404, detail="Research not found")

    if card.get("password"):
        card_path = str(card.get("path"))
        if not _has_access_cookie(request, card_path):
            return RedirectResponse(url=f"/go/{card_path}", status_code=307)

    response = await _proxy_request(request, target_url, f"/{card.get('path')}")
    response.set_cookie(
        key="research_active_path",
        value=str(card.get("path")),
        max_age=PASSWORD_GATE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
    )
    return response
