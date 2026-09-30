"""Límite de peticiones con ventana fija en Redis."""

from redis.asyncio import Redis

from app.core.config import RateLimit
from app.core.errors import RateLimitedError
from app.core.security import hash_token


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, scope: str, key: str, rule: RateLimit) -> None:
        """Cuenta una petición; lanza RateLimitedError (429) si se supera `rule`.

        La clave se guarda con hash para no dejar correos ni IPs en claro en Redis.
        """
        redis_key = f"rl:{scope}:{hash_token(key)[:32]}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, rule.window_seconds, nx=True)
            pipe.ttl(redis_key)
            count, _, ttl = await pipe.execute()
        if int(count) > rule.limit:
            raise RateLimitedError(retry_after=max(int(ttl), 1))
