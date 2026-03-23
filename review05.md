# review05 — устранение замечаний из review04 и дальнейшие предложения

Дата: 2026-03-23

## 1) Что исправлено

1. **Redis integration в CI**
   - Добавлена job `redis-integration` в `.github/workflows/ci.yml` с `services.redis`.
   - Добавлен тест `tests/test_redis_backend.py`.

2. **Алертинг по метрикам**
   - Добавлен файл правил `monitoring/alerts.yml` с алертами:
     - High 429
     - Slow requests
     - Target down
   - Введён счётчик `http_slow_requests_total`.

3. **Подпись контейнеров (cosign)**
   - `release-security.yml` дополнен установкой cosign и keyless signing шагом.

4. **Policy-as-code**
   - Добавлены OPA/Conftest политики для Dockerfile и docker-compose.
   - Добавлена CI job `policy-check`.

5. **Миграция SQLite -> Redis Cluster**
   - Добавлен backend `redis-cluster` в приложении.
   - Добавлена инструкция `docs/redis-cluster-migration.md`.

---

## 2) Результат

Все замечания из `review04.md` реализованы.

---

## 3) Дополнительные предложения

1. Вынести rate-limit ключ в отдельный namespace (например, `research:rl:<ip>`).
2. Добавить mTLS между приложением и Redis.
3. Добавить SLO/SLA дашборд (latency/error-rate).
4. Автоматизировать тесты миграции backend через ephemeral Redis cluster в CI.
5. Поддержать remote-write для метрик в централизованный мониторинг.
