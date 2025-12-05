from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Dict,
    Generic,
    Iterable,
    MutableMapping,
    Optional,
    Sequence,
    TypeVar,
)


class CircuitState(str, Enum):
    """Circuit breaker states tracked per domain."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class RetryPolicy:
    """Retry settings with exponential backoff and jitter."""

    max_retries: int = 3
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 8.0
    jitter_factor: float = 0.1
    retryable_statuses: Sequence[int] = field(
        default_factory=lambda: [429, 500, 502, 503, 504]
    )
    retryable_exceptions: Sequence[str] = field(
        default_factory=lambda: [
            "TimeoutError",
            "ConnectionError",
            "OSError",
        ]
    )


@dataclass(frozen=True)
class RateLimitPolicy:
    """Per-domain token bucket parameters for politeness."""

    requests_per_second: float = 1.0
    burst: int = 1


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Thresholds for moving the circuit between CLOSED, OPEN, HALF-OPEN."""

    error_rate_threshold: float = 0.5
    minimum_volume: int = 10
    cooldown_seconds: float = 30.0
    half_open_max_requests: int = 1


@dataclass(frozen=True)
class RobotsConfig:
    """Robots.txt enforcement and caching options."""

    enabled: bool = True
    cache_ttl_seconds: float = 3600.0
    respect_crawl_delay: bool = True
    user_agent: str = "ladon-bot"
    allow_on_failure: bool = False


@dataclass(frozen=True)
class Timeouts:
    """Network timeout limits (seconds) applied to requests."""

    connect: float = 5.0
    read: float = 10.0
    total: float = 20.0


@dataclass(frozen=True)
class HttpClientConfig:
    """Top-level configuration for the synchronous HTTP client."""

    retry: RetryPolicy = field(default_factory=RetryPolicy)
    rate_limit: RateLimitPolicy = field(default_factory=RateLimitPolicy)
    circuit_breaker: CircuitBreakerConfig = field(
        default_factory=CircuitBreakerConfig
    )
    robots: RobotsConfig = field(default_factory=RobotsConfig)
    timeouts: Timeouts = field(default_factory=Timeouts)
    user_agent: str = "ladon/0.0.1"
    default_headers: Dict[str, str] = field(default_factory=dict[str, str])
    proxy_url: Optional[str] = None
    max_download_bytes: Optional[int] = None
    allowed_content_types: Optional[Iterable[str]] = None


@dataclass
class RequestMeta:
    """Telemetry captured for each request, enriched across the pipeline."""

    url: str
    method: str
    start_time: float
    end_time: Optional[float] = None
    status: Optional[int] = None
    retries: int = 0
    total_backoff_seconds: float = 0.0
    rate_limit_wait_seconds: float = 0.0
    circuit_state: CircuitState = CircuitState.CLOSED
    robots_allowed: Optional[bool] = None
    response_size: Optional[int] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    extra: MutableMapping[str, Any] = field(default_factory=dict[str, Any])


T = TypeVar("T")


@dataclass
class Result(Generic[T]):
    """Result wrapper carrying either a value or an error plus request metadata."""

    value: Optional[T] = None
    error: Optional[Exception] = None
    meta: Optional[RequestMeta] = None

    @property
    def is_ok(self) -> bool:
        return self.error is None

    @property
    def is_err(self) -> bool:
        return self.error is not None
