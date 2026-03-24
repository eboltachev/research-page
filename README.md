# research-page

Минимальный production-ready микросервис для домена `research.aicorex.tech`.

## Запуск

```bash
docker compose up --build -d
```

## Конфигурация

- `configs/routers.yml` — список карточек исследований (`path`, `url`, `password`, `name`, `description`, `sources`).
  - `sources` поддерживает формат `[{ href, label? }]`.
  - Для обратной совместимости также принимается устаревшее поле `external`.
- `configs/information.md` — markdown-контент страницы `/information`.

Изменения в конфигурации подхватываются динамически, с кэшированием по `mtime` файлов.

## Надёжность и безопасность

- `/healthz` для liveness/readiness проверок.
- `/metrics` в формате Prometheus.
- Fail-soft обработка невалидных записей в `configs/routers.yml`.
- Pydantic-валидация схемы и формата `path` (`<USER>/<PATH>`).
- Санитизация HTML на странице `/information`.
- CSP и базовые security headers выставляются middleware.
- Поддержка backend rate-limit: `memory`, `sqlite`, `redis`, `redis-cluster`.
- Namespace для ключей rate-limit: `RATE_LIMIT_KEY_PREFIX` (по умолчанию `research:rl`).
- mTLS для Redis/Redis Cluster через `RATE_LIMIT_REDIS_TLS=true` и пути к сертификатам.

## Зависимости

Управление зависимостями выполняется через `uv` (`pyproject.toml` + `uv.lock`).

## Локальные проверки

```bash
uv pip install --system -r uv.lock
ruff check .
pytest -q
```

## Интеграционный smoke test

```bash
./scripts/smoke_container.sh
```

## Release security

Workflow `release-security.yml` собирает образ, формирует SBOM, запускает image scan и подписывает образ через cosign.

## Monitoring

- Alerting rules: `monitoring/alerts.yml`.
- SLO/SLA dashboard template: `monitoring/dashboard-slo.json`.
- Prometheus config with `remote_write`: `monitoring/prometheus.yml`.
- Migration guide to Redis Cluster: `docs/redis-cluster-migration.md`.

## Policy as code

Conftest policies are located in `policy/` and validated in CI job `policy-check`.
