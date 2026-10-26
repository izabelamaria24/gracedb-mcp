# syntax=docker/dockerfile:1

FROM python:3.11-slim AS base

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
COPY pyproject.toml README.md ./
COPY src ./src

# Install with the optional strain extra so download_strain_data works out of the box.
RUN pip install --no-cache-dir ".[strain]"

# Volume for downloaded skymaps / p_astro / strain files.
RUN mkdir -p /data
VOLUME ["/data"]

# MCP servers speak JSON-RPC over stdio by default.
ENTRYPOINT ["gracedb-mcp"]
