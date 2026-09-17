from __future__ import annotations

from pathlib import Path

import pytest

from persona_ingestion.canonical.models import SourceMeta
from persona_ingestion.canonical.personas import PersonaConfig, load_persona_config

REPO_ROOT = Path(__file__).resolve().parents[1]
PERSONAS = REPO_ROOT / "configs" / "personas.yaml"

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "synthetic"
PRIVATE_ROOT = FIXTURES
TRANSCRIPT_FIXTURE = FIXTURES / "transcript_txt" / "sample_series_ep001.txt"
MANIFEST_FIXTURE = FIXTURES / "manifests" / "sample-series-ep001.yaml"
EXPECTED_DIR = FIXTURES / "expected"


@pytest.fixture(scope="session")
def personas() -> PersonaConfig:
    return load_persona_config(PERSONAS)


@pytest.fixture()
def meta() -> SourceMeta:
    return SourceMeta(
        source_id="sample-series-ep001",
        character_id="character_a",
        series="sample-series",
        episode="001",
        language="en",
        sha256="0" * 64,
        input_type="transcript_txt",
        dataset_version="ds1-testfixture0000",
    )
