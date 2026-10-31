"""MCP prompts: reusable workflows that chain the tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS

from . import app
from .tools.alerts import GRACEDB_TIME

_AUDIENCES = {
    "scientist": "Write for a gravitational-wave scientist: precise, with units and uncertainties.",
    "student": "Write for a physics student: explain each term (FAR, p_astro, chirp mass) briefly.",
    "public": "Write for the general public: no jargon, use analogies, keep it short.",
}

_CATEGORIES = {
    "Production": (
        "If nothing matches, say so plainly. The detectors may be between observing runs; "
        "offer to list the most recent alerts instead (search without a start time, "
        "max_results=5)."
    ),
    "MDC": (
        "These are simulated signals from GraceDB's mock data challenge, created about "
        "hourly to exercise the alert pipeline. Say clearly at the top that none of them "
        "is a real detection."
    ),
    "Test": "These are test events, not real detections; say so at the top.",
}


def _argument_error(message: str) -> MCPError:
    # Other exceptions reach the client as a bare "Internal server error".
    return MCPError(code=INVALID_PARAMS, message=message)


def _check_event(event: str) -> None:
    if not app.SAFE_ID.match(event):
        raise _argument_error(f"invalid event name: {event!r}")


@app.mcp.prompt(title="Gravitational-wave alert briefing")
def alert_briefing(hours: int = 24, max_far: float = 3.8e-7, category: str = "Production") -> str:
    """Brief me on new gravitational-wave alerts from the last few hours.

    Use category "MDC" to try the workflow on simulated alerts, which GraceDB creates
    about hourly even between observing runs.
    """
    if category not in _CATEGORIES:
        raise _argument_error(f"category must be one of {sorted(_CATEGORIES)}")
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    return f"""Brief me on gravitational-wave alerts from the last {hours} hours.

1. Call `search_superevents` with start="{start.strftime(GRACEDB_TIME)}", \
end="{end.strftime(GRACEDB_TIME)}", max_far={max_far:g} (false alarms about \
{max_far * 86400 * 30:.1g} times per month) and category="{category}".
2. {_CATEGORIES[category]}
3. For each superevent:
   - `get_event_labels`: flag retractions (ADVNO), data-quality vetoes (DQV) and \
injections (INJ) first, because they change everything else. If a label has no meaning \
listed, mention it without guessing what it means.
   - `get_event_metadata`: classification (BNS, NSBH, BBH, Terrestrial) and \
astrophysical probability.
   - `analyze_skymap`: 90% credible area and distance. If a sky-map image such as \
`bayestar.png` is in `available_files`, also download it with `download_event_files` and \
look at it through the returned resource URI.
4. Report a one-line verdict per event (id, most likely class, significance, retracted or \
not), then a short paragraph each: significance in plain terms, classification, human \
review status, and localisation (90% area in square degrees, distance, and where on the \
sky from the image, if you have it).

These are low-latency results that may still change. Public strain data does not exist \
yet for recent alerts, so do not call the strain tools."""


@app.mcp.prompt(title="Gravitational-wave event dossier")
def event_dossier(event: str, audience: str = "scientist") -> str:
    """Everything about one event: significance, review, source, sky map and data."""
    _check_event(event)
    if audience not in _AUDIENCES:
        raise _argument_error(f"audience must be one of {sorted(_AUDIENCES)}")
    return f"""Write a dossier on the gravitational-wave event {event}.

1. Resolve both names: `get_catalog_event` accepts a GraceDB id (S...) or a catalogue \
name (GW...) and returns the final parameters plus `gracedb_id`. If it reports the event \
is not in a catalogue, say so (it may be unpublished or retracted) and continue with \
GraceDB only.
2. With the GraceDB superevent id (starts with S): `get_event_metadata` (alert-time \
classification, significance, files) and `get_event_labels` (review history, retraction). \
Events before O3 (2019) have no superevent, only an id starting with G; skip this step \
for them.
3. Sky map: `analyze_skymap` on the superevent gives the 90% credible area, distance and \
most probable position (it picks the newest map; say which one). If a sky-map image \
(e.g. `bayestar.png`) is available, download it with `download_event_files` and view it \
through the returned resource URI.
4. `check_data_availability` with event="{event}": which detectors have public strain. \
Then `check_data_quality` with event="{event}": mention any vetoed detector or hardware \
injection, ignoring continuous-wave (CW) injections, which run for most of each run.

Structure the dossier as: Summary; Significance and review (including which search \
pipelines found it, from `pipelines` in the metadata); Source (keep alert-time \
classification and final catalogue parameters clearly separate, quoting 90% intervals); \
Sky localisation; Public data; Links (GWOSC event page). End with: "Data: Gravitational \
Wave Open Science Center (gwosc.org), CC BY 4.0."

{_AUDIENCES[audience]}"""


@app.mcp.prompt(title="Alert classification vs. final result")
def alert_vs_catalog(
    event: str | None = None, catalog: str | None = None, max_events: int = 15
) -> str:
    """Compare low-latency alert classifications with final published parameters."""
    if (event is None) == (catalog is None):
        raise _argument_error("give exactly one of event or catalog")
    if event is not None:
        _check_event(event)
        scope = f"the event {event}"
        listing = f"Use {event} only."
    else:
        scope = f"events in {catalog}"
        listing = (
            f"Call `search_catalog_events` with catalog=\"{catalog}\" and "
            f"max_results={max_events}."
        )
    return f"""How well did the low-latency alert classification predict the final result \
for {scope}?

1. {listing}
2. For each event, `get_catalog_event` gives final masses (mass_1_source, mass_2_source) \
with 90% intervals and the `gracedb_id`. Only superevent ids (starting with S, from O3 \
onwards) can be compared; skip events whose id is missing or starts with G, and count them.
3. For each remaining event, `get_event_metadata` on the GraceDB id gives the alert-time \
`classification`; its largest entry is the alert class.
4. Final class: treat a component below 3 solar masses as a neutron star (the boundary \
the alert classifier uses): both below is BNS, one below is NSBH, neither is BBH. If a \
90% interval straddles 3 solar masses, call the final class ambiguous. A component \
between about 2.2 and 3 solar masses could be a heavy neutron star or a light black hole \
(GW190814's 2.6 solar-mass companion is the classic case): say so instead of presenting \
the label as certain.

Report a table: event, GraceDB id, alert class (with probability), final masses with \
90% intervals, final class, agree (yes / no / ambiguous). Then discuss each disagreement \
in a sentence or two, and give the agreement rate over the events compared."""
