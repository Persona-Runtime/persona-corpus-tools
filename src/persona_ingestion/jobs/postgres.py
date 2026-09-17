"""Postgres implementation of the gateway's ingestion v1 contract."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import psycopg
from psycopg import Connection

from .service import (
    AttemptInput,
    AttemptResult,
    JobInputError,
    MaterialSource,
    Outcome,
    ProcessedDocument,
)


class PostgresJobStore:
    """Read sources and write one attempt's deterministic derived result."""

    def __init__(self, connection: Connection[tuple[object, ...]]) -> None:
        self.connection = connection

    @classmethod
    def connect(cls, database_url: str) -> PostgresJobStore:
        return cls(psycopg.connect(database_url))

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[Connection[tuple[object, ...]]]:
        with self.connection.transaction():
            yield self.connection

    def existing_result(self, attempt_id: uuid.UUID) -> AttemptResult | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "select outcome,error_kind,error_code "
                "from ingestion_attempt_results where attempt_id=%s",
                (attempt_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        outcome, kind, code = row
        return AttemptResult(
            outcome=cast(Outcome, outcome),
            error_kind=cast(str | None, kind),
            error_code=cast(str | None, code),
        )

    def load_attempt(self, job_id: uuid.UUID, attempt_id: uuid.UUID) -> AttemptInput:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                select j.version_id
                from ingestion_attempts a
                join ingestion_jobs j on j.id=a.job_id
                where a.id=%s and a.job_id=%s and a.status in ('dispatching', 'running')
                """,
                (attempt_id, job_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise JobInputError("job_attempt_not_runnable")
            version_id = cast(uuid.UUID, row[0])
            cursor.execute(
                """
                select id,content
                from material_sources
                where version_id=%s
                order by created_at,id
                """,
                (version_id,),
            )
            sources = [
                MaterialSource(id=cast(uuid.UUID, source_id), content=cast(str, content))
                for source_id, content in cursor
            ]
        return AttemptInput(version_id=version_id, sources=sources)

    def save_success(
        self,
        attempt_id: uuid.UUID,
        attempt: AttemptInput,
        documents: list[ProcessedDocument],
        character_count: int,
    ) -> AttemptResult:
        with self._transaction() as connection, connection.cursor() as cursor:
            existing = self._locked_result(cursor, attempt_id)
            if existing is not None:
                return existing
            self._assert_attempt_writable(cursor, attempt_id)
            cursor.executemany(
                """
                insert into processed_documents(
                    id,version_id,source_id,ordinal,cleaned_content,sha256
                )
                values (%s,%s,%s,%s,%s,%s)
                on conflict (version_id,source_id,ordinal) do nothing
                """,
                [
                    (
                        document.id,
                        attempt.version_id,
                        document.source_id,
                        document.ordinal,
                        document.cleaned_content,
                        document.sha256,
                    )
                    for document in documents
                ],
            )
            cursor.execute(
                """
                insert into ingestion_attempt_results(
                    attempt_id,outcome,processed_document_count,summary
                )
                values (%s,'success',%s,%s::jsonb)
                on conflict (attempt_id) do nothing
                """,
                (attempt_id, len(documents), json.dumps({"character_count": character_count})),
            )
            if cursor.rowcount == 0:
                concurrent_result = self._locked_result(cursor, attempt_id)
                if concurrent_result is None:  # pragma: no cover - database invariant guard
                    raise RuntimeError("attempt result disappeared after conflict")
                return concurrent_result
        return AttemptResult(outcome="success")

    def save_failure(
        self, attempt_id: uuid.UUID, error_kind: str, error_code: str
    ) -> AttemptResult:
        with self._transaction() as connection, connection.cursor() as cursor:
            existing = self._locked_result(cursor, attempt_id)
            if existing is not None:
                return existing
            self._assert_attempt_writable(cursor, attempt_id)
            cursor.execute(
                """
                insert into ingestion_attempt_results(attempt_id,outcome,error_kind,error_code)
                values (%s,'failure',%s,%s)
                on conflict (attempt_id) do nothing
                """,
                (attempt_id, error_kind, error_code),
            )
            if cursor.rowcount == 0:
                concurrent_result = self._locked_result(cursor, attempt_id)
                if concurrent_result is None:  # pragma: no cover - database invariant guard
                    raise RuntimeError("attempt result disappeared after conflict")
                return concurrent_result
        return AttemptResult(outcome="failure", error_kind=error_kind, error_code=error_code)

    @staticmethod
    def _assert_attempt_writable(
        cursor: psycopg.Cursor[tuple[object, ...]], attempt_id: uuid.UUID
    ) -> None:
        cursor.execute(
            """
            select 1
            from ingestion_attempts a
            join ingestion_jobs j on j.id=a.job_id
            where a.id=%s
              and a.status in ('dispatching', 'running')
              and j.status in ('dispatching', 'running')
            for update of a
            """,
            (attempt_id,),
        )
        if cursor.fetchone() is None:
            raise JobInputError("job_attempt_not_writable")

    @staticmethod
    def _locked_result(
        cursor: psycopg.Cursor[tuple[object, ...]], attempt_id: uuid.UUID
    ) -> AttemptResult | None:
        cursor.execute(
            """
            select outcome,error_kind,error_code
            from ingestion_attempt_results
            where attempt_id=%s
            for update
            """,
            (attempt_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        outcome, kind, code = row
        return AttemptResult(
            outcome=cast(Outcome, outcome),
            error_kind=cast(str | None, kind),
            error_code=cast(str | None, code),
        )
