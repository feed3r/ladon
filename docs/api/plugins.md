# Plugin API

Plugins are the site-specific half of Ladon.  A plugin bundles a `Source`
(discovers top-level refs), one or more `Expanders` (fan out through the
URL tree), and a `Sink` (fetches each leaf and returns a record).  All
protocols are structural (PEP 544) — no inheritance from Ladon is required.

Ladon ships two parallel protocol hierarchies: sync and async.

## Running a plugin

Use `run_plugin()` for the normal whole-plugin path: it calls
`plugin.source.discover(client)` once, runs every discovered root in source
order, and returns a `PluginRunResult`. The aggregate exposes total leaf
counts and errors while retaining a `RunResult` per root in `results`.
`RunConfig.leaf_limit` applies separately to every discovered root. If a
later root raises a globally fatal error, earlier roots may already have run
their `on_leaf` callback; make that callback idempotent when retrying a whole
plugin run.

`run_crawl(top_ref, ...)` remains available when the caller intentionally
owns root discovery or needs to process one known root. The async equivalents
are `async_run_plugin()` and `async_run_crawl()`; async whole-plugin runs keep
root processing ordered, while each root's leaf work uses `async_concurrency`.

## Sync protocols

::: ladon.plugins.protocol

## Async protocols

The async protocols mirror the sync ones exactly but use `async def`
methods and accept `AsyncHttpClientProtocol` instead of
`SyncHttpClientProtocol`.

::: ladon.plugins.async_protocol

## Data models

::: ladon.plugins.models

## Errors

::: ladon.plugins.errors
