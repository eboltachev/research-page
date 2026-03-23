# review06 — устранение замечаний из review05 и дальнейшие предложения

Дата: 2026-03-23

## 1) Что исправлено

1. **Namespace ключей rate-limit**
   - Добавлен префикс `RATE_LIMIT_KEY_PREFIX` (default: `research:rl`).
   - В middleware ключ формируется как `<prefix>:<client_ip>`.

2. **mTLS для Redis**
   - Для `redis` и `redis-cluster` backend добавлены TLS-параметры:
     - `RATE_LIMIT_REDIS_TLS`
     - `RATE_LIMIT_REDIS_CA_CERT`
     - `RATE_LIMIT_REDIS_CLIENT_CERT`
     - `RATE_LIMIT_REDIS_CLIENT_KEY`

3. **SLO/SLA дашборд**
   - Добавлен шаблон дашборда `monitoring/dashboard-slo.json`.
   - Добавлена метрика `http_slow_requests_total` для отслеживания деградации latency.

4. **Автотесты миграции backend с ephemeral Redis cluster**
   - В CI добавлена job `redis-cluster-integration` (service container cluster).
   - Добавлен тест `tests/test_redis_cluster_backend.py`.

5. **Remote-write поддержка метрик**
   - Добавлен `monitoring/prometheus.yml` с секцией `remote_write`.

---

## 2) Результат

Все замечания из `review05.md` устранены.

---

## 3) Дополнительные предложения

1. Добавить e2e нагрузочный тест для проверки корректности лимитирования под burst-трафиком.
2. Добавить ротацию TLS-сертификатов Redis через секрет-менеджер (Vault/KMS).
3. Добавить golden-tests для `monitoring/prometheus.yml` и `monitoring/dashboard-slo.json`.
4. Добавить в CI проверку подписи контейнера после cosign sign (cosign verify).
5. Добавить SLO error budget burn-rate алерты (multi-window, multi-burn).
