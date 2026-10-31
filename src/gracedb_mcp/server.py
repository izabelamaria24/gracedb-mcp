"""GraceDB MCP server: LIGO/Virgo/KAGRA alerts and open data for LLM agents.

Modules
-------
- ``app``            Shared state: the MCP instance, GraceDB client, data directory.
- ``tools.alerts``   GraceDB superevents: search, metadata, files, labels.
- ``tools.catalog``  GWOSC catalogues: published events with final parameters.
- ``tools.strain``   GWOSC open data: strain download, availability, GPS time.
- ``tools.observing`` GWOSC observing runs and data-quality flags.
- ``resources``      Downloaded files exposed as ``gracedb://files/<event>/<file>``.
- ``prompts``        Workflows: alert briefing, event dossier, alert vs. catalogue.

Note on strain data: GraceDB does **not** host raw strain. Public strain lives at
GWOSC and is only available for released observing runs.
"""

from __future__ import annotations

from . import prompts, resources  # noqa: F401 - importing registers the prompts
from .app import mcp
from .tools import alerts, catalog, observing, strain  # noqa: F401 - importing registers the tools


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    resources.register_existing_files()
    mcp.run()


if __name__ == "__main__":
    main()
