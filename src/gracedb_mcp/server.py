"""GraceDB MCP server.

Exposes LIGO/Virgo gravitational-wave superevents to MCP-compatible LLM agents.

Tools
-----
- ``search_superevents``   Query recent candidate superevents by time + significance.
- ``get_event_metadata``   Full record + source classification (BBH/BNS/NSBH/...).
- ``download_event_files`` Fetch GraceDB artifacts (skymaps, p_astro, PE, logs).
- ``download_strain_data`` Fetch *public* strain time series from GWOSC by GPS time.
- ``search_catalog_events`` Published events by masses, distance, SNR (GWOSC catalogues).
- ``get_catalog_event``    Parameters with 90% intervals for one event (GW name or GraceDB id).
- ``get_event_labels``     GraceDB labels (data quality, review, retraction) with meanings.
- ``check_data_availability`` Which detectors have public GWOSC strain for a time span.

Note on strain data: GraceDB does **not** host raw strain. Public strain lives at
GWOSC and is only available for released observing runs, so ``download_strain_data``
works for archival events, not fresh low-latency alerts.
"""

from __future__ import annotations

import importlib.util
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from gwosc import timeline
from mcp.server.mcpserver import MCPServer

from . import gwosc_catalog
from .gracedb_client import GraceDbClient

load_dotenv()

mcp = MCPServer(name="gracedb-mcp")
_client = GraceDbClient()

# The Docker image sets GRACEDB_MCP_DATA_DIR=/data.
DATA_DIR = os.path.expanduser(os.environ.get("GRACEDB_MCP_DATA_DIR", "~/.cache/gracedb-mcp"))

# Event ids become directory names, so reject anything that could escape DATA_DIR.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _check_id(event_id: str) -> None:
    if not _SAFE_ID.match(event_id):
        raise ValueError(f"invalid event id: {event_id!r}")


def _build_query(
    start: str | None,
    end: str | None,
    max_far: float | None,
    category: str,
) -> str:
    """Assemble a GraceDB superevent query string from user-friendly arguments."""
    clauses: list[str] = []
    if start and end:
        clauses.append(f"created: {start} .. {end}")
    elif start:
        clauses.append(f"created: {start} ..")
    elif end:
        clauses.append(f"created: .. {end}")
    if max_far is not None:
        clauses.append(f"far < {max_far:g}")
    if category:
        clauses.append(f"category: {category}")
    return " ".join(clauses)


def _astro_probability(p_astro: dict[str, Any] | None) -> float | None:
    """Return P(astrophysical) = 1 - P(Terrestrial) if a p_astro record exists."""
    if not p_astro:
        return None
    terrestrial = p_astro.get("Terrestrial")
    if terrestrial is None:
        return None
    return round(1.0 - float(terrestrial), 6)


@mcp.tool()
def search_superevents(
    start: str | None = None,
    end: str | None = None,
    max_far: float | None = None,
    min_confidence: float | None = None,
    category: str = "Production",
    max_results: int = 20,
) -> dict[str, Any]:
    """Search recent GraceDB superevents.

    Args:
        start: Inclusive lower bound on creation time (ISO-8601, e.g. ``2023-05-01``
            or ``2023-05-01T00:00:00``). Optional.
        end: Inclusive upper bound on creation time (same format). Optional.
        max_far: Maximum false-alarm rate in Hz. Lower = more significant. A common
            "confident detection" threshold is ``3.2e-8`` (~1 per year). Optional.
        min_confidence: Minimum astrophysical probability in [0, 1], computed as
            ``1 - P(Terrestrial)`` from each event's p_astro file. Applied as a
            post-filter (requires one extra fetch per event). Optional.
        category: GraceDB category, usually ``Production``, ``Test`` or ``MDC``.
        max_results: Maximum number of superevents to return.

    Returns:
        A dict with the resolved ``query`` string and a list of matching ``superevents``.
    """
    query = _build_query(start, end, max_far, category)
    records = _client.search_superevents(query, max_results=max_results * 2 if min_confidence else max_results)

    events: list[dict[str, Any]] = []
    for record in records:
        summary = {
            "superevent_id": record.get("superevent_id"),
            "created": record.get("created"),
            "t_0": record.get("t_0"),
            "far": record.get("far"),
            "preferred_event": record.get("preferred_event"),
            "labels": record.get("labels"),
        }
        if min_confidence is not None:
            sid = record.get("superevent_id")
            p_astro = _client.get_p_astro(sid) if sid else None
            confidence = _astro_probability(p_astro)
            summary["astro_probability"] = confidence
            if confidence is None or confidence < min_confidence:
                continue
        events.append(summary)
        if len(events) >= max_results:
            break

    return {"query": query, "count": len(events), "superevents": events}


