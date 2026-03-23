from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import bleach
import markdown
import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, field_validator

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

ICON_BY_DOMAIN = {
    "github.com": "/static/icons/github.svg",
    "kaggle.com": "/static/icons/kaggle.svg",
    "elibrary.ru": "/static/icons/elibrary.svg",
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


class ExternalLink(BaseModel):
    url: str
    name: str
    icon: str


class RouterRecord(BaseModel):
    path: str = Field(min_length=3)
    url: str
    password: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    external: list[str]


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


def _resource_name(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    return host or url


def _icon_for_url(url: str) -> str:
    domain = _resource_name(url)
    return ICON_BY_DOMAIN.get(domain, "/static/icons/web.svg")


def _normalize_external(external: list[str], idx: int) -> list[dict[str, str]]:
    links: list[ExternalLink] = []
    for raw_url in external:
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            logger.warning("Record %s: skipped invalid external url '%s'", idx, raw_url)
            continue

        links.append(
            ExternalLink(url=raw_url, name=_resource_name(raw_url), icon=_icon_for_url(raw_url))
        )

    links.sort(key=lambda x: x.name)
    return [item.model_dump() for item in links]


def _load_routers_uncached() -> list[dict[str, object]]:
    metrics["routers_reload_total"] += 1
    if not ROUTERS_FILE.exists():
        logger.warning("Missing routers file: %s", ROUTERS_FILE)
        return []

    with ROUTERS_FILE.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []

    if not isinstance(raw, list):
        logger.error("configs/routers.yml must contain a list")
        return []

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
                "external": _normalize_external(parsed.external, idx),
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
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self'; "
        "script-src 'none'; "
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


@app.get("/go/{research_path:path}", response_class=HTMLResponse)
def research_password_form(request: Request, research_path: str) -> HTMLResponse:
    card = _find_router_by_path(research_path)
    if not card:
        raise HTTPException(status_code=404, detail="Research not found")

    return templates.TemplateResponse(
        request,
        "password_gate.html",
        {"research_path": research_path, "research_name": card.get("name"), "error": None},
    )


@app.post("/go/{research_path:path}")
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

    return RedirectResponse(url=str(card.get("url")), status_code=303)

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics_endpoint() -> str:
    return prometheus_text(metrics)
