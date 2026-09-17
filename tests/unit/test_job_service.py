from __future__ import annotations

import uuid

from persona_ingestion.jobs.service import (
    AttemptInput,
    AttemptResult,
    MaterialSource,
    ProcessedDocument,
    normalize_newlines,
    process_attempt,
)


class FakeStore:
    def __init__(self, attempt: AttemptInput) -> None:
        self.attempt = attempt
        self.result: AttemptResult | None = None
        self.documents: list[ProcessedDocument] = []
        self.character_count: int | None = None
        self.load_count = 0

    def existing_result(self, _attempt_id: uuid.UUID) -> AttemptResult | None:
        return self.result

    def load_attempt(self, _job_id: uuid.UUID, _attempt_id: uuid.UUID) -> AttemptInput:
        self.load_count += 1
        return self.attempt

    def save_success(
        self,
        _attempt_id: uuid.UUID,
        _attempt: AttemptInput,
        documents: list[ProcessedDocument],
        character_count: int,
    ) -> AttemptResult:
        self.documents = documents
        self.character_count = character_count
        self.result = AttemptResult("success")
        return self.result

    def save_failure(
        self, _attempt_id: uuid.UUID, error_kind: str, error_code: str
    ) -> AttemptResult:
        self.result = AttemptResult("failure", error_kind, error_code)
        return self.result


def attempt(*contents: str) -> AttemptInput:
    return AttemptInput(
        version_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        sources=[
            MaterialSource(uuid.UUID(f"00000000-0000-4000-8000-{index:012d}"), content)
            for index, content in enumerate(contents, start=1)
        ],
    )


def test_normalize_newlines_preserves_other_content() -> None:
    assert normalize_newlines("one\r\ntwo\rthree\nfour") == "one\ntwo\nthree\nfour"


def test_processes_every_nonempty_document_and_records_metrics() -> None:
    store = FakeStore(attempt("one\r\ntwo", "세\r네"))
    result = process_attempt(
        store,
        uuid.UUID("00000000-0000-4000-8000-000000000010"),
        uuid.UUID("00000000-0000-4000-8000-000000000011"),
    )

    assert result.outcome == "success"
    assert [document.cleaned_content for document in store.documents] == ["one\ntwo", "세\n네"]
    assert store.character_count == len("one\ntwo") + len("세\n네")
    assert all(len(document.sha256) == 64 for document in store.documents)


def test_empty_document_fails_without_saving_any_processed_document() -> None:
    store = FakeStore(attempt("valid", " \r\n\t "))
    result = process_attempt(store, uuid.uuid4(), uuid.uuid4())

    assert result == AttemptResult("failure", "permanent", "empty_input")
    assert store.documents == []


def test_empty_submission_fails() -> None:
    store = FakeStore(attempt())
    assert process_attempt(store, uuid.uuid4(), uuid.uuid4()) == AttemptResult(
        "failure", "permanent", "empty_input"
    )


def test_rerun_reuses_existing_result_without_reading_sources() -> None:
    store = FakeStore(attempt("not read"))
    store.result = AttemptResult("success")

    assert process_attempt(store, uuid.uuid4(), uuid.uuid4()) == AttemptResult("success")
    assert store.load_count == 0