@mcp.tool()
def get_event_metadata(graceid: str) -> dict[str, Any]:
    """Fetch the full record and source classification for one superevent.

    Args:
        graceid: The superevent id, e.g. ``S230518h``.

    Returns:
        A dict with the raw superevent record, the available file list, and the
        parsed source-classification probabilities (BBH / BNS / NSBH / Terrestrial)
        when a p_astro file has been published.
    """
    record = _client.get_superevent(graceid)
    files = _client.list_files(graceid)
    p_astro = _client.get_p_astro(graceid)

    return {
        "superevent_id": graceid,
        "record": record,
        "classification": p_astro,
        "astro_probability": _astro_probability(p_astro),
        "available_files": sorted(files.keys()),
    }


@mcp.tool()
def download_event_files(
    graceid: str,
    filenames: list[str] | None = None,
) -> dict[str, Any]:
    """Download GraceDB artifacts (skymaps, p_astro, PE results, logs) for an event.

    These are the files GraceDB actually hosts. This is NOT raw strain data
    (use ``download_strain_data`` for that).

    Args:
        graceid: The superevent id, e.g. ``S230518h``.
        filenames: Specific filenames to download. If omitted, downloads every
            file attached to the superevent.

    Returns:
        A dict mapping each requested filename to the local path it was saved to.
    """
    _check_id(graceid)
    available = _client.list_files(graceid)
    targets = filenames or list(available.keys())

    dest_dir = os.path.join(DATA_DIR, graceid)
    os.makedirs(dest_dir, exist_ok=True)

    saved: dict[str, str] = {}
    errors: dict[str, str] = {}
    for name in targets:
        if name not in available:
            errors[name] = "not found on this superevent"
            continue
        try:
            dest = os.path.join(dest_dir, os.path.basename(name))
            saved[name] = _client.download_file(graceid, name, dest)
        except Exception as exc:  # noqa: BLE001 - surface per-file failures without aborting the batch
            errors[name] = str(exc)

    return {"superevent_id": graceid, "saved": saved, "errors": errors}


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


