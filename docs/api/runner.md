# Runner API

The runner drives the crawl loop: it discovers a plugin's roots, expands refs
through the expander chain, and passes each leaf to the sink.

Use `run_plugin()` or `async_run_plugin()` for a complete plugin run. They
call `Source.discover()` once and return a `PluginRunResult` containing the
individual `RunResult` values and their aggregate counts. Use `run_crawl()` or
`async_run_crawl()` only when the caller deliberately owns root discovery or
needs to process one known root.

All runners use the same `RunConfig`. Its `leaf_limit` is a per-root cap for
whole-plugin runs. If a later root raises a globally fatal error, earlier
roots may already have invoked `on_leaf`; persistence callbacks must therefore
be idempotent when retrying `run_plugin()` or `async_run_plugin()`.

## See also

[Concepts](../guides/concepts.md) explains the `RunResult` counters and the
typed plugin errors that determine runner recovery behaviour.

## run_plugin

::: ladon.runner.run_plugin

## async_run_plugin

::: ladon.async_runner.async_run_plugin

## run_crawl

::: ladon.runner.run_crawl

## async_run_crawl

::: ladon.async_runner.async_run_crawl

## RunConfig

::: ladon.runner.RunConfig

## RunResult

::: ladon.runner.RunResult

## PluginRunResult

::: ladon.runner.PluginRunResult
