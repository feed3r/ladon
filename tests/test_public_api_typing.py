"""Runtime and strict-type-checking smoke tests for public generic defaults."""

from ladon import (
    AsyncOnLeafCallback,
    AsyncOnPlannedLeafCallback,
    CrawlPlan,
    Expansion,
    OnLeafCallback,
    OnPlannedLeafCallback,
    PluginRunResult,
    Ref,
    RunResult,
)


def test_bare_public_generic_symbols_remain_importable() -> None:
    symbols: tuple[object, ...] = (
        Ref,
        Expansion,
        RunResult,
        CrawlPlan,
        PluginRunResult,
        OnLeafCallback,
        OnPlannedLeafCallback,
        AsyncOnLeafCallback,
        AsyncOnPlannedLeafCallback,
    )
    assert all(symbol is not None for symbol in symbols)
