"""Entrypoint used by the Kubernetes ingestion Job."""

from __future__ import annotations

import os
import sys
import uuid

import psycopg

from .postgres import PostgresJobStore
from .service import JobInputError, process_attempt

EXIT_SUCCESS = 0
EXIT_FAILURE_RECORDED = 1
EXIT_CONFIGURATION = 2
EXIT_DATABASE = 3
EXIT_CODE = 4


def _required_uuid(name: str) -> uuid.UUID:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return uuid.UUID(value)


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("error: DATABASE_URL is required", file=sys.stderr)
        return EXIT_CONFIGURATION
    try:
        job_id = _required_uuid("PERSONA_JOB_ID")
        attempt_id = _required_uuid("PERSONA_ATTEMPT_ID")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION

    try:
        store = PostgresJobStore.connect(database_url)
    except psycopg.Error:
        print("error: database connection failed", file=sys.stderr)
        return EXIT_DATABASE

    try:
        result = process_attempt(store, job_id, attempt_id)
    except JobInputError:
        print("error: job attempt is not runnable", file=sys.stderr)
        return EXIT_FAILURE_RECORDED
    except psycopg.Error:
        print("error: database operation failed", file=sys.stderr)
        return EXIT_DATABASE
    except Exception:
        print("error: worker failed", file=sys.stderr)
        return EXIT_CODE
    finally:
        store.close()

    if result.outcome == "success":
        return EXIT_SUCCESS
    return EXIT_FAILURE_RECORDED


if __name__ == "__main__":
    raise SystemExit(main())
