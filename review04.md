# review04 — закрытие замечаний из review03 и новые предложения

Дата: 2026-03-23

## 1) Устранение замечаний из review03

### Критические/приоритетные
1. **Метрики Prometheus-формата**
   - Добавлен модуль `app/metrics.py` с генерацией экспозиции в формате Prometheus.
   - Endpoint `/metrics` переведён на использование этого форматтера.

2. **Persistent rate-limit backend**
   - Реализованы backends в `app/rate_limit.py`:
     - `InMemoryRateLimiter`
     - `SqliteRateLimiter` (persisted backend)
     - `RedisRateLimiter` (для масштабируемого сценария)
   - В `app/main.py` добавлен выбор backend через env (`RATE_LIMIT_BACKEND`: `memory|sqlite|redis`).

3. **Усиленная валидация схемы маршрутов**
   - Валидация `path` вынесена в `app/validation.py`.
   - `RouterRecord` использует typed validation и нормализацию для `<USER>/<PATH>`.

4. **Покрытие тестами**
   - Добавлены тесты:
     - property-style тесты валидации пути (`tests/test_validation_properties.py`)
     - тест persisted rate-limit (`tests/test_rate_limit_sqlite.py`)
     - тест формата метрик (`tests/test_metrics_format.py`)
   - Существующие тесты сохранены и расширены.

5. **Release security pipeline (SBOM + image scan)**
   - Добавлен workflow `/.github/workflows/release-security.yml`:
     - сборка контейнера
     - генерация SBOM (SPDX)
     - сканирование Trivy
     - загрузка SARIF и SBOM-артефакта

---

## 2) Результат

Все замечания из `review03.md` реализованы.

---

## 3) Дополнительные предложения

1. Добавить интеграционные тесты для `RATE_LIMIT_BACKEND=redis` в CI через service containers.
2. Добавить алерты по метрикам (`http_429_total`, latency) в мониторинг.
3. Добавить подпись контейнерных образов (cosign) в release pipeline.
4. Добавить policy-as-code (OPA/Conftest) для проверки Docker/Compose конфигов.
5. Добавить миграцию с SQLite rate-limit на Redis cluster для HA-инсталляций.
