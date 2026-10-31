"""GWOSC open-data tools: public strain, data availability and GPS time."""

from __future__ import annotations

import importlib.util
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

from gwosc import timeline
from mcp.server.mcpserver import Context

from .. import app, gpstime, gwosc_catalog, resources

# Detector names go into GWOSC URLs, so only allow the standard two-character form.
_IFO = re.compile(r"^[A-Z][0-9]$")
MAX_AVAILABILITY_SPAN = 7 * 86400


def _pick_gwosc_file(urls: list[str], start: float, end: float) -> str | None:
    """Return the shortest GWOSC file that fully covers [start, end], if any.

    GWOSC file names end in ``-<gps_start>-<duration>.hdf5``. Catalogue events often
    have a 32 s file next to the ~100 MB hour-long file, so this avoids large downloads.
    """
    covering = []
    for url in urls:
        try:
            file_start, file_duration = (int(v) for v in url.rsplit(".", 1)[0].split("-")[-2:])
        except ValueError:
            continue
        if file_start <= start and end <= file_start + file_duration:
            covering.append((file_duration, url))
    return min(covering)[1] if covering else None


def _fetch_strain(ifo: str, start: float, end: float, sample_rate: int):
    """Read open strain from GWOSC; set GWPY_CACHE=1 to reuse downloaded files."""
    from gwosc.locate import get_urls
    from gwpy.timeseries import TimeSeries

    url = _pick_gwosc_file(get_urls(ifo, start, end, sample_rate=sample_rate), start, end)
    if url is None:  # window spans several files
        return TimeSeries.fetch_open_data(ifo, start, end, sample_rate=sample_rate)
    return TimeSeries.read(url, format="hdf5.gwosc", start=start, end=end)


