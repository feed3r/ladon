---
status: accepted
date: 2026-03-18
decision-makers: [Maintainers]
informed: [Contributors]
refs: [ADR-001, "Issue #38", "Issue #160"]
---

# ADR-007 — Per-Host Circuit Breaker

## Context and Problem Statement

ADR-001 mandated a circuit breaker at the `HttpClient` layer to prevent
cascading failures when a target host becomes unavailable or starts
rejecting connections.  `CircuitOpenError` was reserved as a placeholder
but never raised.  Without a circuit breaker `HttpClient` hammers a
failing host indefinitely, consuming retry budget and delaying detection
of systematic outages.

## Decision Drivers

* Prevent thundering-herd retries against a host that is already down.
* Keep per-host state so one failing domain does not affect others.
* Remain disabled by default to avoid breaking existing callers.
* Fit cleanly into the existing sync `_request()` pipeline.

## Considered Options

* **A — Count-based per-host state machine (chosen)**
* **B — Rate-based (failure ratio over a sliding window)**
* **C — External circuit breaker (e.g. `pybreaker` library)**

## Decision Outcome

**Chosen: Option A.**

A count-based, per-host state machine is simple, deterministic, and
requires no additional dependencies.

### State machine

```
CLOSED ─(failures >= threshold)─► OPEN ─(recovery elapsed)─► HALF_OPEN
  ▲                                  ▲                              │
  └───────── success ────────────────┼──────────────────────────────┘
                                     └──────── failure ─────────────┘
```

* **CLOSED** — normal operation; consecutive failure counter tracked.
* **OPEN** — all requests to this host blocked with `CircuitOpenError`;
  timer running toward `recovery_seconds`.
* **HALF_OPEN** — one probe allowed; success → CLOSED, failure → OPEN.

### Implementation

* New class `CircuitBreaker` in `ladon.networking.circuit_breaker`.
* `HttpClientConfig` fields (both off by default):
  * `circuit_breaker_failure_threshold: int | None = None`
  * `circuit_breaker_recovery_seconds: float = 60.0`
* `HttpClient._circuit_breakers: dict[str, CircuitBreaker]` keyed by
  `netloc`; created lazily on first request to a host.
* Check at start of `_request()` before `_enforce_rate_limit()`; raise
  `CircuitOpenError` if blocked.
* Record success/failure on every `_request()` completion.

### Async concurrency amendment (2026-08-02, Issue #160)

The original caller-enforced single-probe assumption is insufficient for
`AsyncHttpClient` and `AsyncCurlHttpClient`: several tasks can be admitted
before any one of them records an outcome. The shared async policy therefore
uses an event-loop-local admission guard with these semantics:

* CLOSED requests remain concurrent. Outcome updates are synchronous between
  await points, so failure counts cannot be partially mutated.
* The first request after OPEN recovery reserves the HALF_OPEN probe. Other
  requests to that host receive `CircuitOpenError` until the probe records an
  outcome or is cancelled.
* Every admission carries a circuit generation. A slow CLOSED request from an
  earlier generation cannot close or re-open a newer HALF_OPEN probe.
* Cancellation releases a HALF_OPEN reservation without counting as host
  success or failure.

Async rate limiting uses a separate per-host `asyncio.Lock`. A caller waits
and commits its next eligible start time while holding that host's lock, so
concurrent callers cannot wake as a batch. Locks are not shared across hosts,
and all event-loop-bound guard state is discarded when the client closes.
Async client instances remain single-event-loop objects and must not be shared
across threads or loops.

## Consequences

* **Good**: cascading failures to a dead host are cut off quickly.
* **Good**: per-host tracking means unrelated domains are unaffected.
* **Good**: disabled by default — zero behaviour change for existing callers.
* **Neutral**: `threshold` counts *call sequences* (one `HttpClient.get()`
  invocation), not individual HTTP attempts.  With `retries=2` and
  `threshold=3`, the circuit opens after 3 exhausted call sequences (up to
  9 underlying HTTP attempts).  Operators should size `threshold` with this
  in mind — a value of 3 is more tolerant than it first appears.  During
  HALF_OPEN, the single probe may itself involve up to `retries + 1` raw
  HTTP attempts; observers watching traffic may see more than one request.
* **Bad**: no persistence across client instances — circuit state resets on
  every new client construction.

## Rejected options

**B (rate-based):** Requires a sliding window, timestamps per request,
and is harder to reason about in tests. Count-based state plus explicit async
admission generations provides deterministic semantics without that extra
model.

**C (pybreaker):** Adds a runtime dependency for functionality that is
straightforward to implement cleanly.  Keeping it in-house means the
behaviour is explicit, testable, and auditable.
