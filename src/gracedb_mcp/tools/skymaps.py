"""Sky-map analysis: localisation area, distance, and whether a position is covered."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

from .. import app, resources, skymap

SKYMAP_SUFFIX = ".multiorder.fits"
# Cone queries walk every pixel, so keep requests bounded.
MAX_POSITIONS = 100
MAX_FIELDS = 20
DISTANCE_MAX_LEVEL = 0.99


class SkyPosition(BaseModel):
    ra: float = Field(ge=0, lt=360, description="Right ascension in degrees (J2000).")
    dec: float = Field(ge=-90, le=90, description="Declination in degrees (J2000).")
    name: str | None = Field(None, max_length=100, description="Label, e.g. a galaxy name.")


class TelescopeField(SkyPosition):
    radius: float = Field(gt=0, le=30, description="Field radius in degrees.")


def _available_skymaps(graceid: str) -> list[dict[str, Any]]:
    """Multi-order sky maps on the superevent, newest upload first."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        files_future = pool.submit(app.client.list_files, graceid)
        logs_future = pool.submit(app.client.get_logs, graceid)
    names = [n for n in files_future.result() if n.endswith(SKYMAP_SUFFIX)]
    uploaded: dict[str, str] = {}
    for entry in logs_future.result():
        name = entry.get("filename") or ""
        if name in names and entry.get("created"):
            uploaded[name] = max(uploaded.get(name, ""), entry["created"])
    return sorted(
        ({"filename": n, "uploaded": uploaded.get(n)} for n in names),
        key=lambda m: m["uploaded"] or "",
        reverse=True,
    )


def _round(value: float, digits: int = 4) -> float:
    return float(f"{value:.{digits}g}")


@app.mcp.tool()
def analyze_skymap(
    graceid: str,
    filename: str | None = None,
    positions: list[SkyPosition] | None = None,
    fields: list[TelescopeField] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Analyse a gravitational-wave sky map: how big the localisation is, how far away,
    and how likely given sky positions or telescope fields are to contain the source.

    Args:
        graceid: Superevent id, e.g. ``S230518h``.
        filename: Sky map to use, e.g. ``bayestar.multiorder.fits``. Defaults to the
            most recently uploaded one; later maps (Bilby, publication maps) are
            usually more accurate than the rapid BAYESTAR map.
        positions: Up to 100 sky positions (e.g. candidate host galaxies or optical
            transients) to check.
        fields: Up to 20 circular telescope fields; returns the probability inside each.

    Returns:
        50% and 90% credible areas (deg^2), the overall distance estimate (Mpc), the
        most probable position, and the other available sky maps. For each position:
        the credible level (smallest credible region containing it; below 0.9 means
        inside the 90% region), probability density per deg^2 and, inside the 99%
        region, the distance along that line of sight (compare it with a galaxy's
        distance before calling it a likely host). For each field: the enclosed
        probability.
    """
    app.check_id(graceid)
    if positions and len(positions) > MAX_POSITIONS:
        return {"error": f"at most {MAX_POSITIONS} positions per call"}
    if fields and len(fields) > MAX_FIELDS:
        return {"error": f"at most {MAX_FIELDS} fields per call"}

    available = _available_skymaps(graceid)
    if not available:
        return {"superevent_id": graceid, "error": "no multi-order sky map on this superevent"}
    chosen = filename or available[0]["filename"]
    if chosen not in {m["filename"] for m in available}:
        return {
            "error": f"{chosen!r} is not a multi-order sky map on {graceid}",
            "available_skymaps": available,
        }

    dest_dir = os.path.join(app.DATA_DIR, graceid)
    os.makedirs(dest_dir, exist_ok=True)
    path = app.client.download_file(graceid, chosen, os.path.join(dest_dir, chosen))
    with open(path, "rb") as handle:
        sky = skymap.SkyMap.from_bytes(handle.read())

    header = sky.header
    peak_ra, peak_dec = sky.peak()
    result: dict[str, Any] = {
        "superevent_id": graceid,
        "skymap": chosen,
        "uploaded": next(m["uploaded"] for m in available if m["filename"] == chosen),
        "created_by": header.get("CREATOR"),
        "detectors": header["INSTRUME"].split(",") if header.get("INSTRUME") else None,
        "area_50_deg2": _round(sky.credible_area_deg2(0.5)),
        "area_90_deg2": _round(sky.credible_area_deg2(0.9)),
        "distance_mpc": (
            {"mean": _round(header["DISTMEAN"]), "std": _round(header["DISTSTD"])}
            if "DISTMEAN" in header and "DISTSTD" in header
            else None
        ),
        "most_probable_position": {"ra": round(peak_ra, 3), "dec": round(peak_dec, 3)},
        "available_skymaps": available,
        "resource": resources.register_saved({chosen: path}, ctx).get(chosen),
    }
    if positions:
        result["positions"] = [
            {**p.model_dump(exclude_none=True), **_position_report(sky, p)} for p in positions
        ]
    if fields:
        result["fields"] = [
            {**f.model_dump(exclude_none=True),
             "probability": _round(sky.prob_within(f.ra, f.dec, f.radius))}
            for f in fields
        ]
    return result


def _position_report(sky: skymap.SkyMap, position: SkyPosition) -> dict[str, Any]:
    report = sky.at(position.ra, position.dec)
    if "error" in report:
        return report
    distance = report["distance_mpc"]
    # Far outside the localisation the distance fit is unconstrained and misleading.
    if report["credible_level"] > DISTANCE_MAX_LEVEL:
        distance = None
    return {
        "credible_level": _round(report["credible_level"]),
        "inside_90": report["credible_level"] <= 0.9,
        "searched_area_deg2": _round(report["searched_area_deg2"]),
        "prob_density_per_deg2": _round(report["prob_density_per_deg2"]),
        "distance_mpc": None if distance is None
        else {"mean": _round(distance["mean"]), "std": _round(distance["std"])},
    }
