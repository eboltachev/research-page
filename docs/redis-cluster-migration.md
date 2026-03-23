# SQLite -> Redis Cluster migration for rate limiting

## Target
Перевести backend rate-limit на `redis-cluster` для HA-сценариев.

## Шаги
1. Развернуть Redis Cluster (минимум 3 master nodes).
2. Настроить переменные окружения приложения:
   - `RATE_LIMIT_BACKEND=redis-cluster`
   - `RATE_LIMIT_REDIS_CLUSTER_NODES=redis-1:6379,redis-2:6379,redis-3:6379`
3. Выполнить canary rollout одной реплики.
4. Проверить метрики `/metrics`:
   - `http_429_total`
   - `http_slow_requests_total`
5. Дорастить rollout до 100%.

## Rollback
- Вернуть `RATE_LIMIT_BACKEND=sqlite` и `RATE_LIMIT_SQLITE_PATH`.
- Перезапустить сервис.
