"""GWOSC observing runs and data-quality flags."""

from __future__ import annotations

from typing import Any

from gwosc import timeline

from .. import app, gpstime, gwosc_catalog
from .strain import resolve_span


@app.mcp.tool()
def list_observing_runs(gps: float | None = None) -> dict[str, Any]:
    """Observing runs whose data GWOSC has publicly released, oldest first.

    Args:
        gps: Optional GPS time; the answer says which released run covers it, or that
            it falls outside every released run (detectors off, or data not public yet).

    Returns:
        Each run's name, GPS and UTC span, detectors and dataset DOI (cite it when
        using the data).
    """
    runs = gwosc_catalog.runs()
    dois, _ = app.map_concurrently(gwosc_catalog.run_doi, [r["name"] for r in runs])
    result: dict[str, Any] = {
        "runs": [
            {
                **run,
                "utc_start": gpstime.iso(run["gps_start"]),
                "utc_end": gpstime.iso(run["gps_end"]),
                "duration_days": round((run["gps_end"] - run["gps_start"]) / 86400, 1),
                "doi": dois.get(run["name"]),
            }
            for run in runs
        ],
    }
    if gps is not None:
        covering = [r["name"] for r in runs if r["gps_start"] <= gps < r["gps_end"]]
        before = [r["name"] for r in runs if r["gps_end"] <= gps]
        after = [r["name"] for r in runs if r["gps_start"] > gps]
        result["at_gps"] = {
            "gps": gps,
            "utc": gpstime.iso(gps),
            "run": covering[0] if covering else None,
            "previous_run": None if covering or not before else before[-1],
            "next_run": None if covering or not after else after[0],
        }
    return result


def _is_injection_flag(name: str) -> bool:
    return "_NO_" in name and name.endswith("_HW_INJ")


@app.mcp.tool()
def check_data_quality(
    gps_start: float | None = None,
    gps_end: float | None = None,
    event: str | None = None,
    window: float = 64.0,
    detectors: list[str] | None = None,
) -> dict[str, Any]:
    """Data-quality flags for each detector over a span, from GWOSC.

    Flags say whether data passes each search's vetoes: CBC (compact binaries), BURST,
    CW (continuous waves) and STOCH (stochastic background), at categories CAT1 (most
    basic checks) to CAT3 (strictest). ``NO_<search>_HW_INJ`` flags mark time free of
    hardware injections, simulated signals physically added to the detector. CW
    injections are weak, long-lasting signals present for much of each run; they matter
    for continuous-wave searches, not for transients such as compact-binary mergers.

    Args:
        gps_start: Start of the span in GPS seconds. Use together with ``gps_end``.
        gps_end: End of the span in GPS seconds, at most 7 days after ``gps_start``.
        event: Instead of a span, a GraceDB superevent id or catalogue name; checks
            ``window`` seconds centred on it.
        window: Span in seconds around ``event``.
        detectors: Detectors to check. Defaults to H1, L1, V1 and K1.

    Returns:
        Per detector: ``data_coverage`` (fraction of the span with public data),
        ``failed`` (flags vetoing part of the time that has data), hardware
        injections in that time, and every flag's coverage.
    """
    resolved = resolve_span(gps_start, gps_end, event, window, detectors)
    if isinstance(resolved, dict):
        return resolved
    span, detectors = resolved
    runs = gwosc_catalog.runs_overlapping(span.gps_start, span.gps_end)
    result: dict[str, Any] = {
        "gps_window": [span.gps_start, span.gps_end],
        "runs": [r["name"] for r in runs],
    }
    if span.event is not None:
        result.update(event=span.event, t_0=span.t_0)
    if not runs:
        result["note"] = (
            "No released observing run covers this span: the detectors were not "
            "observing, or the data is not public yet. See list_observing_runs."
        )
        return result

    flag_lists, failed_runs = app.map_concurrently(
        gwosc_catalog.run_flags, [r["name"] for r in runs]
    )
    descriptions = {
        flag["name"]: flag["description"]
        for flags in flag_lists.values()
        for flag in flags
        if flag["name"].split("_", 1)[0] in detectors
    }
    segments_by_flag, failed_flags = app.map_concurrently(
        lambda flag: timeline.get_segments(flag, span.start, span.end), descriptions
    )

    report: dict[str, Any] = {}
    for ifo in detectors:
        coverage = {
            flag: span.coverage(segments)[1]
            for flag, segments in segments_by_flag.items()
            if flag.startswith(f"{ifo}_")
        }
        if not coverage:
            continue
        data = coverage.get(f"{ifo}_DATA", 0.0)
        report[ifo] = {
            "data_coverage": data,
            # Category flags are subsets of DATA, so judge them only where data exists.
            "failed": {
                f: c for f, c in coverage.items()
                if c < data and not _is_injection_flag(f) and f != f"{ifo}_DATA"
            },
            "hardware_injections": sorted(
                f.removeprefix(f"{ifo}_NO_").removesuffix("_HW_INJ")
                for f, c in coverage.items()
                if _is_injection_flag(f) and c < data
            ),
            "coverage": coverage,
        }
        if data < 1:
            report[ifo]["note"] = (
                "No public bulk data for part of this span, so flags cannot pass there. "
                "check_data_availability also counts short files published around "
                "catalogue events."
            )
    result.update(
        detectors=report,
        descriptions=descriptions,
        errors={
            **{run: str(exc) for run, exc in failed_runs.items()},
            **{flag: str(exc) for flag, exc in failed_flags.items()},
        },
    )
    missing = [d for d in detectors if d not in report]
    if missing:
        result["not_in_run"] = missing
    return result
