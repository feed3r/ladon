from ladon.http import (
    CircuitBreakerConfig,
    HttpClient,
    HttpClientConfig,
    RateLimitPolicy,
    Result,
    RetryPolicy,
    RobotsConfig,
    Timeouts,
)


def test_http_client_config_defaults():
    # Ensure default configuration wires expected policy objects and sane values.
    config = HttpClientConfig()

    assert isinstance(config.retry, RetryPolicy)
    assert config.retry.max_retries == 3
    assert isinstance(config.rate_limit, RateLimitPolicy)
    assert config.rate_limit.requests_per_second > 0
    assert isinstance(config.circuit_breaker, CircuitBreakerConfig)
    assert config.circuit_breaker.cooldown_seconds > 0
    assert isinstance(config.robots, RobotsConfig)
    assert config.robots.enabled is True
    assert isinstance(config.timeouts, Timeouts)
    assert config.timeouts.total > 0
    assert "ladon" in config.user_agent


def test_result_helpers():
    # Result helpers should reflect presence or absence of errors.
    ok: Result[str] = Result(value="payload")
    err: Result[str] = Result(error=RuntimeError("boom"))

    assert ok.is_ok
    assert not ok.is_err
    assert not err.is_ok
    assert err.is_err


def test_http_client_stub_returns_result_with_meta():
    # Stubbed client should yield an error result with populated metadata.
    client = HttpClient()
    result = client.get("https://example.com")

    assert result.is_err
    assert isinstance(result.error, NotImplementedError)
    assert result.meta is not None
    assert result.meta.url == "https://example.com"
    assert result.meta.method == "GET"
    client.close()