@app.mcp.tool()
def download_strain_data(
    graceid: str,
    detectors: list[str] | None = None,
    duration: float = 30.0,
    sample_rate: int = 4096,
    gps_time: float | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Download public strain time series around an event from GWOSC.

    IMPORTANT: GraceDB does not host raw strain. This tool reads the event GPS
    time (``t_0``) from GraceDB, then fetches open strain from GWOSC. It only
    succeeds for events whose data has been publicly released (past observing
    runs); fresh low-latency alerts will not have public strain yet.

    Requires the optional ``gwpy`` dependency (``pip install gracedb-mcp[strain]``).

    Args:
        graceid: The superevent id, e.g. ``S190814bv``. When ``gps_time`` is given
            this is only used as the output folder name (e.g. ``GW150914``).
        detectors: Detector prefixes to fetch, e.g. ``["H1", "L1", "V1"]``.
            Defaults to H1 and L1.
        duration: Length of the segment in seconds, centred on ``t_0``. Up to 30 s
            fits the short file GWOSC publishes around catalogue events (the event is
            not exactly centred in it); longer windows read ~100 MB hour-long files.
        sample_rate: Target sample rate in Hz (4096 or 16384 typically).
        gps_time: Centre time in GPS seconds. Skips the GraceDB lookup, which is
            needed for O1 events such as GW150914 (``1126259462.4``) that predate
            GraceDB superevents.

    Returns:
        A dict mapping each detector to the saved ``.hdf5`` strain file path, and
        ``resources`` mapping it to a ``gracedb://`` URI readable over MCP.
    """
    app.check_id(graceid)
    if importlib.util.find_spec("gwpy") is None:
        return {
            "error": "gwpy is not installed. Install with: pip install 'gracedb-mcp[strain]'",
        }

    if gps_time is not None:
        t_0 = float(gps_time)
    else:
        t_0 = app.client.get_superevent(graceid).get("t_0")
        if t_0 is None:
            return {"error": f"superevent {graceid} has no t_0 GPS time"}

    detectors = detectors or ["H1", "L1"]
    half = duration / 2.0
    start_gps = t_0 - half
    end_gps = t_0 + half

    dest_dir = os.path.join(app.DATA_DIR, graceid, "strain")
    os.makedirs(dest_dir, exist_ok=True)

    def fetch_and_save(ifo: str) -> str:
        data = _fetch_strain(ifo, start_gps, end_gps, sample_rate)
        dest = os.path.join(dest_dir, f"{ifo}-{int(start_gps)}-{int(duration)}.hdf5")
        data.write(dest, overwrite=True)
        return dest

    # Data may not be public for every detector at this time; failures are per detector.
    saved, failed = app.map_concurrently(fetch_and_save, detectors)
    return {
        "superevent_id": graceid,
        "t_0": t_0,
        "gps_window": [start_gps, end_gps],
        "saved": saved,
        "resources": resources.register_saved(saved, ctx),
        "errors": {ifo: str(exc) for ifo, exc in failed.items()},
    }


@app.mcp.tool()
def utc_to_gps(iso_timestamp: str) -> dict[str, Any]:
    """Convert an ISO-8601 UTC timestamp to GPS seconds (handy for building queries).

    Args:
        iso_timestamp: e.g. ``2015-09-14T09:50:45`` (UTC assumed if no tz given).
    """
    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return {"iso": iso_timestamp, "gps": gpstime.utc_to_gps(dt)}


def _event_time(event: str) -> tuple[float, dict[str, Any] | None]:
    """GPS time of a superevent or catalogue event, plus its full catalogue record if any."""
    if event.upper().startswith("GW"):
        record = gwosc_catalog.event_record(event)
        return record["GPS"], record
    t_0 = app.client.get_superevent(event).get("t_0")
    if t_0 is None:
        raise ValueError(f"superevent {event} has no t_0 GPS time")
    match = gwosc_catalog.find_by_gps(t_0)
    return t_0, gwosc_catalog.event_record(match["commonName"]) if match else None


class Span:
    """A validated GPS span, optionally centred on an event."""

    def __init__(
        self,
        gps_start: float,
        gps_end: float,
        event: str | None = None,
        t_0: float | None = None,
        record: dict[str, Any] | None = None,
    ) -> None:
        self.gps_start, self.gps_end = gps_start, gps_end
        self.event, self.t_0, self.record = event, t_0, record
        # Whole seconds covering the span, as GWOSC segment queries expect.
        self.start, self.end = math.floor(gps_start), math.ceil(gps_end)

    def coverage(self, segments: list[tuple[int, int]]) -> tuple[list[list[int]], float]:
        """Clip segments to the span; return them and the fraction of the span covered."""
        clipped = [(max(s, self.start), min(e, self.end)) for s, e in segments]
        clipped = [(s, e) for s, e in clipped if e > s]
        covered = sum(e - s for s, e in clipped)
        return [list(seg) for seg in clipped], round(covered / (self.end - self.start), 4)


def resolve_span(
    gps_start: float | None,
    gps_end: float | None,
    event: str | None,
    window: float,
    detectors: list[str] | None,
) -> tuple[Span, list[str]] | dict[str, str]:
    """Validate a span (or event + window) and detector names; return an error dict if bad."""
    t_0 = record = None
    if event is not None:
        app.check_id(event)
        t_0, record = _event_time(event)
        gps_start, gps_end = t_0 - window / 2, t_0 + window / 2
    if gps_start is None or gps_end is None:
        return {"error": "give either gps_start and gps_end, or event"}
    if not 0 < gps_end - gps_start <= MAX_AVAILABILITY_SPAN:
        return {"error": f"span must be positive and at most {MAX_AVAILABILITY_SPAN} s (7 days)"}
    detectors = detectors or ["H1", "L1", "V1", "K1"]
    bad = [d for d in detectors if not _IFO.match(d)]
    if bad:
        return {"error": f"invalid detector names {bad}; use e.g. H1, L1, V1, K1"}
    return Span(gps_start, gps_end, event, t_0, record), detectors


@app.mcp.tool()
def check_data_availability(
    gps_start: float | None = None,
    gps_end: float | None = None,
    event: str | None = None,
    window: float = 64.0,
    detectors: list[str] | None = None,
) -> dict[str, Any]:
    """Check which detectors have public GWOSC strain data for a time span.

    Call this before ``download_strain_data``. Public strain exists only for
    released observing runs, and detectors are often offline, so coverage is
    frequently partial.

    Args:
        gps_start: Start of the span in GPS seconds. Use together with ``gps_end``.
        gps_end: End of the span in GPS seconds, at most 7 days after ``gps_start``.
        event: Instead of a span, a GraceDB superevent id (``S190814bv``) or a
            catalogue name (``GW150914``); checks ``window`` seconds centred on it.
        window: Span in seconds around ``event``.
        detectors: Detectors to check. Defaults to H1, L1, V1 and K1.

    Returns:
        For each detector, the public data segments and the fraction of the span
        they cover. ``available`` lists detectors with complete coverage, also
        counting the short files GWOSC publishes around catalogue events.
    """
    resolved = resolve_span(gps_start, gps_end, event, window, detectors)
    if isinstance(resolved, dict):
        return resolved
    span, detectors = resolved
    segments_by_ifo, failed = app.map_concurrently(
        lambda ifo: timeline.get_segments(f"{ifo}_DATA", span.start, span.end), detectors
    )
    report: dict[str, Any] = {}
    for ifo, raw in segments_by_ifo.items():
        segments, coverage = span.coverage(raw)
        report[ifo] = {"segments": segments, "coverage": coverage}
    errors = {ifo: str(exc) for ifo, exc in failed.items()}

    record = span.record
    event_files = (
        gwosc_catalog.strain_detectors(record, span.gps_start, span.gps_end) if record else []
    )
    result: dict[str, Any] = {
        "gps_window": [span.gps_start, span.gps_end],
        "available": sorted(
            ifo for ifo in detectors
            if ifo in event_files or report.get(ifo, {}).get("coverage") == 1
        ),
        "detectors": report,
        "errors": errors,
    }
    if span.event is not None:
        result.update(event=span.event, t_0=span.t_0)
    if record:
        result["catalog_event_files"] = {"event": record["commonName"], "detectors": event_files}
    if not errors and not event_files and not any(r["coverage"] for r in report.values()):
        result["note"] = (
            "No public data in this span: the observing run may not be released yet, "
            "or the detectors were off."
        )
    return result
