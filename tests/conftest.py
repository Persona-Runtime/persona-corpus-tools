from __future__ import annotations

from pathlib import Path

import pytest

from persona_ingestion.canonical.models import SourceMeta
from persona_ingestion.canonical.personas import PersonaConfig, load_persona_config

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "synthetic"
TRANSCRIPT_FIXTURE = FIXTURES / "transcript_txt" / "sample_series_ep001.txt"
EXPECTED_DIR = FIXTURES / "expected"


@pytest.fixture(scope="session")
def personas() -> PersonaConfig:
    return load_persona_config(REPO_ROOT / "configs" / "personas.yaml")


@pytest.fixture()
def meta() -> SourceMeta:
    return SourceMeta(
        source_id="sample-series-ep001",
        series="sample-series",
        episode="001",
        language="en",
        sha256="0" * 64,
        input_type="transcript_txt",
    )
