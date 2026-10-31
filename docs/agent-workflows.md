# Agent workflows: plan

Reusable workflows built on the existing tools, so users get useful results without
knowing which tools to chain.

## Packaging

| Form | Where it lives | Reaches | Use for |
| --- | --- | --- | --- |
| **MCP prompt** | `@app.mcp.prompt()` in `prompts.py` | Every MCP client (slash commands in Claude Desktop, VS Code, ...) | Workflows that only need this server's tools |
| **Agent skill** | `skills/<name>/SKILL.md` in this repo | Claude Code, Copilot and other skill-aware agents; listable on mcprush | Workflows that also need the agent's own abilities (running Python, writing files) |

## Workflows

| # | Workflow | Form | Status |
| --- | --- | --- | --- |
| 1 | Alert briefing | Prompt | Done |
| 2 | Event dossier | Prompt | Done |
| 3 | Alert vs. final result | Prompt | Done |
| 4 | Open-data analysis starter | Skill | Planned |
| 5 | Alert audit | Prompt | Planned |

### 1. Alert briefing (`alert_briefing`)

"Anything new in the last N hours?" For astronomers deciding whether to follow up.

- Arguments: `hours` (default 24), `max_far` (default 3.8e-7 Hz, about one false alarm per
  month), `category` (`Production` for real alerts, `MDC` for the simulated alerts GraceDB
  creates about hourly, `Test`).
- Steps: `search_superevents` over the window, then for each event `get_event_labels`
  (flag retractions, data-quality vetoes, injections), `get_event_metadata`
  (classification, astrophysical probability), and the sky-map image via
  `download_event_files` when `SKYMAP_READY`.
- Output: one-line verdict per event, then a short block each: significance, class,
  review status, sky map. Say plainly when nothing matched.
- Guardrails: low-latency values change; no public strain exists yet for recent alerts,
  so do not call the strain tools. MDC and Test events must be called simulations.
  Labels without a known meaning (e.g. `HIGH_PROFILE`) are reported, not guessed.

### 2. Event dossier (`event_dossier`)

Everything about one event, for scientists, students or the public.

- Arguments: `event` (GraceDB id like `S190814bv` or catalogue name like `GW190814`),
  `audience` (`scientist`, `student` or `public`).
- Steps: resolve both identities (`get_catalog_event` maps either way), then
  `get_event_metadata`, `get_event_labels`, sky map, `check_data_availability`.
- Output sections: summary, significance and review, source (alert classification and
  final parameters with 90% intervals), sky localisation, public data, links and the
  GWOSC citation.
- Guardrails: never mix up alert-time and final values; say when an event is not in a
  catalogue yet (or was retracted).

### 3. Alert vs. final result (`alert_vs_catalog`)

How did the low-latency classification hold up once the final analysis was published?
Combines GraceDB and the GWOSC catalogues, which few tools do.

- Arguments: `event` for one event, or `catalog` (e.g. `GWTC-3-confident`) plus
  `max_events` (default 15) for a batch.
- Steps: `get_catalog_event` for final masses with 90% intervals, `get_event_metadata`
  on the matching GraceDB id for the alert-time `p_astro`.
- Comparison rule: a component counts as a neutron star below 3 solar masses (the
  boundary `p_astro` uses); flag events whose 90% interval straddles it as ambiguous.
- Output: table of event, alert class (top `p_astro` entry), final masses, final class,
  agreement; then a short discussion of the disagreements.
- Guardrails: events before O3 have no GraceDB superevent; skip and count them.

### 4. Open-data analysis starter (skill)

Find events, check data, download strain, then write and run a notebook that whitens
the data and shows the chirp in a Q-transform. Needs code execution, hence a skill.

### 5. Alert audit (`alert_audit`)

For a period: how many alerts, how many retracted and why (labels), time to retraction,
which detectors were involved.

## Testing

- Each prompt renders through a real MCP client (`prompts/list`, `prompts/get`).
- Guard test: every tool a prompt names exists, so renaming a tool cannot silently break
  a prompt.
- Manual run of each prompt in an MCP client against live services before release.

## Future: more data sources

No plugin framework until two sources share a shape: each new source is a client module
plus a `tools/<source>.py`, which the current layout already supports.

| # | Step | Adds | Size |
| --- | --- | --- | --- |
| A | Simulated alerts in the briefing (`category`) | Try workflows between observing runs | Done |
| B | More GraceDB and GWOSC data | Per-pipeline candidate events (gstlal, PyCBC, MBTA, cWB); any GWOSC data-quality flag, not just data availability; observing-run information; GraceDB's own `gwtc` catalogue endpoint | Small to medium |
| C | Sky-map analysis | 90% credible area, distance, "is this telescope field or galaxy inside the map?" | Medium (needs `ligo.skymap` or `healpy`) |
| D | GCN (NASA's General Coordinates Network) | Telescope follow-up reports (GCN Circulars) per event; neutrino (IceCube) and gamma-ray (Fermi, Swift) alerts, making this a multi-messenger server | Large |

Recommended order: C for usefulness to astronomers, then D for breadth.
