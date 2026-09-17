FROM ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 AS uv

# The Debian package version is deliberately fixed: layout extraction changes
# are data-affecting parser changes and must not occur through a base-image
# rebuild.  Update this value only with a parser-version bump and revalidation.
FROM python:3.11-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84

ARG POPPLER_UTILS_VERSION=22.12.0-2+deb12u3

RUN apt-get update \
    && apt-get install --no-install-recommends -y "poppler-utils=${POPPLER_UTILS_VERSION}" \
    && pdftotext -v 2>&1 | grep -F "pdftotext version 22.12.0" \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# The gateway dispatcher supplies PERSONA_JOB_ID and PERSONA_ATTEMPT_ID to the
# Job and intentionally does not allow a caller-controlled command override.
ENTRYPOINT ["/app/.venv/bin/persona-ingest-job"]
