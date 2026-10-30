"""Downloaded files exposed as MCP resources at ``gracedb://files/<event>/<file>``."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import anyio.from_thread
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.resources import FileResource

from . import app

# Larger files (e.g. full PE posteriors) are too big to pass through an agent's context.
MAX_RESOURCE_BYTES = 50 * 1024 * 1024
_MIME_TYPES = {
    ".json": "application/json",
    ".xml": "application/xml",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".csv": "text/csv",
    ".html": "text/html",
    ".png": "image/png",
    ".fits": "application/fits",
    ".gz": "application/gzip",
    ".hdf5": "application/x-hdf5",
    ".h5": "application/x-hdf5",
}


def mime_type(filename: str) -> str:
    # GraceDB versions files as "name.ext,N".
    suffix = Path(filename.split(",")[0]).suffix.lower()
    return _MIME_TYPES.get(suffix, "application/octet-stream")


def register_file(path: str) -> str | None:
    """Expose a downloaded file as an MCP resource and return its URI (None if too large)."""
    file = Path(path).resolve()
    if not file.is_file() or file.name.startswith("."):
        return None
    size = file.stat().st_size
    if size > MAX_RESOURCE_BYTES:
        return None
    relative = file.relative_to(Path(app.DATA_DIR).resolve()).as_posix()
    uri = f"gracedb://files/{quote(relative, safe='/,')}"
    app.mcp.add_resource(
        FileResource(
            uri=uri,
            name=relative,
            description=f"Downloaded file, {size} bytes.",
            mime_type=mime_type(file.name),
            path=file,
        )
    )
    return uri


def register_existing_files() -> None:
    for root, _, names in os.walk(app.DATA_DIR):
        for name in names:
            register_file(os.path.join(root, name))


def register_saved(saved: dict[str, str], ctx: Context | None) -> dict[str, str]:
    """Register newly saved files, notify clients, and return ``{key: uri}``."""
    uris = {key: uri for key, path in saved.items() if (uri := register_file(path))}
    if uris and ctx is not None:

        async def notify() -> None:
            await ctx.notify_resources_changed()  # 2026-07-28 clients, via subscriptions/listen
            await ctx.session.send_resource_list_changed()  # clients on earlier protocol versions

        # Sync tools run in a worker thread; the notification is sent from the event loop.
        anyio.from_thread.run(notify)
    return uris
