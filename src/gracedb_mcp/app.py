"""Shared server state: the MCP instance, the GraceDB client and the data directory.

Tool modules read these through the module (``app.client``, ``app.DATA_DIR``) rather
than importing the names, so tests can swap them with ``monkeypatch.setattr(app, ...)``.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from .gracedb_client import GraceDbClient

load_dotenv()

# Re-downloading a file re-registers the same URI, which is expected.
mcp = MCPServer(
    name="gracedb-mcp",
    version=importlib.metadata.version("gracedb-mcp"),
    warn_on_duplicate_resources=False,
)
client = GraceDbClient()

# The Docker image sets GRACEDB_MCP_DATA_DIR=/data.
DATA_DIR = os.path.expanduser(os.environ.get("GRACEDB_MCP_DATA_DIR", "~/.cache/gracedb-mcp"))

# Event ids become directory names, so reject anything that could escape DATA_DIR.
SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def check_id(event_id: str) -> None:
    if not SAFE_ID.match(event_id):
        raise ValueError(f"invalid event id: {event_id!r}")


K = TypeVar("K")
V = TypeVar("V")


def map_concurrently(
    fn: Callable[[K], V], items: Iterable[K], max_workers: int = 8
) -> tuple[dict[K, V], dict[K, Exception]]:
    """Call ``fn`` on each item in parallel threads (the work is network-bound).

    Returns ``(results, errors)`` keyed by item, in input order, so one failure
    does not hide the other results.
    """
    keys = list(dict.fromkeys(items))
    if not keys:
        return {}, {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(keys))) as pool:
        futures = {key: pool.submit(fn, key) for key in keys}
    results: dict[K, V] = {}
    errors: dict[K, Exception] = {}
    for key, future in futures.items():
        try:
            results[key] = future.result()
        except Exception as exc:  # noqa: BLE001 - reported per item by the caller
            errors[key] = exc
    return results, errors
