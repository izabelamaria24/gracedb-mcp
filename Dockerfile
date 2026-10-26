# syntax=docker/dockerfile:1

FROM python:3.11-slim AS base

# Lets the MCP Registry verify that this image belongs to the server entry.
LABEL io.modelcontextprotocol.server.name="io.github.izabelamaria24/gracedb-mcp" \
      org.opencontainers.image.source="https://github.com/izabelamaria24/gracedb-mcp" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

# ligo-gracedb / gwpy pull in scientific wheels; these system libs cover the gaps.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GRACEDB_MCP_DATA_DIR=/data

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Install with the optional strain extra so download_strain_data works out of the box.
RUN pip install --no-cache-dir ".[strain]"

# Volume for downloaded skymaps / p_astro / strain files, owned by an unprivileged user.
RUN useradd --create-home --uid 1000 mcp \
    && mkdir -p /data \
    && chown mcp:mcp /data
USER mcp
VOLUME ["/data"]

# MCP servers speak JSON-RPC over stdio by default.
ENTRYPOINT ["gracedb-mcp"]
