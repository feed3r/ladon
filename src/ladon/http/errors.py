from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class HttpClientError(Exception):
    """Base class for HTTP client errors."""


@dataclass
class HttpError(HttpClientError):
    """Represents an HTTP response classified as an application error."""

    status: Optional[int] = None
    message: str = ""
    body_excerpt: Optional[str] = None
    url: Optional[str] = None

    def __str__(self) -> str:
        status_part = (
            f" status={self.status}" if self.status is not None else ""
        )
        return f"HttpError{status_part}: {self.message}"


class RetryExhaustedError(HttpClientError):
    """Raised when retry budget is exceeded."""


class CircuitOpenError(HttpClientError):
    """Raised when the circuit breaker is OPEN and short-circuits the request."""


class RobotsBlockedError(HttpClientError):
    """Raised when robots.txt disallows the request."""


class TimeoutError(HttpClientError):
    """Raised when request timeouts are exceeded."""