@mcp.tool()
def download_strain_data(
    graceid: str,
    detectors: list[str] | None = None,
    duration: float = 32.0,
    sample_rate: int = 4096,
    gps_time: float | None = None,
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
        duration: Length of the segment in seconds, centred on ``t_0``.
        sample_rate: Target sample rate in Hz (4096 or 16384 typically).
        gps_time: Centre time in GPS seconds. Skips the GraceDB lookup, which is
            needed for O1 events such as GW150914 (``1126259462.4``) that predate
            GraceDB superevents.

    Returns:
        A dict mapping each detector to the saved ``.hdf5`` strain file path.
    """
    _check_id(graceid)
    if importlib.util.find_spec("gwpy") is None:
        return {
            "error": "gwpy is not installed. Install with: pip install 'gracedb-mcp[strain]'",
        }

    if gps_time is not None:
        t_0 = float(gps_time)
    else:
        t_0 = _client.get_superevent(graceid).get("t_0")
        if t_0 is None:
            return {"error": f"superevent {graceid} has no t_0 GPS time"}

    detectors = detectors or ["H1", "L1"]
    half = duration / 2.0
    start_gps = t_0 - half
    end_gps = t_0 + half

    dest_dir = os.path.join(DATA_DIR, graceid, "strain")
    os.makedirs(dest_dir, exist_ok=True)

    saved: dict[str, str] = {}
    errors: dict[str, str] = {}
    for ifo in detectors:
        try:
            data = _fetch_strain(ifo, start_gps, end_gps, sample_rate)
            dest = os.path.join(dest_dir, f"{ifo}-{int(start_gps)}-{int(duration)}.hdf5")
            data.write(dest, overwrite=True)
            saved[ifo] = dest
        except Exception as exc:  # noqa: BLE001 - data may not be public for this GPS time
            errors[ifo] = str(exc)

    return {
        "superevent_id": graceid,
        "t_0": t_0,
        "gps_window": [start_gps, end_gps],
        "saved": saved,
        "errors": errors,
    }


@mcp.tool()
def utc_to_gps(iso_timestamp: str) -> dict[str, Any]:
    """Convert an ISO-8601 UTC timestamp to GPS seconds (handy for building queries).

    Args:
        iso_timestamp: e.g. ``2015-09-14T09:50:45`` (UTC assumed if no tz given).
    """
    try:
        from gwpy.time import to_gps
    except ImportError:
        return {"error": "gwpy is required for GPS conversion: pip install 'gracedb-mcp[strain]'"}

    dt = datetime.fromisoformat(iso_timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return {"iso": iso_timestamp, "gps": float(to_gps(dt))}


def _within(value: float | None, low: float | None, high: float | None) -> bool:
    if low is None and high is None:
        return True
    if value is None:
        return False
    return (low is None or value >= low) and (high is None or value <= high)


@mcp.tool()
def search_catalog_events(
    catalog: str | None = None,
    gps_start: float | None = None,
    gps_end: float | None = None,
    min_snr: float | None = None,
    min_p_astro: float | None = None,
    min_total_mass: float | None = None,
    max_total_mass: float | None = None,
    max_distance_mpc: float | None = None,
    max_results: int = 50,
) -> dict[str, Any]:
    """Search published gravitational-wave events in the GWOSC catalogues.

    Unlike GraceDB alerts, catalogue events are vetted detections with estimated
    source parameters. Each event appears once, in its newest version.

    Args:
        catalog: Restrict to one catalogue, e.g. ``GWTC-1-confident``,
            ``GWTC-2.1-confident``, ``GWTC-3-confident``, ``GWTC-4.0``. Omit for all.
        gps_start: Earliest event GPS time. Optional.
        gps_end: Latest event GPS time. Optional.
        min_snr: Minimum network matched-filter SNR (louder signals are clearer).
        min_p_astro: Minimum probability of astrophysical origin, 0-1.
        min_total_mass: Minimum total source-frame mass in solar masses.
        max_total_mass: Maximum total source-frame mass in solar masses.
        max_distance_mpc: Maximum luminosity distance in megaparsecs.
        max_results: Maximum number of events to return (newest first).

    Returns:
        ``count`` of all matches and up to ``max_results`` event summaries with
        masses, distance, redshift, spin (chi_eff), SNR, FAR and p_astro.
    """
    if catalog and catalog not in gwosc_catalog.catalogs():
        return {"error": f"unknown catalog {catalog!r}", "catalogs": gwosc_catalog.catalogs()}

    bounds = [
        ("network_matched_filter_snr", min_snr, None),
        ("p_astro", min_p_astro, None),
        ("total_mass_source", min_total_mass, max_total_mass),
        ("luminosity_distance", None, max_distance_mpc),
    ]
    matches = [
        gwosc_catalog.summarise(record)
        for record in gwosc_catalog.all_events(catalog)
        if _within(record["GPS"], gps_start, gps_end)
        and all(_within(record.get(param), low, high) for param, low, high in bounds)
    ]
    return {
        "count": len(matches),
        "units": gwosc_catalog.UNITS,
        "events": matches[:max_results],
    }


@mcp.tool()
def get_catalog_event(name: str) -> dict[str, Any]:
    """Source parameters for one published event, with 90% credible intervals.

    Args:
        name: A catalogue name such as ``GW150914`` or ``GW200129_065458``, or a
            GraceDB superevent id such as ``S190814bv`` (matched by GPS time).

    Returns:
        Masses, distance, redshift, spin, SNR, FAR and p_astro as
        ``{value, interval_90}``, plus the GraceDB id, available strain files,
        parameter-estimation sets and the GWOSC event page.
    """
    _check_id(name)
    if not name.upper().startswith("GW"):
        t_0 = _client.get_superevent(name).get("t_0")
        match = gwosc_catalog.find_by_gps(t_0) if t_0 is not None else None
        if match is None:
            return {
                "error": f"{name} is not in any GWOSC catalogue (not yet published, "
                "or retracted)",
                "t_0": t_0,
            }
        name = match["commonName"]

    try:
        record = gwosc_catalog.event_record(name)
    except ValueError:
        prefix = name.upper()[:8]
        similar = [r["commonName"] for r in gwosc_catalog.all_events()
                   if r["commonName"].upper().startswith(prefix)]
        return {"error": f"unknown event {name!r}", "similar": similar[:10]}

    strain = sorted(
        {(s["detector"], s["duration"], s["sampling_rate"])
         for s in record.get("strain", []) if s.get("format") == "hdf5"}
    )
    return {
        **gwosc_catalog.summarise(record, intervals=True),
        "version": record["version"],
        "gracedb_id": record.get("gracedb_id"),
        "units": gwosc_catalog.UNITS,
        "strain_files": [
            {"detector": d, "duration_s": dur, "sample_rate": rate} for d, dur, rate in strain
        ],
        "parameter_sets": sorted(record.get("parameters", {})),
        "gwosc_page": gwosc_catalog.event_page(record),
    }


LABEL_MEANINGS = {
    "ADVREQ": "Human review (advocate) requested before a public alert.",
    "ADVOK": "Advocate approved the candidate.",
    "ADVNO": "Advocate rejected the candidate; it is retracted.",
    "DQOK": "Automated data-quality checks passed.",
    "DQV": "Data-quality veto: an instrumental problem near the event time.",
    "DQR_REQUEST": "Data-quality report requested.",
    "INJ": "Injection (simulated signal), not a real event.",
    "EM_READY": "Initial information ready for electromagnetic follow-up.",
    "EM_Selected": "Preferred event chosen for the public alert.",
    "SIGNIF_LOCKED": "Preferred event frozen for a significant alert.",
    "LOW_SIGNIF_LOCKED": "Preferred event frozen for a low-significance alert.",
    "SKYMAP_READY": "Sky map available.",
    "PASTRO_READY": "Source classification (p_astro) available.",
    "EMBRIGHT_READY": "Neutron-star and remnant probabilities (HasNS, HasRemnant) available.",
    "PE_READY": "Full parameter estimation finished.",
    "GCN_PRELIM_SENT": "Preliminary GCN notice sent.",
    "RAVEN_ALERT": "Coincidence with an external trigger (e.g. gamma-ray burst) found.",
    "EM_COINC": "Possible coincidence with an external (gamma-ray or neutrino) event.",
    "COMBINEDSKYMAP_READY": "Sky map combining GW and external-trigger data available.",
    "EARLY_WARNING": "Detected before merger (pre-merger alert).",
}
for _ifo in ("H1", "L1", "V1", "K1"):
    LABEL_MEANINGS[f"{_ifo}OPS"] = f"{_ifo} operator sign-off requested."
    LABEL_MEANINGS[f"{_ifo}OK"] = f"{_ifo} operator confirmed the detector was fine."
    LABEL_MEANINGS[f"{_ifo}NO"] = f"{_ifo} operator reported a detector problem."


@mcp.tool()
def get_event_labels(graceid: str) -> dict[str, Any]:
    """GraceDB labels on a superevent, in the order they were applied, with meanings.

    Labels record the event's history: data-quality checks (DQOK, DQV), human
    review (ADVOK, ADVNO = retracted), injections (INJ), and which products are
    ready (SKYMAP_READY, PE_READY).

    Args:
        graceid: The superevent id, e.g. ``S190814bv``.
    """
    _check_id(graceid)
    labels = sorted(_client.get_labels(graceid), key=lambda label: label.get("created") or "")
    return {
        "superevent_id": graceid,
        "retracted": any(label["name"] == "ADVNO" for label in labels),
        "labels": [
            {
                "name": label["name"],
                "created": label.get("created"),
                "meaning": LABEL_MEANINGS.get(label["name"]),
            }
            for label in labels
        ],
    }


# Detector names go into GWOSC URLs, so only allow the standard two-character form.
_IFO = re.compile(r"^[A-Z][0-9]$")
MAX_AVAILABILITY_SPAN = 7 * 86400


def _event_time(event: str) -> tuple[float, dict[str, Any] | None]:
    """GPS time of a superevent or catalogue event, plus its full catalogue record if any."""
    if event.upper().startswith("GW"):
        record = gwosc_catalog.event_record(event)
        return record["GPS"], record
    t_0 = _client.get_superevent(event).get("t_0")
    if t_0 is None:
        raise ValueError(f"superevent {event} has no t_0 GPS time")
    match = gwosc_catalog.find_by_gps(t_0)
    return t_0, gwosc_catalog.event_record(match["commonName"]) if match else None


@mcp.tool()
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
    t_0 = record = None
    if event is not None:
        _check_id(event)
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

    start, end = math.floor(gps_start), math.ceil(gps_end)
    with ThreadPoolExecutor(max_workers=len(detectors)) as pool:
        futures = {
            ifo: pool.submit(timeline.get_segments, f"{ifo}_DATA", start, end) for ifo in detectors
        }

    report: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for ifo, future in futures.items():
        try:
            segments = [(max(s, start), min(e, end)) for s, e in future.result()]
        except Exception as exc:  # noqa: BLE001 - one detector failing shouldn't hide the rest
            errors[ifo] = str(exc)
            continue
        covered = sum(e - s for s, e in segments if e > s)
        report[ifo] = {
            "segments": [list(seg) for seg in segments],
            "coverage": round(covered / (end - start), 4),
        }

    event_files = gwosc_catalog.strain_detectors(record, gps_start, gps_end) if record else []
    result: dict[str, Any] = {
        "gps_window": [gps_start, gps_end],
        "available": sorted(
            ifo for ifo in detectors
            if ifo in event_files or report.get(ifo, {}).get("coverage") == 1
        ),
        "detectors": report,
        "errors": errors,
    }
    if event is not None:
        result.update(event=event, t_0=t_0)
    if record:
        result["catalog_event_files"] = {"event": record["commonName"], "detectors": event_files}
    if not errors and not event_files and not any(r["coverage"] for r in report.values()):
        result["note"] = (
            "No public data in this span: the observing run may not be released yet, "
            "or the detectors were off."
        )
    return result


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
