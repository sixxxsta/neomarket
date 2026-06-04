from dataclasses import dataclass


@dataclass
class RetryPolicy:
    max_retries: int = 5
    initial_delay_ms: int = 200
    max_delay_ms: int = 30000
    backoff_multiplier: float = 2.0
    jitter: bool = True


class EventConsumerWithRetry:
    def __init__(self, redis_client, service_name, source, retry_policy):
        self.redis_client = redis_client
        self.service_name = service_name
        self.source = source
        self.retry_policy = retry_policy

    def consume_with_retry(self, handler, batch_size=20, block_ms=5000):
        return None
