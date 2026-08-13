# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""Tests for ladon.plugins.models — frozen dataclasses."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import MISSING, FrozenInstanceError, fields
from pathlib import Path
from typing import assert_type

import pytest

from ladon.plugins.models import Expansion, Ref


class TestRef:
    def test_fields_stored(self) -> None:
        ref = Ref(url="https://example.com/resource/1")
        assert ref.url == "https://example.com/resource/1"
        assert ref.raw == {}

    def test_raw_preserved(self) -> None:
        ref = Ref(url="https://example.com/resource/1", raw={"code": "X001"})
        assert ref.raw["code"] == "X001"

    def test_omitted_raw_uses_default_specialization(self) -> None:
        ref = assert_type(
            Ref("https://example.com/resource/1"),
            Ref[Mapping[str, object]],
        )
        assert ref.raw == {}

    def test_raw_retains_dataclass_default_factory(self) -> None:
        raw_field = next(field for field in fields(Ref) if field.name == "raw")
        default_factory = raw_field.default_factory
        assert default_factory is not MISSING
        first = default_factory()
        second = default_factory()
        assert first == second == {}
        assert first is not second

    def test_explicit_specialization_requires_raw(self, tmp_path: Path) -> None:
        source = tmp_path / "invalid_ref.py"
        source.write_text(
            "from dataclasses import dataclass\n"
            "from ladon import Ref\n\n"
            "@dataclass(frozen=True)\n"
            "class RawContext:\n"
            "    value: int\n\n"
            'invalid = Ref[RawContext]("https://example.com")\n'
        )
        config = tmp_path / "pyrightconfig.json"
        config.write_text(
            json.dumps(
                {
                    "typeCheckingMode": "strict",
                    "include": [str(source)],
                    "extraPaths": [str(Path(__file__).parents[2] / "src")],
                }
            )
        )

        result = subprocess.run(
            [sys.executable, "-m", "pyright", "--project", str(config)],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert 'Argument missing for parameter "raw"' in result.stdout

    def test_immutable(self) -> None:
        ref = Ref(url="https://example.com/resource/1")
        with pytest.raises(FrozenInstanceError):
            ref.url = "other"  # type: ignore[misc]


class TestExpansion:
    def test_fields_stored(self) -> None:
        child_refs = [
            Ref(url="https://example.com/leaf/1"),
            Ref(url="https://example.com/leaf/2"),
        ]
        record = object()
        exp = Expansion(record=record, child_refs=child_refs)
        assert exp.record is record
        assert len(exp.child_refs) == 2

    def test_immutable(self) -> None:
        exp = Expansion(record=object(), child_refs=[])
        with pytest.raises(FrozenInstanceError):
            exp.record = object()  # type: ignore[misc]

    def test_empty_child_refs(self) -> None:
        exp = Expansion(record="some-record", child_refs=[])
        assert exp.child_refs == []
