from __future__ import annotations

import dataclasses

import pytest

from persona_ingestion.adapters.registry import (
    ADAPTERS,
    UnsupportedSourceError,
    resolve_adapter,
)
from persona_ingestion.intake.manifest import load_manifest

from ..conftest import MANIFEST_FIXTURE


def test_parser_profile_selects_the_adapter() -> None:
    adapter = resolve_adapter(load_manifest(MANIFEST_FIXTURE))
    assert adapter.parser_profile == "transcript-txt"
    assert adapter.parser_version == "transcript-txt-v2"


def test_shooting_script_profile_selects_its_own_adapter() -> None:
    manifest = dataclasses.replace(
        load_manifest(MANIFEST_FIXTURE),
        parser_profile="shooting-script-pdf",
        input_type="script_pdf",
    )
    adapter = resolve_adapter(manifest)
    assert adapter.parser_profile == "shooting-script-pdf"
    assert adapter.parser_version == "shooting-script-pdf-v1"


def test_unimplemented_profile_is_reported_not_guessed() -> None:
    manifest = dataclasses.replace(
        load_manifest(MANIFEST_FIXTURE),
        parser_profile="script-pdf",
        input_type="script_pdf",
    )
    with pytest.raises(UnsupportedSourceError, match="no adapter"):
        resolve_adapter(manifest)


def test_profile_and_input_type_must_agree() -> None:
    """A manifest whose two format fields disagree is a mistake in the
    manifest, not a missing feature — and it is reported differently."""
    manifest = dataclasses.replace(load_manifest(MANIFEST_FIXTURE), input_type="script_pdf")
    with pytest.raises(UnsupportedSourceError, match="but the manifest declares"):
        resolve_adapter(manifest)


def test_each_adapter_carries_its_own_version() -> None:
    """A fix in one adapter must not move another adapter's dataset versions."""
    versions = [a.parser_version for a in ADAPTERS.values()]
    assert len(versions) == len(set(versions))
