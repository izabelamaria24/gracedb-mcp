# syntax=docker/dockerfile:1

# EXTRAS=strain (default) includes gwpy for strain download; EXTRAS="" builds the slim image.
ARG EXTRAS=strain

FROM python:3.11-slim AS build
ARG EXTRAS

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Bundled package test suites are unused at runtime; astropy imports its own on startup.
RUN python -m venv --without-pip /venv \
    && pip --python /venv/bin/python install --no-cache-dir ".${EXTRAS:+[$EXTRAS]}" \
    && find /venv -depth -type d \( -name tests -o -name test \) \
        -not -path "*/astropy/tests" -exec rm -rf {} +

FROM python:3.11-slim

# Lets the MCP Registry verify that this image belongs to the server entry.
LABEL io.modelcontextprotocol.server.name="io.github.izabelamaria24/gracedb-mcp" \
      org.opencontainers.image.source="https://github.com/izabelamaria24/gracedb-mcp" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GRACEDB_MCP_DATA_DIR=/data

COPY --from=build /venv /venv

# Volume for downloaded skymaps / p_astro / strain files, owned by an unprivileged user.
RUN useradd --create-home --uid 1000 mcp \
    && mkdir -p /data \
    && chown mcp:mcp /data
USER mcp
VOLUME ["/data"]

# MCP servers speak JSON-RPC over stdio by default.
ENTRYPOINT ["gracedb-mcp"]
