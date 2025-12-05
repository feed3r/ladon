from __future__ import annotations

from types import TracebackType
from typing import Any, Dict, Optional, Type

import requests

from .types import HttpClientConfig, RequestMeta, Result


class HttpClient:
    """Synchronous HTTP client entry point."""

    def __init__(self, config: Optional[HttpClientConfig] = None) -> None:
        """Create a client with optional configuration and a shared session."""
        self.config = config or HttpClientConfig()
        self._session = requests.Session()

    def get(self, url: str, **kwargs: Any) -> Result[bytes]:
        """Perform a GET request through the shared request pipeline."""
        return self._request("GET", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> Result[bytes]:
        """Perform a lightweight HEAD request via the same resilience path as GET."""
        return self._request("HEAD", url, **kwargs)

    def post(
        self,
        url: str,
        data: Optional[Any] = None,
        json: Optional[Any] = None,
        **kwargs: Any,
    ) -> Result[bytes]:
        payload: Dict[str, Any] = {}
        if data is not None:
            payload["data"] = data
        if json is not None:
            payload["json"] = json
        payload.update(kwargs)
        """Send a POST request with optional form or JSON payload."""
        return self._request("POST", url, **payload)

    def download(self, url: str, **kwargs: Any) -> Result[bytes]:
        """Streamed download path; size/content-type enforcement will be added."""
        return self._request("GET", url, stream=True, **kwargs)

    def _request(self, method: str, url: str, **kwargs: Any) -> Result[bytes]:
        """Central request dispatcher; retries/limits/circuit/robots to be added."""
        meta = RequestMeta(
            url=url, method=method, start_time=0.0
        )  # timestamps added in future steps
        return Result(
            error=NotImplementedError("HTTP pipeline not implemented yet"),
            meta=meta,
        )

    def close(self) -> None:
        """Release underlying session resources."""
        self._session.close()

    def __enter__(self) -> "HttpClient":
        """Support context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        """Support context manager exit by closing the session."""
        self.close()
