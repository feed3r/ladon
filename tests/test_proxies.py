# pyright: reportUnknownMemberType=false, reportPrivateUsage=false
# pyright: reportOptionalSubscript=false
"""Tests for static proxy support in HttpClientConfig / HttpClient."""

from unittest.mock import patch

import pytest

from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig

# ============================================================
# Config
# ============================================================


def test_proxies_default_is_none():
    assert HttpClientConfig().proxies is None


def test_proxies_can_be_set():
    config = HttpClientConfig(
        proxies={"https": "http://proxy.example.com:8080"}
    )
    assert config.proxies == {"https": "http://proxy.example.com:8080"}


def test_proxies_are_immutable():
    config = HttpClientConfig(
        proxies={"https": "http://proxy.example.com:8080"}
    )

    with pytest.raises(TypeError):
        config.proxies["https"] = "http://other.example.com:8080"  # type: ignore[index]


def test_proxies_copies_external_input():
    raw = {"https": "http://proxy.example.com:8080"}
    config = HttpClientConfig(proxies=raw)
    raw["https"] = "http://other.example.com:9090"

    assert config.proxies["https"] == "http://proxy.example.com:8080"


def test_proxies_http_and_https():
    proxies = {
        "http": "http://proxy.example.com:8080",
        "https": "http://proxy.example.com:8080",
    }
    config = HttpClientConfig(proxies=proxies)
    assert config.proxies == proxies


def test_proxies_socks5_accepted():
    config = HttpClientConfig(
        proxies={"https": "socks5://proxy.example.com:1080"}
    )
    assert config.proxies["https"] == "socks5://proxy.example.com:1080"


def test_proxies_socks4h_accepted():
    config = HttpClientConfig(
        proxies={"https": "socks4h://proxy.example.com:1080"}
    )
    assert config.proxies["https"] == "socks4h://proxy.example.com:1080"


def test_proxies_invalid_scheme_raises():
    with pytest.raises(ValueError, match="proxies"):
        HttpClientConfig(proxies={"https": "proxy.example.com:8080"})


def test_proxies_ftp_scheme_raises():
    with pytest.raises(ValueError, match="proxies"):
        HttpClientConfig(proxies={"https": "ftp://proxy.example.com:21"})


# ============================================================
# HttpClient — session wiring
# ============================================================


def test_proxy_applied_to_session():
    proxies = {"https": "http://proxy.example.com:8080"}
    config = HttpClientConfig(proxies=proxies)

    client = HttpClient(config)
    assert (
        client._session.proxies.get("https") == "http://proxy.example.com:8080"
    )


def test_no_proxy_session_proxies_empty():
    config = HttpClientConfig()

    client = HttpClient(config)
    # requests.Session.__init__ sets self.proxies = {} unconditionally; env vars
    # are merged later in Session.send(), not at construction time.  Assert that
    # our code injected no proxy entry.
    assert "https" not in client._session.proxies
    assert "http" not in client._session.proxies


def test_proxy_does_not_break_request_flow():
    # Smoke test: proxy wiring must not interfere with the normal request path.
    # Proxy correctness is verified by test_proxy_applied_to_session above.
    proxies = {"https": "http://proxy.example.com:8080"}
    config = HttpClientConfig(proxies=proxies)

    with patch("requests.Session.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = b"ok"
        mock_get.return_value.url = "https://example.com"
        mock_get.return_value.reason = "OK"
        mock_get.return_value.elapsed.total_seconds.return_value = 0.05
        mock_get.return_value.headers = {}
        client = HttpClient(config)
        client.get("https://example.com")

    mock_get.assert_called_once()
