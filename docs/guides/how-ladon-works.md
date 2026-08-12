# How Ladon Works

Ladon crawls **tree-structured web resources** — sites where content is
organized hierarchically: a forum has threads, a thread has posts; a shop has
categories, a category has products; a news site has topics, a topic has
articles. This guide explains the mental model behind Ladon's plugin system
and how the three pipeline roles map to a real crawl.

## The Source → Expander → Sink pipeline

Every Ladon plugin decomposes its crawl into three roles:

```
Source  →  [Expander, …]  →  Sink
```

| Role | Question it answers | Output |
|---|---|---|
| **Source** | Where do I start? | A list of top-level refs |
| **Expander** | What's inside this node? | A record + child refs |
| **Sink** | What's at this leaf? | A final record |

For the normal whole-plugin path, call `run_plugin()` (or
`async_run_plugin()`). Ladon calls `Source.discover()` once, dispatches the
discovered roots in source order, and then:

1. Calls the first `Expander.expand()` for each root to get its record and children.
2. Repeats with subsequent Expanders for deeper levels.
3. Calls `Sink.consume()` on every leaf ref.
4. Fires the `on_leaf` callback (your persistence hook) after each success.

Ladon owns discovery, traversal, error counting, rate limiting, and the leaf
limit. Your code owns the parsing. Use `run_crawl(top_ref, ...)` or
`async_run_crawl(top_ref, ...)` only when your application deliberately owns
root discovery or must process one known root. For that lower-level path, call
`Source.discover(client)` and loop over its refs, calling `run_crawl()` once
per ref. With the async API, discovery must be awaited: call
`await AsyncSource.discover(client)` and loop over its refs, calling
`async_run_crawl()` once per ref. See the [Hacker News tree-crawl cookbook
example](cookbook.md#multi-level-tree-crawl-hacker-news) for the complete
pattern.

## A concrete example: Hacker News

Hacker News has a two-level structure: a **front page** (list of stories) and
individual **story pages** (comments). Here's how that maps:

```
Source          →  [Expander]      →  Sink
─────────────────────────────────────────────
HN front page      Story page         Comment
(top story IDs)    (story + comment   (comment record)
                    IDs)
```

**Source** fetches `https://hacker-news.firebaseio.com/v0/topstories.json` and
returns a list of story IDs as refs.

**Expander** takes one story ID ref, fetches the story item, and returns an
`Expansion`:

```python
@dataclass(frozen=True)
class Expansion:
    record: object          # the story record (title, score, author…)
    child_refs: Sequence[object]   # the comment ID refs
```

**Sink** takes one comment ID ref and returns the fully parsed comment record.

The runner calls the Expander once per story, then the Sink once per comment.
You write three focused classes, each doing one thing.

## Flat crawls (no tree)

Not every site has multiple levels. A flat crawl — a paginated list of items
where each item is the leaf — uses a **single Expander** that returns the item
URLs as `child_refs`, followed by a Sink that processes each one:

```
Source  →  [Expander (page → item refs)]  →  Sink (item record)
```

The number of Expanders is yours to choose; the framework handles any depth.
A Sink is always required — the runner always calls `Sink.consume()` on each
leaf ref.

## Refs carry context

A **ref** is a lightweight, immutable pointer to a resource. It always has a
URL, and it can carry a `raw` dict for pre-fetched data:

```python
@dataclass(frozen=True)
class Ref:
    url: str
    raw: Mapping[str, object]   # arbitrary pre-fetched context
```

The Expander populates `ref.raw` with whatever the Sink will need — parent
metadata, pre-loaded JSON, currency information — so the Sink never has to
re-fetch it. No parent context is threaded through the runner; context travels
with the ref.

## Errors are explicit, not swallowed

Ladon's error taxonomy is part of the protocol. Each exception carries a
specific meaning that tells the runner exactly what to do:

| Exception | Meaning | Runner behaviour |
|---|---|---|
| `ExpansionNotReadyError` | Resource not ready yet | Abort the run; retry next schedule |
| `PartialExpansionError` | Child list incomplete | **First expander:** abort the run (re-raised to caller). **Non-first expander:** log and skip the branch. |
| `ChildListUnavailableError` | Child list unreachable | **First expander:** abort the run (re-raised to caller). **Non-first expander:** log and skip the branch. |
| `LeafUnavailableError` | Leaf fetch failed | Log, record the failure, and continue, as for other non-fatal Sink exceptions |

Your plugin raises the right typed exception when it can. During leaf
processing, the runner also records unexpected `Exception` text in
`RunResult.errors` and continues with independent leaves, so a single bad leaf
does not discard the rest of the crawl. Explicitly fatal
`AssetDownloadError`, cancellation, and other `BaseException` subclasses still
propagate to the caller.

## Persistence is injected, not built in

The runner has no database dependency. You inject persistence as a callback:

```python
def my_persist(leaf_record: object, parent_record: object) -> None:
    db.insert(leaf_record)

result = run_crawl(
    top_ref=my_ref,
    plugin=my_plugin,
    client=client,
    config=RunConfig(),
    on_leaf=my_persist,
)
```

`on_leaf` fires after every successful Sink call. If the Sink raises, `on_leaf`
is never called for that leaf. What you do in `on_leaf` — write to a database,
append to a file, stream to S3, print to stdout — is entirely up to you.

## What Ladon provides for free

Because all plugins go through the same runner and `HttpClient`, every crawl
gets these automatically:

- **Rate limiting** — per-domain request pacing
- **Retries with backoff** — configurable, per-domain
- **Circuit breaking** — stops hammering a failing host (ADR-007)
- **robots.txt enforcement** — opt-in, RFC 9309 compliant (ADR-008)
- **Structured logging** — runner and leaf events logged with plugin name, ref, counts
- **Leaf limit** — `RunConfig.leaf_limit` caps any crawl for safe testing

## Next steps

- [Concepts](concepts.md) — why refs, results, and typed errors have these contracts
- [Cookbook](cookbook.md) — runnable patterns for flat, tree, async, and public-web crawls
- [Authoring Plugins](authoring-plugins.md) — implement your first plugin
- [ADR-004: SES Protocol Design](../decisions/adr-004-ses-protocol-design.md) — the full rationale behind this architecture
- [API Reference: Runner](../api/runner.md) — `run_crawl`, `RunConfig`, `RunResult`
- [API Reference: Plugins](../api/plugins.md) — `CrawlPlugin`, `Source`, `Expander`, `Sink`, `Expansion`
