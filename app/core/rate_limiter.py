import time
from app.core.redis_client import redis_client

MAX_TOKENS = 5          # bucket capacity
REFILL_RATE = 1          # tokens added per second
REFILL_INTERVAL = 1.0    # seconds


def is_allowed(client_id: str) -> bool:
    key = f"ratelimit:{client_id}"
    now = time.time()

    bucket = redis_client.hgetall(key)

    if bucket:
        tokens = float(bucket["tokens"])
        last_refill = float(bucket["last_refill"])
    else:
        tokens = MAX_TOKENS
        last_refill = now

    # Refill tokens based on elapsed time since last request
    elapsed = now - last_refill
    refill_amount = elapsed * (REFILL_RATE / REFILL_INTERVAL)
    tokens = min(MAX_TOKENS, tokens + refill_amount)

    if tokens < 1:
        # Not enough tokens — reject, but still save state
        redis_client.hset(key, mapping={"tokens": tokens, "last_refill": now})
        redis_client.expire(key, 60)
        return False

    # Enough tokens — consume one, allow request
    tokens -= 1
    redis_client.hset(key, mapping={"tokens": tokens, "last_refill": now})
    redis_client.expire(key, 60)
    return True