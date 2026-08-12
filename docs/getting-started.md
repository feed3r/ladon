# Getting Started

## Installation

```bash
pip install ladon-crawl
```

Ladon requires Python 3.11+.  Runtime dependencies: `requests` (sync
client) and `httpx` (async client).

## First request

```python
from ladon.networking.client import HttpClient
from ladon.networking.config import HttpClientConfig

config = HttpClientConfig(
    timeout_seconds=10,
    retries=2,
    backoff_base_seconds=1.0,
)

with HttpClient(config) as client:
    result = client.get("https://httpbin.org/get")
    if result.ok:
        print(result.value)       # response bytes
        print(result.meta["status_code"])  # HTTP status code
    else:
        print("error:", result.error)
```

## Using the CLI

!!! note "CLI entry point"
    The `ladon` command is installed via the `[project.scripts]` entry point.
    If `ladon --version` reports "command not found", re-run the editable
    install from the repo root: `pip install -e .`

After installation the `ladon` command is available:

```bash
ladon --version
ladon info
ladon run --plugin mypackage.adapters:MyPlugin --ref https://example.com
```

`ladon run` dynamically imports the plugin class and runs a crawl against the
given reference URL.  Exit codes: 0 = success, 1 = error, 2 = partial
failures, 3 = data not ready (retry later).  Uses default `HttpClientConfig`
settings (30 s timeout, no retries, no rate limiting).  For fine-grained
control, call `run_plugin()` directly from Python; use `run_crawl()` only when
your application already owns one top-level ref.

## Running a complete plugin from Python

`run_plugin()` is the normal library entry point. It calls the plugin's
`Source.discover()` once and returns aggregate counts plus one `RunResult` per
discovered root. `RunConfig(leaf_limit=...)` applies to each root. If a later
root raises a globally fatal error, earlier roots may already have persisted
records through `on_leaf`, so make that callback idempotent before retrying.

See [Authoring Plugins](guides/authoring-plugins.md) for a complete sync and
async example.

## Configuration reference

`HttpClientConfig` controls all client behaviour.  All fields have defaults and
the config is immutable after construction.

| Field | Default | Description |
|---|---|---|
| `user_agent` | `None` | Custom `User-Agent` header |
| `retries` | `0` | Number of retry attempts after the first failure |
| `backoff_base_seconds` | `0.5` | Exponential backoff base (set to 0 for an explicit unpaced-retry opt-out) |
| `timeout_seconds` | `30.0` | Request timeout in seconds |
| `connect_timeout_seconds` | `None` | Separate connect timeout (set both or neither) |
| `read_timeout_seconds` | `None` | Separate read timeout (set both or neither) |
| `min_request_interval_seconds` | `0.0` | Per-host rate limit (disabled if 0) |
| `verify_tls` | `True` | Verify TLS certificates |
| `circuit_breaker_failure_threshold` | `None` | Enable circuit breaker (None = disabled) |
| `circuit_breaker_recovery_seconds` | `60.0` | Seconds before HALF_OPEN probe |
| `respect_robots_txt` | `False` | Enforce robots.txt rules |

## Ethical note: robots.txt

!!! tip "Enable robots.txt enforcement for public-web crawls"
    `respect_robots_txt` is **disabled by default** to avoid breaking callers
    that crawl their own infrastructure or operate under explicit data-access
    agreements.  If you are crawling third-party public websites, you are
    **strongly encouraged** to enable it:

    ```python
    HttpClientConfig(respect_robots_txt=True)
    ```

    **Why it matters:**

    - **Community norm** — Respecting `robots.txt` is the long-established
      contract between crawlers and web servers, codified as an IETF Proposed
      Standard in [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html)
      (2022), which uses **MUST** language for crawlers that claim conformance
      to its directives.
    - **Ethical baseline** — Respecting robots.txt is treated as a baseline
      ethical expectation for responsible crawling projects in academic and
      legal literature on web data collection.
    - **Legal exposure** — Commercial operators have faced legal challenges
      over scraping practices; demonstrating good-faith compliance with
      robots.txt is widely cited as a relevant factor by legal practitioners
      in data-collection disputes.
    - **Industry standard** — All major search engines (Google, Bing,
      DuckDuckGo, Yandex) and the dominant Python scraping framework (Scrapy)
      respect robots.txt by default.

    The only known case for disabling this setting is crawling your own
    infrastructure, sites you have a contractual right to access, or archival
    work under an explicit institutional policy.

**What `respect_robots_txt=True` does automatically:**

- Requests to disallowed URLs are blocked immediately and returned as
  `RobotsBlockedError` (no network attempt is made).
- `Crawl-delay` directives are honoured: if a site advertises
  `Crawl-delay: 5`, Ladon applies at least a 5-second gap between
  requests to that host, overriding `min_request_interval_seconds` if
  the advertised delay is larger.
- Matching uses the **full URL including query string**.  A rule like
  `Disallow: /search?q=` correctly blocks `/search?q=foo` but not
  `/search?lang=en`.
- The robots.txt fetch is cached for the duration of the
  `HttpClient` session — at most one fetch per origin (one per
  `scheme + hostname` pair).

## Async crawling

For high-throughput whole-plugin crawls use `async_run_plugin()` with
`AsyncHttpClient`:

```python
import asyncio
from ladon import AsyncHttpClient, async_run_plugin
from ladon.networking.config import HttpClientConfig
from ladon.runner import RunConfig

config = HttpClientConfig(retries=2, timeout_seconds=10)

async def main() -> None:
    async with AsyncHttpClient(config) as client:
        result = await async_run_plugin(
            plugin=my_async_plugin,
            client=client,
            config=RunConfig(async_concurrency=20),
            on_leaf=my_async_persist,
        )
        print(f"consumed {result.leaves_consumed}, failed {result.leaves_failed}")

asyncio.run(main())
```

Phase 1 (expander traversal) runs sequentially; Phase 3 (sink) issues leaf
fetches concurrently behind `asyncio.Semaphore(async_concurrency)`.  Each
slot covers the full `sink.consume()` + `on_leaf` pair so slow callbacks
do not cause unbounded parallelism.

`on_leaf` must be an `async def` function when used with `async_run_plugin`.

## Next steps

- [Concepts](guides/concepts.md) — understand refs, results, and typed crawl errors
- [Cookbook](guides/cookbook.md) — runnable patterns for common real-world crawls
- [Authoring Plugins](guides/authoring-plugins.md) — build a site adapter
- [API Reference → Networking](api/networking.md) — `HttpClient`, `AsyncHttpClient`, and config
- [API Reference → Runner](api/runner.md) — whole-plugin and single-root runners, plus result types
