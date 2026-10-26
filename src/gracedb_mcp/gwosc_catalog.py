"""GWOSC event catalogues: source parameters (masses, distance, SNR) for confident events.

GWOSC lists every published event with its estimated parameters and 90% credible
intervals. GraceDB does not link superevents to catalogue names, so superevent ids
are matched to catalogue events by GPS time.
"""

from __future__ import annotations

import time
from typing import Any

from gwosc import api

# Parameters kept in summaries; each has ``<name>_lower``/``_upper`` offsets and ``_unit``.
PARAMETERS = (
    "mass_1_source",
    "mass_2_source",
    "total_mass_source",
    "chirp_mass_source",
    "final_mass_source",
    "luminosity_distance",
    "redshift",
    "chi_eff",
    "network_matched_filter_snr",
    "far",
    "p_astro",
)
UNITS = {
    "mass_1_source": "M_sun",
    "mass_2_source": "M_sun",
    "total_mass_source": "M_sun",
    "chirp_mass_source": "M_sun",
    "final_mass_source": "M_sun",
    "luminosity_distance": "Mpc",
    "far": "1/yr",
}
EVENT_PAGE = "https://gwosc.org/eventapi/html/{catalog}/{name}/v{version}/"
CACHE_SECONDS = 3600

_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, fetch):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fetch()
    _cache[key] = (time.monotonic(), value)
    return value


def catalogs() -> list[str]:
    return sorted({r["catalog.shortName"] for r in _raw_events()})


def _raw_events() -> list[dict[str, Any]]:
    return _cached("allevents", lambda: list(api.fetch_allevents_json()["events"].values()))


def all_events(catalog: str | None = None) -> list[dict[str, Any]]:
    """Newest version of each event (one per common name), newest events first.

    Filtering by ``catalog`` happens first, since an event's newest version may sit
    in a later catalogue than the one asked for.
    """
    latest: dict[str, dict[str, Any]] = {}
    for record in _raw_events():
        if catalog and record["catalog.shortName"] != catalog:
            continue
        name = record["commonName"]
        if name not in latest or record["version"] > latest[name]["version"]:
            latest[name] = record
    return sorted(latest.values(), key=lambda r: r["GPS"], reverse=True)


def event_record(name: str) -> dict[str, Any]:
    """Full GWOSC record for one event; raises ``ValueError`` if the name is unknown."""

    def fetch() -> dict[str, Any]:
        return next(iter(api.fetch_event_json(name)["events"].values()))

    return _cached(f"event:{name}", fetch)


def find_by_gps(gps: float, tolerance: float = 1.0) -> dict[str, Any] | None:
    best = min(all_events(), key=lambda r: abs(r["GPS"] - gps), default=None)
    return best if best and abs(best["GPS"] - gps) <= tolerance else None


def summarise(record: dict[str, Any], intervals: bool = False) -> dict[str, Any]:
    """Compact event summary; with ``intervals``, values become {value, interval_90}."""
    summary: dict[str, Any] = {
        "name": record["commonName"],
        "catalog": record["catalog.shortName"],
        "gps": record["GPS"],
    }
    for param in PARAMETERS:
        value = record.get(param)
        if not intervals or value is None:
            summary[param] = value
            continue
        lower, upper = record.get(f"{param}_lower"), record.get(f"{param}_upper")
        summary[param] = {
            "value": value,
            "interval_90": None if lower is None or upper is None
            else [round(value + lower, 6), round(value + upper, 6)],
        }
    return summary


def event_page(record: dict[str, Any]) -> str:
    return EVENT_PAGE.format(
        catalog=record["catalog.shortName"], name=record["commonName"], version=record["version"]
    )


def strain_detectors(record: dict[str, Any], start: float, end: float) -> list[str]:
    """Detectors with a published event strain file (hdf5) covering [start, end]."""
    return sorted({
        s["detector"]
        for s in record.get("strain", [])
        if s.get("format") == "hdf5" and s["GPSstart"] <= start and end <= s["GPSstart"] + s["duration"]
    })
