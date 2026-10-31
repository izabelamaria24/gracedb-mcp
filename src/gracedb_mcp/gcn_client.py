"""NASA GCN Circulars: rapid follow-up reports from telescopes and detectors worldwide.

Single circulars are public JSON at ``/circulars/<id>.json``. Search has no documented
JSON API, so it uses the data route behind the archive's own search page, which takes
the same parameters as the page (``query`` in Lucene syntax, dates, ``page``, ``limit``).
"""

from __future__ import annotations

import importlib.metadata
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

BASE_URL = "https://gcn.nasa.gov"
_SEARCH_ROUTE = "routes/circulars._archive._index"
TIMEOUT = 30
USER_AGENT = (
    f"gracedb-mcp/{importlib.metadata.version('gracedb-mcp')} "
    "(+https://github.com/izabelamaria24/gracedb-mcp)"
)


class GcnError(RuntimeError):
    pass


def _get_json(url: str) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if "json" not in response.headers.get("Content-Type", ""):
                raise GcnError("GCN returned HTML instead of JSON; its search interface may have changed")
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise GcnError("not found") from exc
        raise GcnError(f"GCN returned HTTP {exc.code}") from exc


def archive_url(query: str | None = None, **params: str) -> str:
    """Link to the same search on the GCN website, for people to follow."""
    params = {k: v for k, v in {"query": query, **params}.items() if v}
    return f"{BASE_URL}/circulars" + (f"?{urllib.parse.urlencode(params)}" if params else "")


def search(
    query: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    limit: int = 100,
    sort: str | None = None,
) -> dict[str, Any]:
    """Search the archive (newest first, or ``sort="relevance"``).

    Returns items (id, subject) and match counts.
    """
    params = {
        "query": query,
        "startDate": start_date,
        "endDate": end_date,
        "page": str(page),
        "limit": str(limit),
        "sort": sort,
        "_data": _SEARCH_ROUTE,
    }
    url = f"{BASE_URL}/circulars?" + urllib.parse.urlencode({k: v for k, v in params.items() if v})
    try:
        data = _get_json(url)
    except GcnError as exc:
        if query and "HTTP 500" in str(exc):
            raise GcnError(
                "GCN could not run this query, most likely invalid Lucene syntax: quote "
                'phrases (subject:"Fermi GBM") and do not end with AND/OR/NOT'
            ) from exc
        raise
    if not isinstance(data, dict) or "items" not in data:
        raise GcnError("unexpected search response; GCN's search interface may have changed")
    return data


@lru_cache(maxsize=2048)
def circular(circular_id: int) -> dict[str, Any]:
    """One circular; cached because circulars rarely change once published."""
    return _get_json(f"{BASE_URL}/circulars/{circular_id}.json")


def summarise(record: dict[str, Any], body: bool = True) -> dict[str, Any]:
    """Public fields of a circular; leaves out the submitter's email address."""
    cid = record["circularId"]
    summary: dict[str, Any] = {
        "id": cid,
        "subject": record.get("subject"),
        "event": record.get("eventId"),
        "created": datetime.fromtimestamp(record["createdOn"] / 1000, timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ) if record.get("createdOn") else None,
        "url": f"{BASE_URL}/circulars/{cid}",
        "ads": f"https://ui.adsabs.harvard.edu/abs/{record['bibcode']}"
        if record.get("bibcode") else None,
    }
    if body:
        summary["body"] = record.get("body")
    return summary
