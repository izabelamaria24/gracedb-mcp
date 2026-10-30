"""GWOSC catalogue tools: published events with final source parameters."""

from __future__ import annotations

from typing import Any

from .. import app, gwosc_catalog


def _within(value: float | None, low: float | None, high: float | None) -> bool:
    if low is None and high is None:
        return True
    if value is None:
        return False
    return (low is None or value >= low) and (high is None or value <= high)


@app.mcp.tool()
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


@app.mcp.tool()
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
    app.check_id(name)
    if not name.upper().startswith("GW"):
        t_0 = app.client.get_superevent(name).get("t_0")
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
