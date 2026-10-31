"""GraceDB tools: low-latency alerts (superevents), their files and labels."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from mcp.server.mcpserver import Context

from .. import app, resources

GRACEDB_TIME = "%Y-%m-%d %H:%M:%S"
_EARLIEST = "2015-01-01"  # before the first GraceDB superevent


def _gracedb_time(value: str) -> str:
    """Normalise an ISO-8601 time; GraceDB's parser rejects the 'T' separator and offsets."""
    if len(value) == 10:  # plain date
        return datetime.fromisoformat(value).date().isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.strftime(GRACEDB_TIME)


def _build_query(
    start: str | None,
    end: str | None,
    max_far: float | None,
    category: str,
) -> str:
    """Assemble a GraceDB superevent query string from user-friendly arguments."""
    clauses: list[str] = []
    # GraceDB rejects open-ended ranges, so fill in a missing bound.
    if start or end:
        low = _gracedb_time(start) if start else _EARLIEST
        high = _gracedb_time(end) if end else datetime.now(timezone.utc).strftime(GRACEDB_TIME)
        clauses.append(f"created: {low} .. {high}")
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


def _preferred_event(record: dict[str, Any]) -> dict[str, Any]:
    """Pipeline, search and detectors of the candidate chosen to represent the superevent."""
    event = record.get("preferred_event_data") or {}
    instruments = event.get("instruments")
    return {
        "pipeline": event.get("pipeline"),
        "search": event.get("search"),
        "detectors": instruments.split(",") if instruments else None,
    }


@app.mcp.tool()
def search_superevents(
    start: str | None = None,
    end: str | None = None,
    max_far: float | None = None,
    min_confidence: float | None = None,
    category: str = "Production",
    max_results: int = 20,
) -> dict[str, Any]:
    """Search GraceDB superevents, newest first.

    Args:
        start: Inclusive lower bound on creation time (ISO-8601, e.g. ``2023-05-01``
            or ``2023-05-01T00:00:00``). Optional.
        end: Inclusive upper bound on creation time (same format). Optional.
        max_far: Maximum false-alarm rate in Hz. Lower = more significant. A common
            "confident detection" threshold is ``3.2e-8`` (~1 per year). Optional.
        min_confidence: Minimum astrophysical probability in [0, 1], computed as
            ``1 - P(Terrestrial)`` from each event's p_astro file. Applied as a
            post-filter (requires extra fetches per event). Optional.
        category: ``Production`` (real alerts), ``MDC`` (simulated signals injected
            continuously, useful for testing) or ``Test``.
        max_results: Maximum number of superevents to return.

    Returns:
        A dict with the resolved ``query`` string and a list of matching ``superevents``.
    """
    query = _build_query(start, end, max_far, category)
    records = app.client.search_superevents(
        query, max_results=max_results * 2 if min_confidence else max_results
    )
    events = [
        {
            "superevent_id": record.get("superevent_id"),
            "created": record.get("created"),
            "t_0": record.get("t_0"),
            "far": record.get("far"),
            "preferred_event": record.get("preferred_event"),
            **_preferred_event(record),
            "labels": record.get("labels"),
        }
        for record in records
    ]
    if min_confidence is not None:
        ids = [e["superevent_id"] for e in events if e["superevent_id"]]
        p_astros, _ = app.map_concurrently(app.client.get_p_astro, ids)
        for event in events:
            event["astro_probability"] = _astro_probability(p_astros.get(event["superevent_id"]))
        events = [
            e for e in events
            if e["astro_probability"] is not None and e["astro_probability"] >= min_confidence
        ]
    events = events[:max_results]
    return {"query": query, "count": len(events), "superevents": events}


@app.mcp.tool()
def get_event_metadata(graceid: str) -> dict[str, Any]:
    """Fetch the full record and source classification for one superevent.

    Args:
        graceid: The superevent id, e.g. ``S230518h``.

    Returns:
        A dict with the raw superevent record, the available file list, the
        parsed source-classification probabilities (BBH / BNS / NSBH / Terrestrial)
        when a p_astro file has been published, and ``pipelines``: each search
        pipeline's best candidate (O4 onwards), so you can see which pipelines
        found the signal and how significant each one rated it.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        record_future = pool.submit(app.client.get_superevent, graceid)
        files_future = pool.submit(app.client.list_files, graceid)
    record, files = record_future.result(), files_future.result()
    p_astro = app.client.get_p_astro(graceid, files)

    return {
        "superevent_id": graceid,
        "record": record,
        "preferred": _preferred_event(record),
        "pipelines": {
            name: {k: event.get(k) for k in ("graceid", "search", "far", "gpstime")}
            for name, event in (record.get("pipeline_preferred_events") or {}).items()
        },
        "classification": p_astro,
        "astro_probability": _astro_probability(p_astro),
        "available_files": sorted(files.keys()),
    }


@app.mcp.tool()
def download_event_files(
    graceid: str,
    filenames: list[str] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Download GraceDB artifacts (skymaps, p_astro, PE results, logs) for an event.

    These are the files GraceDB actually hosts. This is NOT raw strain data
    (use ``download_strain_data`` for that).

    Args:
        graceid: The superevent id, e.g. ``S230518h``.
        filenames: Specific filenames to download. If omitted, downloads every
            file attached to the superevent.

    Returns:
        A dict mapping each requested filename to the local path it was saved to,
        and ``resources`` mapping it to a ``gracedb://`` URI readable over MCP
        (files over 50 MB are local only).
    """
    app.check_id(graceid)
    available = app.client.list_files(graceid)
    targets = filenames or list(available.keys())

    dest_dir = os.path.join(app.DATA_DIR, graceid)
    os.makedirs(dest_dir, exist_ok=True)

    errors = {name: "not found on this superevent" for name in targets if name not in available}

    def download(name: str) -> str:
        return app.client.download_file(graceid, name, os.path.join(dest_dir, os.path.basename(name)))

    # A few at a time: enough to overlap latency without hammering GraceDB.
    saved, failed = app.map_concurrently(
        download, [n for n in targets if n in available], max_workers=4
    )
    errors.update({name: str(exc) for name, exc in failed.items()})
    uris = resources.register_saved(saved, ctx)
    return {"superevent_id": graceid, "saved": saved, "resources": uris, "errors": errors}


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


@app.mcp.tool()
def get_event_labels(graceid: str) -> dict[str, Any]:
    """GraceDB labels on a superevent, in the order they were applied, with meanings.

    Labels record the event's history: data-quality checks (DQOK, DQV), human
    review (ADVOK, ADVNO = retracted), injections (INJ), and which products are
    ready (SKYMAP_READY, PE_READY).

    Args:
        graceid: The superevent id, e.g. ``S190814bv``.
    """
    app.check_id(graceid)
    labels = sorted(app.client.get_labels(graceid), key=lambda label: label.get("created") or "")
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
