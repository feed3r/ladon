"""Regression tests for behavior-defining project documentation."""

from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[1]


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def test_adr_004_contains_phase_3_exception_isolation_amendment() -> None:
    adr = (
        _PROJECT_ROOT / "docs/decisions/adr-004-ses-protocol-design.md"
    ).read_text(encoding="utf-8")

    assert _normalize_whitespace(
        "### Phase-3 leaf-exception isolation amendment "
        "(2026-08-11, Issue #164)"
    ) in _normalize_whitespace(adr)


def test_changelog_documents_cli_partial_failure_exit_code() -> None:
    changelog = (_PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    normalized_changelog = _normalize_whitespace(changelog)
    assert (
        _normalize_whitespace("CLI now exits with code 2")
        in normalized_changelog
    )
    assert (
        _normalize_whitespace("ordinary `Sink.consume()` exception")
        in normalized_changelog
    )
