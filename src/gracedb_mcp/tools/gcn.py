"""GCN Circulars tools: what telescopes and other detectors reported about an event."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .. import app, gcn_client, gwosc_catalog

# Event names go inside a quoted Lucene phrase, so quotes and backslashes are not allowed.
_EVENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _./+-]{0,63}$")
MAX_RESULTS = 50


def _names_for(event: str) -> list[str]:
    """Names circulars may use for an event; GW catalogue names map to their GraceDB id."""
    names = [event]
    if event.upper().startswith("GW"):
        try:
            gracedb_id = gwosc_catalog.event_record(event).get("gracedb_id")
        except ValueError:
            gracedb_id = None
        if gracedb_id:
            names.append(gracedb_id)
    return names


def _check_date(value: str | None) -> str | None:
    if value is not None:
        date.fromisoformat(value)
    return value


@app.mcp.tool()
def search_gcn_circulars(
    event: str | None = None,
    query: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort: str = "newest",
    page: int = 1,
    include_bodies: bool = False,
    max_results: int = 25,
) -> dict[str, Any]:
    """Search NASA GCN Circulars: rapid reports from telescopes, satellites and neutrino
    detectors about transients, including follow-up of gravitational-wave alerts.

    Reports name gravitational-wave events by their GraceDB id (``S230518h``), so a
    catalogue name such as ``GW190814`` is also searched as ``S190814bv``.

    Args:
        event: An event name: GraceDB id (``S230518h``), GW name (``GW170817``), GRB
            (``GRB 230518A``), IceCube alert (``IceCube-261020A``), etc. Matches
            circulars with the name in their subject, i.e. reports about the event.
        query: Free search in Lucene syntax over ``subject``, ``body`` and ``submitter``,
            e.g. ``subject:"Fermi" AND body:"upper limit"``. Combined with ``event``
            using AND when both are given. Use ``body:"S230518h"`` to also find
            circulars that only mention an event in passing.
        start_date: Only circulars submitted on or after this date (``YYYY-MM-DD``).
        end_date: Only circulars submitted on or before this date (``YYYY-MM-DD``).
        sort: ``newest`` (default) or ``relevance`` (best for well-studied events with
            hundreds of circulars, such as GW170817).
        page: Page of results, for more than ``max_results`` matches.
        include_bodies: Include each circular's full text (about 1-3 kB each).
        max_results: Circulars per page, at most 50.

    Returns:
        ``total`` matching circulars and one page of them with id, subject, event,
        submission time, links (GCN page, ADS for citation), and optionally the text;
        plus ``archive_url`` to see the same search on the GCN website.
    """
    if event is None and query is None and start_date is None and end_date is None:
        return {"error": "give an event, a query, or a date range"}
    if event is not None and not _EVENT_NAME.match(event):
        return {"error": f"invalid event name {event!r}"}
    if sort not in ("newest", "relevance"):
        return {"error": "sort must be 'newest' or 'relevance'"}
    try:
        _check_date(start_date)
        _check_date(end_date)
    except ValueError:
        return {"error": "dates must be YYYY-MM-DD"}
    max_results = max(1, min(max_results, MAX_RESULTS))
    page = max(1, page)

    clauses = []
    names = _names_for(event) if event else []
    if names:
        clauses.append(" OR ".join(f'subject:"{name}"' for name in names))
    if query:
        clauses.append(query)
    lucene = " AND ".join(f"({c})" for c in clauses) if len(clauses) > 1 else (clauses or [None])[0]

    try:
        found = gcn_client.search(
            lucene, start_date, end_date, page=page, limit=max_results,
            sort="relevance" if sort == "relevance" else None,
        )
        ids = [int(item["circularId"]) for item in found["items"][:max_results]]
        records, failed = app.map_concurrently(gcn_client.circular, ids)
    except gcn_client.GcnError as exc:
        return {"error": f"GCN search failed: {exc}", "query": lucene}

    result: dict[str, Any] = {
        "query": lucene,
        "total": found.get("totalItems", len(ids)),
        "page": page,
        "pages": found.get("totalPages"),
        "circulars": [
            gcn_client.summarise(records[i], body=include_bodies)
            if i in records else {"id": i, "error": str(failed[i])}
            for i in ids
        ],
        "archive_url": gcn_client.archive_url(
            lucene, startDate=start_date, endDate=end_date,
            sort="relevance" if sort == "relevance" else None,
        ),
    }
    if names[1:]:
        result["names_searched"] = names
    if found.get("queryFallback"):
        result["note"] = (
            "GCN could not parse the query as Lucene syntax and fell back to a plain-text "
            "search; results may be broader than intended."
        )
    return result


@app.mcp.tool()
def get_gcn_circular(circular_id: int) -> dict[str, Any]:
    """Full text of one GCN Circular, with its links (GCN page, ADS for citation).

    Args:
        circular_id: The circular number, e.g. ``33813``.
    """
    if circular_id < 1:
        return {"error": "circular ids are positive integers"}
    try:
        return gcn_client.summarise(gcn_client.circular(circular_id))
    except gcn_client.GcnError as exc:
        return {"error": f"circular {circular_id}: {exc}"}
