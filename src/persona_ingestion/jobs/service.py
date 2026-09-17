"""Minimum, deterministic processing for a gateway-owned ingestion attempt."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

Outcome = Literal["success", "failure"]


@dataclass(frozen=True)
class MaterialSource:
    """One immutable web-upload source supplied by the gateway."""

    id: uuid.UUID
    content: str


@dataclass(frozen=True)
class AttemptInput:
    """The sources belonging to one verified job attempt."""

    version_id: uuid.UUID
    sources: list[MaterialSource]


@dataclass(frozen=True)
class ProcessedDocument:
    """A minimal derived document. The original source remains untouched."""

    id: uuid.UUID
    source_id: uuid.UUID
    ordinal: int
    cleaned_content: str
    sha256: str


@dataclass(frozen=True)
class AttemptResult:
    outcome: Outcome
    error_kind: str | None = None
    error_code: str | None = None


class JobStore(Protocol):
    """The narrow DB contract used by the worker service."""

    def existing_result(self, attempt_id: uuid.UUID) -> AttemptResult | None: ...

    def load_attempt(self, job_id: uuid.UUID, attempt_id: uuid.UUID) -> AttemptInput: ...

    def save_success(
        self,
        attempt_id: uuid.UUID,
        attempt: AttemptInput,
        documents: list[ProcessedDocument],
        character_count: int,
    ) -> AttemptResult: ...

    def save_failure(
        self, attempt_id: uuid.UUID, error_kind: str, error_code: str
    ) -> AttemptResult: ...


class JobInputError(Exception):
    """The supplied job/attempt pair is not runnable by this worker."""


def normalize_newlines(text: str) -> str:
    """Canonicalize every supported line ending without otherwise rewriting text."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def process_attempt(store: JobStore, job_id: uuid.UUID, attempt_id: uuid.UUID) -> AttemptResult:
    """Process one attempt, reusing an already-recorded terminal result.

    Empty input is a permanent input failure. All other classification belongs
    to the caller because only the store knows whether a database error is
    transient.
    """
    existing = store.existing_result(attempt_id)
    if existing is not None:
        return existing

    attempt = store.load_attempt(job_id, attempt_id)
    documents: list[ProcessedDocument] = []
    character_count = 0
    for ordinal, source in enumerate(attempt.sources):
        cleaned = normalize_newlines(source.content)
        if not cleaned.strip():
            return store.save_failure(attempt_id, "permanent", "empty_input")
        documents.append(
            ProcessedDocument(
                id=uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"persona-ingestion/processed-document/{attempt.version_id}/{source.id}/{ordinal}",
                ),
                source_id=source.id,
                ordinal=ordinal,
                cleaned_content=cleaned,
                sha256=hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
            )
        )
        character_count += len(cleaned)

    if not documents:
        return store.save_failure(attempt_id, "permanent", "empty_input")
    return store.save_success(attempt_id, attempt, documents, character_count)
